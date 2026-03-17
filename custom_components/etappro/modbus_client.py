"""Modbus TCP client for the ETAPpro EV charger.

Implemented using raw TCP sockets to avoid pymodbus version incompatibilities.

Design choice: a new TCP connection is opened for every poll and closed
immediately after. This is the most reliable approach because the ETAPpro
closes idle connections after a short timeout, which would leave a persistent
socket in a broken state that is hard to detect without sending data.

Register addresses: https://github.com/EV-Chargeking/etap-modbus
"""
from __future__ import annotations

import logging
import socket
import struct
from typing import Any

_LOGGER = logging.getLogger(__name__)

_UNIT_ID = 1
_FC_READ_HOLDING = 0x03
_FC_WRITE_SINGLE = 0x06
_FC_WRITE_MULTIPLE = 0x10
_TIMEOUT = 5


class ETAPproModbusError(Exception):
    """Raised on any Modbus communication failure."""


class ETAPproModbusClient:
    """Raw Modbus TCP client for the ETAPpro EV charger.

    Called via hass.async_add_executor_job() so the HA event loop
    is never blocked.
    """

    def __init__(self, host: str, port: int) -> None:
        # Strip port if user accidentally included it in the host field
        if ":" in host:
            host = host.split(":")[0]
        self._host = host
        self._port = port
        self._transaction_id = 0

    # -- Connection management -----------------------------------

    def _open_connection(self) -> socket.socket:
        """Open and return a fresh TCP connection to the charger."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(_TIMEOUT)
            sock.connect((self._host, self._port))
            return sock
        except OSError as err:
            raise ETAPproModbusError(
                f"Cannot connect to {self._host}:{self._port} — {err}"
            ) from err

    def disconnect(self) -> None:
        """No-op: connections are closed after each operation."""

    # -- Raw Modbus TCP framing ----------------------------------

    def _next_transaction_id(self) -> int:
        self._transaction_id = (self._transaction_id + 1) % 0xFFFF
        return self._transaction_id

    def _send_request(self, sock: socket.socket, pdu: bytes) -> bytes:
        """Wrap PDU in a Modbus TCP MBAP header, send, and return the response PDU."""
        tid = self._next_transaction_id()
        mbap = struct.pack(">HHHB", tid, 0, len(pdu) + 1, _UNIT_ID)
        try:
            sock.sendall(mbap + pdu)
            header = self._recv_exactly(sock, 6)
            resp_tid, _, resp_len = struct.unpack(">HHH", header)
            if resp_tid != tid:
                raise ETAPproModbusError(
                    f"Transaction ID mismatch: sent {tid}, got {resp_tid}"
                )
            body = self._recv_exactly(sock, resp_len)
            return body[1:]  # strip unit id byte
        except OSError as err:
            raise ETAPproModbusError(f"Communication error: {err}") from err

    def _recv_exactly(self, sock: socket.socket, n: int) -> bytes:
        """Read exactly n bytes from the socket."""
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise ETAPproModbusError("Connection closed by charger")
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
            raise ETAPproModbusError(
                f"Modbus exception on FC03 at address {address}: code {response[1]}"
            )
        byte_count = response[1]
        return [
            struct.unpack(">H", response[2 + i * 2: 4 + i * 2])[0]
            for i in range(byte_count // 2)
        ]

    def _write_single_register(
        self, sock: socket.socket, address: int, value: int
    ) -> None:
        """FC06 — Write Single Register."""
        pdu = struct.pack(">BHH", _FC_WRITE_SINGLE, address, value)
        response = self._send_request(sock, pdu)
        if response[0] & 0x80:
            raise ETAPproModbusError(
                f"Modbus exception on FC06 at address {address}: code {response[1]}"
            )

    def _write_multiple_registers(
        self, sock: socket.socket, address: int, values: list[int]
    ) -> None:
        """FC16 — Write Multiple Registers."""
        count = len(values)
        byte_count = count * 2
        pdu = struct.pack(">BHHB", _FC_WRITE_MULTIPLE, address, count, byte_count)
        pdu += struct.pack(f">{count}H", *values)
        response = self._send_request(sock, pdu)
        if response[0] & 0x80:
            raise ETAPproModbusError(
                f"Modbus exception on FC16 at address {address}: code {response[1]}"
            )

    # -- Type decoders (take an open socket as first argument) ---

    def _float32(self, sock: socket.socket, address: int) -> float:
        regs = self._read_holding_registers(sock, address, 2)
        return struct.unpack(">f", struct.pack(">HH", regs[0], regs[1]))[0]

    def _float64(self, sock: socket.socket, address: int) -> float:
        regs = self._read_holding_registers(sock, address, 4)
        return struct.unpack(">d", struct.pack(">HHHH", *regs))[0]

    def _int16(self, sock: socket.socket, address: int) -> int:
        regs = self._read_holding_registers(sock, address, 1)
        return struct.unpack(">h", struct.pack(">H", regs[0]))[0]

    def _uint16(self, sock: socket.socket, address: int) -> int:
        return self._read_holding_registers(sock, address, 1)[0]

    def _string(self, sock: socket.socket, address: int, count: int) -> str:
        regs = self._read_holding_registers(sock, address, count)
        raw = b"".join(struct.pack(">H", r) for r in regs)
        return raw.decode("ascii", errors="ignore").rstrip("\x00").strip()

    def _safe(self, key: str, fn, *args) -> Any:
        """Call fn(*args); return None if the register is unavailable (exception code 2)."""
        try:
            return fn(*args)
        except ETAPproModbusError as err:
            _LOGGER.debug("Register unavailable for '%s': %s", key, err)
            return None

    # -- Public methods ------------------------------------------

    def test_connection(self) -> bool:
        """Verify connectivity by reading the availability register.

        Raises ETAPproModbusError on failure.
        """
        sock = self._open_connection()
        try:
            self._uint16(sock, 1200)
            return True
        except Exception as err:
            raise ETAPproModbusError(str(err)) from err
        finally:
            sock.close()

    def read_all(self) -> dict[str, Any]:
        """Open a connection, read all registers, close the connection.

        Registers not supported by the current firmware are returned as None.
        """
        sock = self._open_connection()
        try:
            def f32(addr):   return round(self._float32(sock, addr), 1)
            def f32_2(addr): return round(self._float32(sock, addr), 2)
            def f32_0(addr): return round(self._float32(sock, addr), 0)

            return {
                # Voltage
                "voltage_l1":       self._safe("voltage_l1",       f32,   306),
                "voltage_l2":       self._safe("voltage_l2",       f32,   308),
                "voltage_l3":       self._safe("voltage_l3",       f32,   310),
                # Current
                "current_l1":       self._safe("current_l1",       f32_2, 320),
                "current_l2":       self._safe("current_l2",       f32_2, 322),
                "current_l3":       self._safe("current_l3",       f32_2, 324),
                "current_sum":      self._safe("current_sum",      f32_2, 326),
                # Power
                "power_l1":         self._safe("power_l1",         f32_0, 338),
                "power_l2":         self._safe("power_l2",         f32_0, 340),
                "power_l3":         self._safe("power_l3",         f32_0, 342),
                "power_sum":        self._safe("power_sum",        f32_0, 344),
                # Energy counter (not available in all firmware versions)
                "energy_wh":        self._safe("energy_wh",
                                        lambda a: round(self._float64(sock, a), 0), 374),
                # Temperatures
                "temp_board":       self._safe("temp_board",       f32,   1102),
                "temp_ev_plug":     self._safe("temp_ev_plug",     f32,   1106),
                "temp_grid_plug":   self._safe("temp_grid_plug",   f32,   1108),
                # Status
                "max_current_hw":   self._safe("max_current_hw",   f32,   1100),
                "availability":     self._safe("availability",     self._int16,  sock, 1200),
                "mode":             self._safe("mode",             self._string, sock, 1201, 5),
                "applied_current":  self._safe("applied_current",  f32,   1206),
                "setpoint_current": self._safe("setpoint_current", f32,   1210),
                "setpoint_active":  self._safe("setpoint_active",  self._uint16, sock, 1214),
                "phases":           self._safe("phases",           self._uint16, sock, 1215),
                "started_by":       self._safe("started_by",       self._string, sock, 1236, 5),
            }
        finally:
            sock.close()

    def set_current_setpoint(self, ampere: float) -> None:
        """Write a new current setpoint to register 1210.

        0 A  = pause charging
        >= 6 A = charge at the given current
        """
        sock = self._open_connection()
        try:
            raw = struct.pack(">f", float(ampere))
            hi, lo = struct.unpack(">HH", raw)
            self._write_multiple_registers(sock, 1210, [hi, lo])
        finally:
            sock.close()

    def set_phases(self, phases: int) -> None:
        """Write the number of charging phases to register 1215 (1 or 3)."""
        if phases not in (1, 3):
            raise ValueError(f"Invalid phase count: {phases}. Must be 1 or 3.")
        sock = self._open_connection()
        try:
            self._write_single_register(sock, 1215, phases)
        finally:
            sock.close()
