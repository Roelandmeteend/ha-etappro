"""Modbus TCP client voor de ETAPpro laadpaal.

Geïmplementeerd met rauwe TCP-sockets om versieconflicten met pymodbus te
vermijden (Home Assistant levert zijn eigen pymodbus mee voor de ingebouwde
modbus-integratie).

Ontwerpkeuzes:

* **Blokgewijs lezen.** Aaneengesloten registers worden in één FC03-request
  opgehaald. Dat brengt een volledige poll terug van 27 losse round-trips naar
  een handvol, wat de kans op storingen op het kleine embedded apparaat sterk
  verkleint.

* **Afbreken bij transportfouten.** Een Modbus *exception response* (bijv.
  "illegal data address") laat de socket schoon achter — dat register slaan we
  over en we gaan verder. Een transportfout — timeout, reset, of een
  transaction-ID die niet klopt — betekent dat er ongelezen bytes in de buffer
  staan. Doorgaan op zo'n socket laat élke volgende read de restanten van het
  vorige antwoord als header interpreteren, waardoor de hele poll omvalt.
  Daarom wordt de poll dan afgebroken en start de volgende met een verse
  verbinding.

* **Eén verbinding per operatie, onder een lock.** De ETAPpro accepteert maar
  één Modbus-verbinding tegelijk. Het lock voorkomt dat een keepalive-write en
  een lopende poll elkaar in de weg zitten.

* **Onthouden wat niet bestaat.** Registers die de firmware niet ondersteunt
  (bijv. de energieteller op 374) worden na de eerste weigering overgeslagen in
  plaats van elke poll opnieuw geprobeerd.

Registeradressen: https://github.com/EV-Chargeking/etap-modbus
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

# Datatypes
_F32 = "f32"
_F64 = "f64"
_I16 = "i16"
_U16 = "u16"
_STR = "str"

# Aantal registers per datatype (_STR heeft een eigen lengte per veld)
_REGISTER_SPAN = {_F32: 2, _F64: 4, _I16: 1, _U16: 1}


class ETAPproModbusError(Exception):
    """Basisfout voor alle Modbus-communicatieproblemen."""


class ETAPproTransportError(ETAPproModbusError):
    """Socket is onbruikbaar geworden (timeout, reset, framing uit sync).

    Er kunnen ongelezen bytes in de buffer staan; verder lezen op deze
    verbinding levert gegarandeerd rommel op. De poll moet afgebroken worden.
    """


class ETAPproRegisterError(ETAPproModbusError):
    """De lader antwoordde met een Modbus exception response.

    Het protocol is netjes gevolgd, de socket is schoon: dit register bestaat
    niet of is niet leesbaar, maar de rest van de poll kan gewoon doorgaan.
    """


@dataclass(frozen=True)
class _Field:
    """Eén uitleesbare waarde binnen een registerblok."""

    key: str
    address: int
    kind: str
    length: int = 1            # aantal registers (alleen voor _STR)
    decimals: int | None = None
    auto_scale_ma: bool = False  # zie _decode()


@dataclass(frozen=True)
class _Block:
    """Een aaneengesloten reeks registers die in één request gelezen wordt."""

    start: int
    count: int
    fields: tuple[_Field, ...]


# Registerblokken. De grenzen volgen de gedocumenteerde, aaneengesloten
# groepen; gaten met onbekende registers worden meegelezen maar genegeerd.
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
    """Modbus TCP client voor de ETAPpro laadpaal.

    Wordt aangeroepen via hass.async_add_executor_job(), zodat de HA event loop
    nooit geblokkeerd wordt. Alle socketgebruik loopt door een lock omdat de
    lader maar één verbinding tegelijk aankan.
    """

    def __init__(self, host: str, port: int) -> None:
        # Poort eruit halen als die per ongeluk in het host-veld is ingevuld
        if ":" in host:
            host = host.split(":")[0]
        self._host = host
        self._port = port
        self._transaction_id = 0
        self._lock = threading.Lock()
        # Registers die de firmware niet ondersteunt; niet opnieuw proberen.
        self._unsupported: set[str] = set()
        # Blokken die per register gelezen moeten worden omdat het blok als
        # geheel geweigerd wordt.
        self._split_blocks: set[int] = set()

    # -- Verbindingsbeheer ---------------------------------------

    def _open_connection(self) -> socket.socket:
        """Open en retourneer een verse TCP-verbinding naar de lader."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(_TIMEOUT)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.connect((self._host, self._port))
            return sock
        except OSError as err:
            raise ETAPproTransportError(
                f"Kan niet verbinden met {self._host}:{self._port} — {err}"
            ) from err

    def disconnect(self) -> None:
        """No-op: verbindingen worden na elke operatie gesloten."""

    # -- Rauwe Modbus TCP framing --------------------------------

    def _next_transaction_id(self) -> int:
        self._transaction_id = (self._transaction_id + 1) % 0xFFFF
        return self._transaction_id

    def _send_request(self, sock: socket.socket, pdu: bytes) -> bytes:
        """Verstuur een PDU in een MBAP-header en geef de response-PDU terug.

        Elke fout hier is een transportfout: de aanroeper moet de verbinding
        weggooien in plaats van er nog een request overheen te sturen.
        """
        tid = self._next_transaction_id()
        mbap = struct.pack(">HHHB", tid, 0, len(pdu) + 1, _UNIT_ID)
        try:
            sock.sendall(mbap + pdu)
            header = self._recv_exactly(sock, 6)
            resp_tid, _, resp_len = struct.unpack(">HHH", header)
            if resp_tid != tid:
                # De byte-stream loopt niet meer synchroon: we lezen het staartje
                # van een eerder (te laat gearriveerd) antwoord. Niet te redden
                # op deze verbinding.
                raise ETAPproTransportError(
                    f"Transaction ID komt niet overeen: verstuurd {tid}, ontvangen {resp_tid}"
                )
            body = self._recv_exactly(sock, resp_len)
            return body[1:]  # unit-id byte eraf
        except OSError as err:
            raise ETAPproTransportError(f"Communicatiefout: {err}") from err

    def _recv_exactly(self, sock: socket.socket, n: int) -> bytes:
        """Lees exact n bytes van de socket."""
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise ETAPproTransportError("Verbinding gesloten door de lader")
            data += chunk
        return data

    # -- Modbus functiecodes -------------------------------------

    def _read_holding_registers(
        self, sock: socket.socket, address: int, count: int
    ) -> list[int]:
        """FC03 — Read Holding Registers."""
        pdu = struct.pack(">BHH", _FC_READ_HOLDING, address, count)
        response = self._send_request(sock, pdu)
        if response[0] & 0x80:
            raise ETAPproRegisterError(
                f"Modbus exception op FC03, adres {address}: code {response[1]}"
            )
        byte_count = response[1]
        if byte_count != count * 2:
            # Antwoord past niet bij de vraag; behandel als framing-probleem.
            raise ETAPproTransportError(
                f"Onverwachte byte count op adres {address}: {byte_count}, verwacht {count * 2}"
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
                f"Modbus exception op FC06, adres {address}: code {response[1]}"
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
                f"Modbus exception op FC16, adres {address}: code {response[1]}"
            )

    # -- Decodering ----------------------------------------------

    def _decode(self, field: _Field, regs: list[int], base: int) -> Any:
        """Decodeer één veld uit een gelezen registerblok."""
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
            raise ValueError(f"Onbekend datatype: {field.kind}")

        if field.auto_scale_ma and value > 100:
            # Sommige firmwareversies geven dit register in milliampère terug,
            # ondanks dat de documentatie ampère vermeldt. Een laadpaal doet
            # realistisch 6–63 A, dus alles boven 100 is mA.
            value = value / 1000

        return round(value, field.decimals) if field.decimals is not None else value

    # -- Publieke methodes ---------------------------------------

    def test_connection(self) -> bool:
        """Controleer de verbinding door het beschikbaarheidsregister te lezen."""
        with self._lock:
            sock = self._open_connection()
            try:
                self._read_holding_registers(sock, 1200, 1)
                return True
            finally:
                sock.close()

    def read_all(self) -> dict[str, Any]:
        """Lees alle registers over één verbinding, blokgewijs.

        Registers die de firmware niet ondersteunt komen terug als None. Bij een
        transportfout wordt ETAPproTransportError opgegooid en is de hele poll
        mislukt — doorgaan op een ontregelde socket levert alleen maar rommel op.
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
                        # Het blok als geheel wordt geweigerd; probeer voortaan
                        # elk register apart, dan verliezen we alleen wat écht
                        # ontbreekt.
                        _LOGGER.debug(
                            "Blok %d-%d geweigerd (%s); schakel over op losse reads",
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
        """Lees velden één voor één; onthoud welke de firmware niet kent."""
        for field in fields:
            span = field.length if field.kind == _STR else _REGISTER_SPAN[field.kind]
            try:
                regs = self._read_holding_registers(sock, field.address, span)
            except ETAPproRegisterError as err:
                _LOGGER.debug(
                    "Register '%s' (adres %d) wordt niet ondersteund: %s",
                    field.key, field.address, err,
                )
                self._unsupported.add(field.key)
                continue
            data[field.key] = self._decode(field, regs, field.address)

    def set_current_setpoint(self, ampere: float) -> None:
        """Schrijf een nieuw laadstroom-setpoint naar register 1210.

        0 A    = laden pauzeren
        >= 6 A = laden op de opgegeven stroom
        """
        with self._lock:
            sock = self._open_connection()
            try:
                hi, lo = struct.unpack(">HH", struct.pack(">f", float(ampere)))
                self._write_multiple_registers(sock, 1210, [hi, lo])
            finally:
                sock.close()

    def set_phases(self, phases: int) -> None:
        """Schrijf het aantal laadfasen naar register 1215 (1 of 3)."""
        if phases not in (1, 3):
            raise ValueError(f"Ongeldig aantal fasen: {phases}. Moet 1 of 3 zijn.")
        with self._lock:
            sock = self._open_connection()
            try:
                self._write_single_register(sock, 1215, phases)
            finally:
                sock.close()
