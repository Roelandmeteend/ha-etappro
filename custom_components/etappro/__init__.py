"""ETAPpro EV Charger integration for Home Assistant.

Communicates via Modbus TCP with the ETAPpro charger by EVchargeking.
No cloud, no API key — only the local IP address of the charger is required.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN, SCAN_INTERVAL_FAST, VERSION
from .coordinator import ETAPproCoordinator
from .modbus_client import ETAPproModbusClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.NUMBER, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the ETAPpro integration from a config entry."""
    _LOGGER.debug("ETAPpro integration version %s starting", VERSION)
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]

    client = ETAPproModbusClient(host, port)

    # Use custom scan interval if set via the options flow
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, SCAN_INTERVAL_FAST)

    coordinator = ETAPproCoordinator(
        hass,
        client,
        charger_name=entry.title,
    )
    coordinator.update_interval = timedelta(seconds=scan_interval)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the integration when options are changed
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration after an options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the ETAPpro integration."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: ETAPproCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await hass.async_add_executor_job(coordinator.client.disconnect)
    return unload_ok
