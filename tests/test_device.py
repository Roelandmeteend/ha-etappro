"""Tests voor het registermodel van de ETAPpro.

Het zwaartepunt ligt op wat bij de migratie naar `modbus-connection` gelijk
moest blijven: welke blokken er gelezen worden, hoe waarden gedecodeerd worden,
en hoe de integratie zich terugtrekt op registers die de firmware niet kent.
"""
from __future__ import annotations

import struct

import pytest
from modbus_connection import IllegalDataAddressError, ServerDeviceBusyError
from modbus_connection.mock import MockModbusConnection

# De blokken die één volledige poll oplevert: (startadres, aantal registers).
# Dit is de reden dat `register_ranges` bestaat — zonder die bereiken zou de
# planner andere blokken kiezen dan de lader bedient.
EXPECTED_BLOCKS = [
    ("holding", 306, 6),
    ("holding", 320, 8),
    ("holding", 338, 8),
    ("holding", 374, 4),
    ("holding", 1100, 10),
    ("holding", 1200, 16),
    ("holding", 1236, 5),
]


def f32(value: float) -> list[int]:
    """Een float32 als twee registers."""
    return list(struct.unpack(">HH", struct.pack(">f", value)))


def f64(value: float) -> list[int]:
    """Een float64 als vier registers."""
    return list(struct.unpack(">HHHH", struct.pack(">d", value)))


def text(value: str, length: int) -> list[int]:
    """Een ASCII-string als `length` registers, met nullen opgevuld."""
    raw = value.encode("ascii").ljust(length * 2, b"\x00")
    return list(struct.unpack(f">{length}H", raw))


def charger_registers(*, mode: str = "C2", max_current: float = 32.0) -> dict[int, int]:
    """Een laadpaal die aan het laden is op 16 A."""
    registers: dict[int, int] = {}

    def put(address: int, words: list[int]) -> None:
        for offset, word in enumerate(words):
            registers[address + offset] = word

    put(306, f32(230.1))
    put(308, f32(231.2))
    put(310, f32(229.3))
    put(320, f32(15.51))
    put(322, f32(15.52))
    put(324, f32(15.53))
    put(326, f32(46.56))
    put(338, f32(3567.4))
    put(340, f32(3568.5))
    put(342, f32(3569.6))
    put(344, f32(10705.5))
    put(374, f64(123456.7))
    put(1100, f32(max_current))
    put(1102, f32(41.5))
    put(1106, f32(38.24))
    put(1108, f32(36.75))
    registers[1200] = 1
    put(1201, text(mode, 5))
    put(1206, f32(16.0))
    put(1210, f32(16.0))
    registers[1214] = 1
    registers[1215] = 3
    put(1236, text("RFID", 5))
    return registers


@pytest.fixture(name="unit")
def unit_fixture(device_module):
    """Een mock-unit met een ladende ETAPpro erachter."""
    unit = MockModbusConnection().for_unit(device_module.UNIT_ID)
    unit.holding.update(charger_registers())
    return unit


@pytest.mark.asyncio
async def test_read_all_decodes_every_field(device_module, unit):
    """Een volledige poll levert alle velden correct gedecodeerd op."""
    data = await device_module.ETAPproDevice(unit).async_read_all()

    assert data == {
        "voltage_l1": 230.1, "voltage_l2": 231.2, "voltage_l3": 229.3,
        "current_l1": 15.51, "current_l2": 15.52, "current_l3": 15.53,
        "current_sum": 46.56,
        "power_l1": 3567.0, "power_l2": 3568.0, "power_l3": 3570.0,
        "power_sum": 10706.0,
        "energy_wh": 123457.0,
        "max_current_hw": 32.0,
        "temp_board": 41.5, "temp_ev_plug": 38.2, "temp_grid_plug": 36.8,
        "availability": 1,
        "mode": "C2",
        "applied_current": 16.0,
        "setpoint_current": 16.0,
        "setpoint_active": 1,
        "phases": 3,
        "started_by": "RFID",
    }
    assert set(data) == set(device_module.ALL_KEYS)


@pytest.mark.asyncio
async def test_read_all_uses_the_documented_blocks(device_module, unit):
    """Aaneengesloten registers gaan in één request, precies zoals gedocumenteerd."""
    await device_module.ETAPproDevice(unit).async_read_all()

    assert [(e.register_type, e.address, e.count) for e in unit.read_events] == (
        EXPECTED_BLOCKS
    )


@pytest.mark.asyncio
async def test_milliampere_firmware_is_scaled_back_to_ampere(device_module, unit):
    """Sommige firmware geeft register 1100 in mA; alles boven 100 is dus mA."""
    unit.holding.update(charger_registers(max_current=32000.0))

    data = await device_module.ETAPproDevice(unit).async_read_all()

    assert data["max_current_hw"] == 32.0


@pytest.mark.asyncio
async def test_padded_strings_are_stripped(device_module, unit):
    """De modus wordt op het eerste teken vergeleken, dus opvulling moet eraf."""
    unit.holding.update(charger_registers(mode="B1  "))

    data = await device_module.ETAPproDevice(unit).async_read_all()

    assert data["mode"] == "B1"


@pytest.mark.asyncio
async def test_unsupported_register_is_dropped_and_not_read_again(device_module, unit):
    """Een firmware zonder energieteller kost ons dat ene veld, niet de poll."""
    unit.fail_read(374, IllegalDataAddressError())
    device = device_module.ETAPproDevice(unit)

    data = await device.async_read_all()

    assert data["energy_wh"] is None
    assert data["power_sum"] == 10706.0  # de rest komt gewoon binnen

    # Blok 374 wordt niet opnieuw geprobeerd bij een volgende poll.
    unit.read_events.clear()
    await device.async_read_all()
    assert 374 not in [event.address for event in unit.read_events]


@pytest.mark.asyncio
async def test_refused_block_falls_back_to_reading_its_fields_apart(
    device_module, unit
):
    """Weigert de lader een heel blok, dan lezen we de velden erin los.

    Alleen het veld dat de firmware echt niet kent valt af; de buren in
    hetzelfde blok blijven gewoon werken.
    """
    # Blok 1100-1109 weigeren, maar het losse register 1106 wel bedienen.
    unit.fail_read(1100, IllegalDataAddressError())
    unit.fail_read(1102, IllegalDataAddressError())
    unit.fail_read(1108, IllegalDataAddressError())
    device = device_module.ETAPproDevice(unit)

    data = await device.async_read_all()

    assert data["temp_ev_plug"] == 38.2
    assert data["max_current_hw"] is None
    assert data["temp_board"] is None
    assert data["temp_grid_plug"] is None

    # Stabiele eindtoestand: alleen 1106 blijft over uit dat bereik.
    unit.read_events.clear()
    await device.async_read_all()
    reads = [(event.address, event.count) for event in unit.read_events]
    assert (1106, 2) in reads
    assert not [r for r in reads if r[0] in (1100, 1102, 1108)]


@pytest.mark.asyncio
async def test_a_busy_device_fails_the_poll_instead_of_dropping_fields(
    device_module, unit
):
    """"Device busy" is tijdelijk — daar mag geen veld blijvend om sneuvelen."""
    unit.fail_read(374, ServerDeviceBusyError())
    device = device_module.ETAPproDevice(unit)

    with pytest.raises(ServerDeviceBusyError):
        await device.async_read_all()

    # Niets afgevallen: zodra de lader weer wil, lezen we 374 gewoon opnieuw.
    unit.fail_read(374, None)
    assert (await device.async_read_all())["energy_wh"] == 123457.0


@pytest.mark.asyncio
async def test_setpoint_is_written_as_one_float32_request(device_module, unit):
    """Register 1210 is een float32 en gaat dus in één FC16-write."""
    writes = []
    unit.on_write(writes.append)

    await device_module.ETAPproDevice(unit).async_set_current_setpoint(12.5)

    assert [(w.address, w.values, w.function_code) for w in writes] == [
        (1210, f32(12.5), 0x10)
    ]


@pytest.mark.asyncio
async def test_phases_are_written_to_a_single_register(device_module, unit):
    """Register 1215 is één register en gaat dus in een FC06-write."""
    writes = []
    unit.on_write(writes.append)

    await device_module.ETAPproDevice(unit).async_set_phases(1)

    assert [(w.address, w.values, w.function_code) for w in writes] == [
        (1215, [1], 0x06)
    ]


@pytest.mark.asyncio
async def test_two_phases_is_refused(device_module, unit):
    """De lader kent alleen 1- of 3-fasig laden."""
    with pytest.raises(ValueError):
        await device_module.ETAPproDevice(unit).async_set_phases(2)


@pytest.mark.asyncio
async def test_connection_test_reads_the_availability_register(device_module, unit):
    """De config flow controleert de verbinding met één klein request."""
    await device_module.async_test_connection(unit)

    assert [(e.address, e.count) for e in unit.read_events] == [(1200, 1)]


def test_connection_params_strips_a_port_typed_into_the_host(device_module):
    """Een bestaande entry kan "192.168.1.5:502" in het host-veld hebben staan."""
    params = device_module.connection_params("192.168.1.5:502", 502)

    assert params.host == "192.168.1.5"
    assert params.port == 502


@pytest.mark.asyncio
async def test_splitting_a_range_keeps_all_its_other_fields_readable(
    device_module, unit
):
    """Na een splitsing krijgt elk veld uit het bereik zijn eigen read.

    Alleen het geweigerde register valt af; de vijf buren in blok 1200-1215
    blijven gewoon gelezen worden.
    """
    unit.fail_read(1201, IllegalDataAddressError())
    device = device_module.ETAPproDevice(unit)

    data = await device.async_read_all()

    assert data["mode"] is None
    assert data["availability"] == 1
    assert data["applied_current"] == 16.0
    assert data["setpoint_current"] == 16.0
    assert data["setpoint_active"] == 1
    assert data["phases"] == 3
