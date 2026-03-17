"""Number entities for the ETAPpro — current setpoint and phases."""
from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MIN_CURRENT_A
from .coordinator import ETAPproCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register number entities."""
    coordinator: ETAPproCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        ETAPproCurrentSetpoint(coordinator, entry),
        ETAPproPhases(coordinator, entry),
    ])


class ETAPproCurrentSetpoint(CoordinatorEntity[ETAPproCoordinator], NumberEntity):
    """Current setpoint — adjustable via a slider in the UI.

    Writes to Modbus register 1210 (FLOAT32, R/W).
    The hardware maximum (register 1100) is used as the upper limit.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "current_setpoint"
    _attr_icon = "mdi:current-ac"
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 0.5

    def __init__(self, coordinator: ETAPproCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_current_setpoint"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=coordinator.charger_name,
            manufacturer="EVchargeking",
            model="ETAPpro",
        )

    @property
    def native_min_value(self) -> float:
        return MIN_CURRENT_A

    @property
    def native_max_value(self) -> float:
        """Use the charger's hardware limit as the maximum."""
        if self.coordinator.data:
            hw_max = self.coordinator.data.get("max_current_hw")
            if hw_max and hw_max > 0:
                return float(hw_max)
        return 32.0

    @property
    def native_value(self) -> float | None:
        """Current setpoint value from register 1210."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("setpoint_current")

    async def async_set_native_value(self, value: float) -> None:
        """Write the new setpoint to register 1210."""
        await self.hass.async_add_executor_job(
            self.coordinator.client.set_current_setpoint, value
        )
        await self.coordinator.async_request_refresh()


class ETAPproPhases(CoordinatorEntity[ETAPproCoordinator], NumberEntity):
    """Number of charging phases — 1 or 3.

    Writes to Modbus register 1215 (UINT16, R/W).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "phases"
    _attr_icon = "mdi:sine-wave"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 1
    _attr_native_max_value = 3
    _attr_native_step = 2  # only 1 and 3 are valid

    def __init__(self, coordinator: ETAPproCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_phases"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=coordinator.charger_name,
            manufacturer="EVchargeking",
            model="ETAPpro",
        )

    @property
    def native_value(self) -> float | None:
        """Current phase count from register 1215."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("phases")

    async def async_set_native_value(self, value: float) -> None:
        """Write the phase count to register 1215."""
        phases = int(value)
        if phases not in (1, 3):
            phases = 1 if phases < 2 else 3
        await self.hass.async_add_executor_job(
            self.coordinator.client.set_phases, phases
        )
        await self.coordinator.async_request_refresh()
