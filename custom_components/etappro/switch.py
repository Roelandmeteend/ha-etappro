"""Switch platform for the ETAPpro — start and pause charging.

Confirmed by EVchargeking support:
  - Pause: write 0 to register 1210 (Current Setpoint)
  - Resume: write a value >= 6 A to register 1210
"""
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
    """Register the charging switch."""
    coordinator: ETAPproCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ETAPproChargingSwitch(coordinator, entry)])


class ETAPproChargingSwitch(CoordinatorEntity[ETAPproCoordinator], SwitchEntity):
    """Switch to start or pause charging.

    ON  — restores the last known setpoint (minimum 6 A) to register 1210.
    OFF — writes 0 A to register 1210, which pauses charging.
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
        """Return True when the charger is actively charging (IEC 61851 mode C)."""
        if self.coordinator.data is None:
            return None
        mode = (self.coordinator.data.get("mode") or "").strip().upper()
        return mode in _CHARGING_MODES

    @property
    def available(self) -> bool:
        """Only available when a car is connected (mode B or C)."""
        if not self.coordinator.last_update_success or self.coordinator.data is None:
            return False
        mode = (self.coordinator.data.get("mode") or "").strip().upper()
        return mode[:1] in ("B", "C")

    @property
    def extra_state_attributes(self) -> dict:
        """Expose the cached resume setpoint for diagnostics."""
        return {"resume_setpoint_a": self._last_setpoint}

    async def async_turn_on(self, **kwargs) -> None:
        """Resume charging by restoring the previous setpoint (>= 6 A)."""
        setpoint = max(self._last_setpoint, MIN_CURRENT_A)
        _LOGGER.debug("ETAPpro: resuming charging at %.1f A", setpoint)
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.client.set_current_setpoint, setpoint
            )
        except ETAPproModbusError as err:
            _LOGGER.error("ETAPpro: failed to resume charging: %s", err)
            return
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Pause charging by writing 0 A to register 1210."""
        if self.coordinator.data:
            current = self.coordinator.data.get("setpoint_current")
            if current and current > 0:
                self._last_setpoint = current
        _LOGGER.debug("ETAPpro: pausing charging (last setpoint was %.1f A)", self._last_setpoint)
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.client.set_current_setpoint, 0
            )
        except ETAPproModbusError as err:
            _LOGGER.error("ETAPpro: failed to pause charging: %s", err)
            return
        await self.coordinator.async_request_refresh()
