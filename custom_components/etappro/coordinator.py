"""DataUpdateCoordinator voor de ETAPpro integratie."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL_FAST
from .modbus_client import ETAPproModbusClient, ETAPproModbusError

_LOGGER = logging.getLogger(__name__)


class ETAPproCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Haalt periodiek alle data op via Modbus TCP."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: ETAPproModbusClient,
        charger_name: str,
    ) -> None:
        self.client = client
        self.charger_name = charger_name
        # Gewenste setpoint ingesteld vanuit HA; wordt bij elke poll hergeschreven
        # als keepalive zodat de lader niet terugvalt op zijn eigen configuratie.
        # None betekent: HA heeft nog geen waarde ingesteld, geen keepalive.
        self._desired_setpoint: float | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL_FAST),
        )

    def set_desired_setpoint(self, ampere: float) -> None:
        """Sla de gewenste setpoint op; wordt bij elke poll als keepalive geschreven."""
        self._desired_setpoint = ampere

    async def _async_update_data(self) -> dict[str, Any]:
        """Lees alle Modbus-registers en stuur keepalive setpoint (in een thread-executor)."""
        try:
            data = await self.hass.async_add_executor_job(self.client.read_all)
        except ETAPproModbusError as err:
            raise UpdateFailed(f"ETAPpro Modbus-fout: {err}") from err

        if self._desired_setpoint is not None:
            try:
                await self.hass.async_add_executor_job(
                    self.client.set_current_setpoint, self._desired_setpoint
                )
                _LOGGER.debug(
                    "ETAPpro keepalive: setpoint %.1f A hergeschreven", self._desired_setpoint
                )
            except ETAPproModbusError as err:
                _LOGGER.warning("ETAPpro keepalive mislukt: %s", err)

        return data
