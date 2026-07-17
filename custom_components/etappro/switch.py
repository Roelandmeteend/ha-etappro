"""Switch platform voor de ETAPpro — laden starten/pauzeren en HA-sturing."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MIN_CURRENT_A
from .coordinator import ETAPproCoordinator
from .modbus_client import ETAPproModbusError

_LOGGER = logging.getLogger(__name__)

_CHARGING_MODES = {"C", "C1", "C2"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register switch entities."""
    coordinator: ETAPproCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        ETAPproChargingSwitch(coordinator, entry),
        ETAPproHAControl(coordinator, entry),
    ])


class ETAPproChargingSwitch(CoordinatorEntity[ETAPproCoordinator], SwitchEntity):
    """Schakelaar om laden te starten of te pauzeren.

    AAN  — herstelt het laatste setpoint (minimaal 6 A) naar register 1210.
    UIT  — schrijft 0 A naar register 1210, waardoor laden pauzeert.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "charging"
    _attr_icon = "mdi:ev-plug-type2"

    def __init__(self, coordinator: ETAPproCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_charging"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=coordinator.charger_name,
            manufacturer="EVchargeking",
            model="ETAPpro",
        )
        self._last_setpoint: float = MIN_CURRENT_A

    @property
    def is_on(self) -> bool | None:
        """True als de lader actief laadt (IEC 61851 modus C)."""
        if self.coordinator.data is None:
            return None
        mode = (self.coordinator.data.get("mode") or "").strip().upper()
        return mode in _CHARGING_MODES

    @property
    def available(self) -> bool:
        """Alleen beschikbaar als een auto aangesloten is (modus B of C)."""
        if not self.coordinator.last_update_success or self.coordinator.data is None:
            return False
        mode = (self.coordinator.data.get("mode") or "").strip().upper()
        return mode[:1] in ("B", "C")

    @property
    def extra_state_attributes(self) -> dict:
        return {"resume_setpoint_a": self._last_setpoint}

    async def async_turn_on(self, **kwargs) -> None:
        """Hervat laden met het vorige setpoint (minimaal 6 A)."""
        setpoint = max(self._last_setpoint, MIN_CURRENT_A)
        _LOGGER.debug("ETAPpro: laden hervatten op %.1f A", setpoint)
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.client.set_current_setpoint, setpoint
            )
        except ETAPproModbusError as err:
            _LOGGER.error("ETAPpro: laden hervatten mislukt: %s", err)
            return
        self.coordinator.set_desired_setpoint(setpoint)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Pauzeer laden door 0 A naar register 1210 te schrijven."""
        if self.coordinator.data:
            current = self.coordinator.data.get("setpoint_current")
            if current and current > 0:
                self._last_setpoint = current
        _LOGGER.debug("ETAPpro: laden pauzeren (setpoint was %.1f A)", self._last_setpoint)
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.client.set_current_setpoint, 0
            )
        except ETAPproModbusError as err:
            _LOGGER.error("ETAPpro: laden pauzeren mislukt: %s", err)
            return
        self.coordinator.set_desired_setpoint(0)
        await self.coordinator.async_request_refresh()


class ETAPproHAControl(CoordinatorEntity[ETAPproCoordinator], SwitchEntity):
    """Schakelaar om HA-sturing (Modbus keepalive) aan of uit te zetten.

    AAN  — HA schrijft bij elke poll het setpoint opnieuw naar de lader.
           Gebruik dit voor slimme laadautomatisering vanuit HA.
    UIT  — HA laat de lader met rust. De laadpaal beheert zichzelf,
           inclusief laden via laadpas (RFID).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "ha_control"
    _attr_icon = "mdi:home-lightning-bolt"

    def __init__(self, coordinator: ETAPproCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_ha_control"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=coordinator.charger_name,
            manufacturer="EVchargeking",
            model="ETAPpro",
        )

    @property
    def is_on(self) -> bool:
        """AAN als HA actief het setpoint beheert (keepalive actief)."""
        return self.coordinator.desired_setpoint is not None

    async def async_turn_on(self, **kwargs) -> None:
        """Activeer HA-sturing: neem het huidige setpoint over als startwaarde."""
        current = None
        if self.coordinator.data:
            current = self.coordinator.data.get("setpoint_current")
        setpoint = float(current) if current and current >= MIN_CURRENT_A else MIN_CURRENT_A
        self.coordinator.set_desired_setpoint(setpoint)
        _LOGGER.debug("ETAPpro: HA-sturing ingeschakeld op %.1f A", setpoint)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Deactiveer HA-sturing: lader beheert zichzelf (RFID werkt weer)."""
        self.coordinator.set_desired_setpoint(None)
        _LOGGER.debug("ETAPpro: HA-sturing uitgeschakeld")
        await self.coordinator.async_request_refresh()
