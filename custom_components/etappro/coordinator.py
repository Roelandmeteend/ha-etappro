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

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL_FAST),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Lees alle Modbus-registers (in een thread-executor)."""
        try:
            return await self.hass.async_add_executor_job(self.client.read_all)
        except ETAPproModbusError as err:
            raise UpdateFailed(f"ETAPpro Modbus-fout: {err}") from err
