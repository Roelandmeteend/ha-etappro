"""Registermodel en toegang tot de ETAPpro laadpaal.

Sinds Home Assistant 2026.9 deelt de core `modbus`-integratie één verbinding
tussen alle integraties die hetzelfde apparaat aanspreken. Wij vragen daar een
*unit* op en gebruiken het `Component`-model van `modbus-connection` om de
registers te beschrijven; de framing, de verbindingsopbouw en het herstel na een
weggevallen link zitten in de library.

Dat is precies wat deze laadpaal nodig heeft: de ETAPpro accepteert maar één
Modbus-verbinding tegelijk, dus een tweede consument (de YAML-modbus, een
energiemanager) botste vroeger met ons. Op de gedeelde verbinding gaan hun
requests netjes achter elkaar in de rij.

Ontwerpkeuzes die van het apparaat zelf komen — de rest doet de library:

* **Blokgewijs lezen.** `register_ranges` beschrijft de aaneengesloten reeksen
  die de lader bedient. Velden binnen één bereik gaan in één FC03-request; een
  read steekt nooit over een grens heen. Dat brengt een volledige poll terug van
  27 losse round-trips naar een handvol.

* **Versmallen na een weigering.** Weigert de lader een blok met een
  "illegal data address", dan lezen we de velden uit dat blok voortaan apart;
  weigert hij vervolgens één veld, dan slaan we dat veld blijvend over. Zo
  verliezen we alleen wat de firmware écht niet kent (bijv. de energieteller op
  374) in plaats van de hele poll.

Registeradressen: https://github.com/EV-Chargeking/etap-modbus
"""
from __future__ import annotations

import logging
from typing import Any

from modbus_connection import (
    IllegalDataAddressError,
    IllegalFunctionError,
    ModbusExceptionError,
    ModbusTcpParams,
    ModbusUnit,
)
from modbus_connection.model import Component, float32, float64, integer, string

_LOGGER = logging.getLogger(__name__)

# De lader antwoordt als slave 1.
UNIT_ID = 1

# Adresbereiken die de lader bedient. De grenzen volgen de gedocumenteerde,
# aaneengesloten groepen; gaten met onbekende registers worden meegelezen maar
# hebben geen veld en worden dus genegeerd.
_REGISTER_RANGES: tuple[tuple[int, int], ...] = (
    (306, 311),
    (320, 327),
    (338, 345),
    (374, 377),
    (1100, 1109),
    (1200, 1215),
    (1236, 1240),
)

_REG_AVAILABILITY = 1200


class ETAPproRegisters(Component):
    """De registers van de ETAPpro, als getypeerde attributen."""

    register_ranges = _REGISTER_RANGES

    # ── Energie-meter ────────────────────────────────────────
    voltage_l1 = float32(306, unit="V")
    voltage_l2 = float32(308, unit="V")
    voltage_l3 = float32(310, unit="V")
    current_l1 = float32(320, unit="A")
    current_l2 = float32(322, unit="A")
    current_l3 = float32(324, unit="A")
    current_sum = float32(326, unit="A")
    power_l1 = float32(338, unit="W")
    power_l2 = float32(340, unit="W")
    power_l3 = float32(342, unit="W")
    power_sum = float32(344, unit="W")
    energy_wh = float64(374, unit="Wh")

    # ── Status ───────────────────────────────────────────────
    max_current_hw = float32(1100, unit="A")
    temp_board = float32(1102, unit="°C")
    temp_ev_plug = float32(1106, unit="°C")
    temp_grid_plug = float32(1108, unit="°C")
    availability = integer(1200, signed=True)
    mode = string(1201, 5)
    applied_current = float32(1206, unit="A")
    setpoint_current = float32(1210, unit="A", writable=True)
    setpoint_active = integer(1214, signed=False)
    phases = integer(1215, signed=False, writable=True)
    started_by = string(1236, 5)


ALL_KEYS: tuple[str, ...] = tuple(ETAPproRegisters.declared_fields)

# Afronding per veld, zoals de entiteiten die verwachten. De library rekent
# ongeschaalde floats exact door, dus dit is van ons.
_DECIMALS: dict[str, int] = {
    "voltage_l1": 1, "voltage_l2": 1, "voltage_l3": 1,
    "current_l1": 2, "current_l2": 2, "current_l3": 2, "current_sum": 2,
    "power_l1": 0, "power_l2": 0, "power_l3": 0, "power_sum": 0,
    "energy_wh": 0,
    "max_current_hw": 1, "temp_board": 1, "temp_ev_plug": 1, "temp_grid_plug": 1,
    "applied_current": 1, "setpoint_current": 1,
}

# Bovengrens voor het aantal versmalstappen binnen één poll: hoogstens één
# splitsing per bereik plus één afgevallen veld.
_MAX_NARROWING_STEPS = len(_REGISTER_RANGES) + len(ALL_KEYS)

# Weigeringen die blijvend zijn: het register bestaat niet of de functiecode
# wordt niet ondersteund. Andere codes (device busy, device failure) zijn
# tijdelijk en laten we de poll gewoon laten mislukken.
_PERMANENT_REFUSALS = (IllegalDataAddressError, IllegalFunctionError)


def connection_params(host: str, port: int) -> ModbusTcpParams:
    """Verbindingsgegevens voor de lader.

    Een poort die per ongeluk in het host-veld is meegetypt wordt eruit gehaald;
    bestaande config entries kunnen die er nog in hebben staan.
    """
    if ":" in host:
        host = host.split(":")[0]
    return ModbusTcpParams(host=host, port=port)


async def async_test_connection(unit: ModbusUnit) -> None:
    """Controleer de verbinding door het beschikbaarheidsregister te lezen.

    Gooit een `ModbusError` als de lader niet bereikbaar is of niet antwoordt.
    """
    await unit.read_holding_registers(_REG_AVAILABILITY, 1)


class ETAPproDevice:
    """Leest en schrijft de ETAPpro over een unit van de gedeelde verbinding."""

    def __init__(self, unit: ModbusUnit) -> None:
        self._unit = unit
        # Wat we lezen: de bereiken (versmald na een geweigerd blok) en de
        # velden (versmald nadat één veld geweigerd werd).
        self._ranges = _REGISTER_RANGES
        self._keys = ALL_KEYS
        self._registers = self._build()

    def _build(self) -> ETAPproRegisters:
        """Bouw het component voor de huidige bereiken en velden."""
        registers = ETAPproRegisters(self._unit)
        registers.register_ranges = self._ranges
        registers.restrict_fields(self._keys)
        return registers

    # -- Lezen ---------------------------------------------------

    async def async_read_all(self) -> dict[str, Any]:
        """Lees alle registers; velden die de firmware niet kent geven None.

        Gooit een `ModbusError` als de poll niet af te maken is.
        """
        for _ in range(_MAX_NARROWING_STEPS):
            try:
                await self._registers.async_update(notify=False)
            except _PERMANENT_REFUSALS as err:
                if not self._narrow(err):
                    raise
                self._registers = self._build()
                continue
            return self._values()

        # Onbereikbaar zolang elke versmalstap iets weghaalt, maar een stille
        # oneindige lus is erger dan een duidelijke fout.
        raise ModbusExceptionError(
            None, "ETAPpro blijft registerblokken weigeren; poll opgegeven"
        )

    def _narrow(self, err: ModbusExceptionError) -> bool:
        """Versmal het leesplan na een geweigerd blok.

        Geeft True terug als er iets veranderd is, False als de weigering niet
        aan een veld van ons toe te schrijven is — dan hoort de fout omhoog.
        """
        block = err.block
        if block is None or block.space != "holding":
            return False

        low = block.address
        high = block.address + block.count - 1
        refused = [key for key in self._keys if self._overlaps(key, low, high)]
        if not refused:
            return False

        if len(refused) > 1:
            # Het blok als geheel wordt geweigerd; lees deze velden voortaan
            # apart, dan verliezen we alleen wat écht ontbreekt.
            containing = next(
                (r for r in self._ranges if r[0] <= low and high <= r[1]), None
            )
            if containing is None:
                return False
            _LOGGER.debug(
                "ETAPpro: blok %d-%d geweigerd (%s); schakel over op losse reads",
                low, high, err,
            )
            self._ranges = self._split(containing)
            return True

        key = refused[0]
        _LOGGER.debug(
            "ETAPpro: register '%s' wordt niet ondersteund (%s); voortaan overslaan",
            key, err,
        )
        self._keys = tuple(k for k in self._keys if k != key)
        return True

    def _overlaps(self, key: str, low: int, high: int) -> bool:
        """Of het veld `key` binnen het adresbereik low-high valt."""
        field = ETAPproRegisters.declared_fields[key]
        return field.address <= high and field.address + field.count - 1 >= low

    def _split(
        self, containing: tuple[int, int]
    ) -> tuple[tuple[int, int], ...]:
        """Vervang een bereik door één bereik per veld dat erin ligt.

        Elk veld dat we nog lezen krijgt zijn eigen bereik, niet alleen de
        velden uit het geweigerde blok: een bereik kan in meerdere blokken
        uiteenvallen, en de velden in de andere blokken moeten leesbaar blijven.
        """
        kept = tuple(r for r in self._ranges if r != containing)
        per_field = tuple(
            (field.address, field.address + field.count - 1)
            for field in (ETAPproRegisters.declared_fields[k] for k in self._keys)
            if containing[0] <= field.address <= containing[1]
        )
        return tuple(sorted(kept + per_field))

    def _values(self) -> dict[str, Any]:
        """De gelezen waarden, afgerond en opgeschoond zoals de entiteiten ze verwachten."""
        data: dict[str, Any] = {}
        for key in ALL_KEYS:
            value = getattr(self._registers, key)
            if isinstance(value, str):
                value = value.strip()
            elif key == "max_current_hw" and value is not None and value > 100:
                # Sommige firmwareversies geven dit register in milliampère terug,
                # ondanks dat de documentatie ampère vermeldt. Een laadpaal doet
                # realistisch 6–63 A, dus alles boven 100 is mA.
                value = value / 1000
            if value is not None and (decimals := _DECIMALS.get(key)) is not None:
                value = round(value, decimals)
            data[key] = value
        return data

    # -- Schrijven -----------------------------------------------

    async def async_set_current_setpoint(self, ampere: float) -> None:
        """Schrijf een nieuw laadstroom-setpoint naar register 1210.

        0 A    = laden pauzeren
        >= 6 A = laden op de opgegeven stroom
        """
        await self._registers.write("setpoint_current", float(ampere))

    async def async_set_phases(self, phases: int) -> None:
        """Schrijf het aantal laadfasen naar register 1215 (1 of 3)."""
        if phases not in (1, 3):
            raise ValueError(f"Ongeldig aantal fasen: {phases}. Moet 1 of 3 zijn.")
        await self._registers.write("phases", phases)
