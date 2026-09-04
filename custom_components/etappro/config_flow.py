"""Config flow for the ETAPpro integration — fully GUI-based."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from modbus_connection import ModbusError

from homeassistant import config_entries
from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import callback

from .const import DEFAULT_PORT, DOMAIN, SCAN_INTERVAL_FAST
from .device import UNIT_ID, async_test_connection, connection_params

_LOGGER = logging.getLogger(__name__)


class ETAPproConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Step-by-step GUI wizard to set up the ETAPpro."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 1: ask for the IP address and port."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]

            # A connection already held by a config entry is shared and stays
            # up; one opened here is closed again when the context exits.
            try:
                async with async_get_temporary_unit(
                    self.hass, connection_params(host, port), UNIT_ID
                ) as unit:
                    await async_test_connection(unit)
            except ModbusError as err:
                _LOGGER.warning("ETAPpro connection test failed: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during connection test")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(f"{host}:{port}")
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"ETAPpro ({host})",
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> ETAPproOptionsFlow:
        """Return the options flow handler."""
        return ETAPproOptionsFlow()


class ETAPproOptionsFlow(config_entries.OptionsFlow):
    """Options flow: adjust the polling interval after installation."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Show the options form."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, SCAN_INTERVAL_FAST
        )

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL, default=current_interval
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
