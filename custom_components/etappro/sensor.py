"""Sensor platform for the ETAPpro EV charger."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODE_LABELS
from .coordinator import ETAPproCoordinator


@dataclass
class ETAPproSensorDescription(SensorEntityDescription):
    """Sensor description with the corresponding data dict key."""
    data_key: str = ""
    value_fn: Any = None


SENSOR_DESCRIPTIONS: tuple[ETAPproSensorDescription, ...] = (
    # Voltage
    ETAPproSensorDescription(
        key="voltage_l1", translation_key="voltage_l1", data_key="voltage_l1",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ETAPproSensorDescription(
        key="voltage_l2", translation_key="voltage_l2", data_key="voltage_l2",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ETAPproSensorDescription(
        key="voltage_l3", translation_key="voltage_l3", data_key="voltage_l3",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    # Current
    ETAPproSensorDescription(
        key="current_l1", translation_key="current_l1", data_key="current_l1",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    ETAPproSensorDescription(
        key="current_l2", translation_key="current_l2", data_key="current_l2",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    ETAPproSensorDescription(
        key="current_l3", translation_key="current_l3", data_key="current_l3",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    ETAPproSensorDescription(
        key="current_sum", translation_key="current_sum", data_key="current_sum",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    # Power
    ETAPproSensorDescription(
        key="power_l1", translation_key="power_l1", data_key="power_l1",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    ETAPproSensorDescription(
        key="power_l2", translation_key="power_l2", data_key="power_l2",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    ETAPproSensorDescription(
        key="power_l3", translation_key="power_l3", data_key="power_l3",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    ETAPproSensorDescription(
        key="power_sum", translation_key="power_sum", data_key="power_sum",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    # Energy
    ETAPproSensorDescription(
        key="energy_kwh", translation_key="energy_kwh", data_key="energy_wh",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda wh: round(wh / 1000, 2),
    ),
    # Temperature
    ETAPproSensorDescription(
        key="temp_board", translation_key="temp_board", data_key="temp_board",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ETAPproSensorDescription(
        key="temp_ev_plug", translation_key="temp_ev_plug", data_key="temp_ev_plug",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ETAPproSensorDescription(
        key="temp_grid_plug", translation_key="temp_grid_plug", data_key="temp_grid_plug",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    # Status
    ETAPproSensorDescription(
        key="status", translation_key="status", data_key="mode",
        icon="mdi:ev-station",
        value_fn=lambda m: MODE_LABELS.get(m.upper(), m) if m else "Unknown",
    ),
    ETAPproSensorDescription(
        key="mode", translation_key="mode", data_key="mode",
        icon="mdi:information-outline",
    ),
    ETAPproSensorDescription(
        key="applied_current", translation_key="applied_current", data_key="applied_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ETAPproSensorDescription(
        key="max_current_hw", translation_key="max_current_hw", data_key="max_current_hw",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    ETAPproSensorDescription(
        key="started_by", translation_key="started_by", data_key="started_by",
        icon="mdi:account-key",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register all sensors for this config entry."""
    coordinator: ETAPproCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ETAPproSensor(coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    )


class ETAPproSensor(CoordinatorEntity[ETAPproCoordinator], SensorEntity):
    """A single sensor entity for the ETAPpro charger."""

    entity_description: ETAPproSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ETAPproCoordinator,
        entry: ConfigEntry,
        description: ETAPproSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=coordinator.charger_name,
            manufacturer="EVchargeking",
            model="ETAPpro",
            configuration_url=f"http://{entry.data['host']}",
        )

    @property
    def native_value(self) -> Any:
        """Return the current value, optionally transformed."""
        if self.coordinator.data is None:
            return None
        raw = self.coordinator.data.get(self.entity_description.data_key)
        if raw is None:
            return None
        fn = self.entity_description.value_fn
        return fn(raw) if fn else raw
