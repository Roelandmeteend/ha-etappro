"""Modbus TCP client for the ETAPpro EV charger.

Implemented with raw TCP sockets to avoid version conflicts with pymodbus
(Home Assistant ships its own copy for the built-in modbus integration).

Design choices:

* **Block reads.** Contiguous registers are fetched in a single FC03 request.
  That takes a full poll from 27 separate round-trips down to a handful, which
  markedly reduces the chance of upsetting the small embedded device.

* **Abort on transport errors.** A Modbus *exception response* (e.g. "illegal
  data address") leaves the socket clean — skip that register and carry on. A
  transport error — timeout, reset, or a transaction ID that does not match —
  means unread bytes are still in the buffer. Continuing on such a socket makes
  every subsequent read interpret the tail of the previous response as its
  header, which brings down the whole poll. So the poll is aborted and the next
  one starts on a fresh connection.

* **One connection per operation, behind a lock.** The ETAPpro accepts only one
  Modbus connection at a time; the lock keeps a keepalive write from cutting
  into a running poll.

* **Remember what does not exist.** Registers the firmware does not support
  (e.g. the energy counter at 374) are skipped after the first refusal instead
  of being retried on every poll.

Register addresses: https://github.com/EV-Chargeking/etap-modbus
"""
from __future__ import annotations

import logging
import socket
import struct
import threading
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)

_UNIT_ID = 1
_FC_READ_HOLDING = 0x03
_FC_WRITE_SINGLE = 0x06
_FC_WRITE_MULTIPLE = 0x10
_TIMEOUT = 5

# Data types
_F32 = "f32"
_F64 = "f64"
_I16 = "i16"
_U16 = "u16"
_STR = "str"

# Register count per data type (_STR carries its own length per field)
_REGISTER_SPAN = {_F32: 2, _F64: 4, _I16: 1, _U16: 1}


class ETAPproModbusError(Exception):
    """Base error for all Modbus communication problems."""


class ETAPproTransportError(ETAPproModbusError):
    """The socket has become unusable (timeout, reset, framing out of sync).

    Unread bytes may remain in the buffer; reading further on this connection
    is guaranteed to return garbage. The poll must be aborted.
    """


class ETAPproRegisterError(ETAPproModbusError):
    """The charger replied with a Modbus exception response.

    The protocol was followed correctly and the socket is clean: this register
    does not exist or cannot be read, but the rest of the poll can continue.
    """


@dataclass(frozen=True)
class _Field:
    """A single readable value within a register block."""

    key: str
    address: int
    kind: str
    length: int = 1              # register count (only used for _STR)
    decimals: int | None = None
    auto_scale_ma: bool = False  # see _decode()


@dataclass(frozen=True)
class _Block:
    """A contiguous range of registers fetched in one request."""

    start: int
    count: int
    fields: tuple[_Field, ...]


# Register blocks. The boundaries follow the documented contiguous groups; gaps
# holding unknown registers are read along with the rest and then ignored.
_BLOCKS: tuple[_Block, ...] = (
    _Block(306, 6, (
        _Field("voltage_l1", 306, _F32, decimals=1),
        _Field("voltage_l2", 308, _F32, decimals=1),
        _Field("voltage_l3", 310, _F32, decimals=1),
    )),
    _Block(320, 8, (
        _Field("current_l1", 320, _F32, decimals=2),
        _Field("current_l2", 322, _F32, decimals=2),
        _Field("current_l3", 324, _F32, decimals=2),
        _Field("current_sum", 326, _F32, decimals=2),
    )),
    _Block(338, 8, (
        _Field("power_l1", 338, _F32, decimals=0),
        _Field("power_l2", 340, _F32, decimals=0),
        _Field("power_l3", 342, _F32, decimals=0),
        _Field("power_sum", 344, _F32, decimals=0),
    )),
    _Block(374, 4, (
        _Field("energy_wh", 374, _F64, decimals=0),
    )),
    _Block(1100, 10, (
        _Field("max_current_hw", 1100, _F32, decimals=1, auto_scale_ma=True),
        _Field("temp_board", 1102, _F32, decimals=1),
        _Field("temp_ev_plug", 1106, _F32, decimals=1),
        _Field("temp_grid_plug", 1108, _F32, decimals=1),
    )),
    _Block(1200, 16, (
        _Field("availability", 1200, _I16),
        _Field("mode", 1201, _STR, length=5),
        _Field("applied_current", 1206, _F32, decimals=1),
        _Field("setpoint_current", 1210, _F32, decimals=1),
        _Field("setpoint_active", 1214, _U16),
        _Field("phases", 1215, _U16),
    )),
    _Block(1236, 5, (
        _Field("started_by", 1236, _STR, length=5),
    )),
)

_ALL_KEYS = tuple(f.key for b in _BLOCKS for f in b.fields)


class ETAPproModbusClient:
    """Modbus TCP client for the ETAPpro EV charger.

    Called via hass.async_add_executor_job() so the HA event loop is never
    blocked. All socket use runs behind a lock because the charger can only
    handle one connection at a time.
    """

    def __init__(self, host: str, port: int) -> None:
        # Strip the port if it was accidentally typed into the host field
        if ":" in host:
            host = host.split(":")[0]
        self._host = host
        self._port = port
        self._transaction_id = 0
        self._lock = threading.Lock()
        # Registers the firmware does not support; do not retry these.
        self._unsupported: set[str] = set()
        # Blocks that must be read register by register because the block as a
        # whole is refused.
        self._split_blocks: set[int] = set()

    # -- Connection management -----------------------------------

    def _open_connection(self) -> socket.socket:
        """Open and return a fresh TCP connection to the charger."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(_TIMEOUT)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.connect((self._host, self._port))
            return sock
        except OSError as err:
            raise ETAPproTransportError(
                f"Cannot connect to {self._host}:{self._port} — {err}"
            ) from err

    def disconnect(self) -> None:
        """No-op: connections are closed after every operation."""

    # -- Raw Modbus TCP framing ----------------------------------

    def _next_transaction_id(self) -> int:
        self._transaction_id = (self._transaction_id + 1) % 0xFFFF
        return self._transaction_id

    def _send_request(self, sock: socket.socket, pdu: bytes) -> bytes:
        """Send a PDU wrapped in an MBAP header and return the response PDU.

        Every failure here is a transport error: the caller must discard the
        connection rather than push another request over it.
        """
        tid = self._next_transaction_id()
        mbap = struct.pack(">HHHB", tid, 0, len(pdu) + 1, _UNIT_ID)
        try:
            sock.sendall(mbap + pdu)
            header = self._recv_exactly(sock, 6)
            resp_tid, _, resp_len = struct.unpack(">HHH", header)
            if resp_tid != tid:
                # The byte stream is no longer in sync: we are reading the tail
                # of an earlier (late-arriving) response. Not recoverable on
                # this connection.
                raise ETAPproTransportError(
                    f"Transaction ID mismatch: sent {tid}, received {resp_tid}"
                )
            body = self._recv_exactly(sock, resp_len)
            return body[1:]  # strip the unit id byte
        except OSError as err:
            raise ETAPproTransportError(f"Communication error: {err}") from err

    def _recv_exactly(self, sock: socket.socket, n: int) -> bytes:
        """Read exactly n bytes from the socket."""
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise ETAPproTransportError("Connection closed by the charger")
            data += chunk
        return data

    # -- Modbus function codes -----------------------------------

    def _read_holding_registers(
        self, sock: socket.socket, address: int, count: int
    ) -> list[int]:
        """FC03 — Read Holding Registers."""
        pdu = struct.pack(">BHH", _FC_READ_HOLDING, address, count)
        response = self._send_request(sock, pdu)
        if response[0] & 0x80:
            raise ETAPproRegisterError(
                f"Modbus exception on FC03, address {address}: code {response[1]}"
            )
        byte_count = response[1]
        if byte_count != count * 2:
            # The response does not match the request; treat as a framing problem.
            raise ETAPproTransportError(
                f"Unexpected byte count at address {address}: "
                f"{byte_count}, expected {count * 2}"
            )
        return [
            struct.unpack(">H", response[2 + i * 2: 4 + i * 2])[0]
            for i in range(count)
        ]

    def _write_single_register(
        self, sock: socket.socket, address: int, value: int
    ) -> None:
        """FC06 — Write Single Register."""
        pdu = struct.pack(">BHH", _FC_WRITE_SINGLE, address, value)
        response = self._send_request(sock, pdu)
        if response[0] & 0x80:
            raise ETAPproRegisterError(
                f"Modbus exception on FC06, address {address}: code {response[1]}"
            )

    def _write_multiple_registers(
        self, sock: socket.socket, address: int, values: list[int]
    ) -> None:
        """FC16 — Write Multiple Registers."""
        count = len(values)
        pdu = struct.pack(">BHHB", _FC_WRITE_MULTIPLE, address, count, count * 2)
        pdu += struct.pack(f">{count}H", *values)
        response = self._send_request(sock, pdu)
        if response[0] & 0x80:
            raise ETAPproRegisterError(
                f"Modbus exception on FC16, address {address}: code {response[1]}"
            )

    # -- Decoding ------------------------------------------------

    def _decode(self, field: _Field, regs: list[int], base: int) -> Any:
        """Decode a single field out of a register block that was read."""
        off = field.address - base

        if field.kind == _I16:
            return struct.unpack(">h", struct.pack(">H", regs[off]))[0]
        if field.kind == _U16:
            return regs[off]
        if field.kind == _STR:
            raw = b"".join(struct.pack(">H", r) for r in regs[off:off + field.length])
            return raw.decode("ascii", errors="ignore").rstrip("\x00").strip()
        if field.kind == _F32:
            value = struct.unpack(">f", struct.pack(">HH", regs[off], regs[off + 1]))[0]
        elif field.kind == _F64:
            value = struct.unpack(">d", struct.pack(">HHHH", *regs[off:off + 4]))[0]
        else:
            raise ValueError(f"Unknown data type: {field.kind}")

        if field.auto_scale_ma and value > 100:
            # Some firmware versions return this register in milliamperes even
            # though the documentation states amperes. A charger realistically
            # does 6-63 A, so anything above 100 is mA.
            value = value / 1000

        return round(value, field.decimals) if field.decimals is not None else value

    # -- Public methods ------------------------------------------

    def test_connection(self) -> bool:
        """Verify the connection by reading the availability register."""
        with self._lock:
            sock = self._open_connection()
            try:
                self._read_holding_registers(sock, 1200, 1)
                return True
            finally:
                sock.close()

    def read_all(self) -> dict[str, Any]:
        """Read every register over a single connection, block by block.

        Registers the firmware does not support come back as None. On a
        transport error ETAPproTransportError is raised and the whole poll has
        failed — continuing on a desynchronised socket only yields garbage.
        """
        data: dict[str, Any] = dict.fromkeys(_ALL_KEYS)

        with self._lock:
            sock = self._open_connection()
            try:
                for block in _BLOCKS:
                    fields = tuple(
                        f for f in block.fields if f.key not in self._unsupported
                    )
                    if not fields:
                        continue
                    if block.start in self._split_blocks:
                        self._read_fields_individually(sock, fields, data)
                        continue
                    try:
                        regs = self._read_holding_registers(sock, block.start, block.count)
                    except ETAPproRegisterError as err:
                        # The block as a whole is refused; from now on read each
                        # register separately so we only lose what is genuinely
                        # missing.
                        _LOGGER.debug(
                            "Block %d-%d refused (%s); switching to individual reads",
                            block.start, block.start + block.count - 1, err,
                        )
                        self._split_blocks.add(block.start)
                        self._read_fields_individually(sock, fields, data)
                        continue
                    for field in fields:
                        data[field.key] = self._decode(field, regs, block.start)
            finally:
                sock.close()

        return data

    def _read_fields_individually(
        self, sock: socket.socket, fields: tuple[_Field, ...], data: dict[str, Any]
    ) -> None:
        """Read fields one by one; remember which ones the firmware rejects."""
        for field in fields:
            span = field.length if field.kind == _STR else _REGISTER_SPAN[field.kind]
            try:
                regs = self._read_holding_registers(sock, field.address, span)
            except ETAPproRegisterError as err:
                _LOGGER.debug(
                    "Register '%s' (address %d) is not supported: %s",
                    field.key, field.address, err,
                )
                self._unsupported.add(field.key)
                continue
            data[field.key] = self._decode(field, regs, field.address)

    def set_current_setpoint(self, ampere: float) -> None:
        """Write a new charging current setpoint to register 1210.

        0 A    = pause charging
        >= 6 A = charge at the given current
        """
        with self._lock:
            sock = self._open_connection()
            try:
                hi, lo = struct.unpack(">HH", struct.pack(">f", float(ampere)))
                self._write_multiple_registers(sock, 1210, [hi, lo])
            finally:
                sock.close()

    def set_phases(self, phases: int) -> None:
        """Write the number of charging phases to register 1215 (1 or 3)."""
        if phases not in (1, 3):
            raise ValueError(f"Invalid phase count: {phases}. Must be 1 or 3.")
        with self._lock:
            sock = self._open_connection()
            try:
                self._write_single_register(sock, 1215, phases)
            finally:
                sock.close()
