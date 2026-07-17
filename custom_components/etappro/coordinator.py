"""DataUpdateCoordinator voor de ETAPpro integratie."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, MIN_CURRENT_A, SCAN_INTERVAL_FAST
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
        # None betekent: HA-sturing inactief, lader beheert zichzelf.
        self._desired_setpoint: float | None = None
        # Vorige modusletter (A/B/C/E/F); None bij eerste poll om onterechte
        # auto-activatie bij HA-opstart te voorkomen.
        self._prev_mode_prefix: str | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL_FAST),
        )

    @property
    def desired_setpoint(self) -> float | None:
        return self._desired_setpoint

    def set_desired_setpoint(self, ampere: float | None) -> None:
        """Sla de gewenste setpoint op; wordt bij elke poll als keepalive geschreven."""
        self._desired_setpoint = ampere

    async def _async_update_data(self) -> dict[str, Any]:
        """Lees alle Modbus-registers, activeer/deactiveer HA-sturing op sessiewijziging."""
        try:
            data = await self.hass.async_add_executor_job(self.client.read_all)
        except ETAPproModbusError as err:
            raise UpdateFailed(f"ETAPpro Modbus-fout: {err}") from err

        self._handle_session_transition(data)

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

    def _handle_session_transition(self, data: dict[str, Any]) -> None:
        """Schakel HA-sturing automatisch in/uit op basis van laadsessie-overgangen.

        Overgang A → B of C: sessie gestart (RFID, app of vrij laden)
            → HA-sturing inschakelen zodat automaties (peakshaving, solar, ...)
              direct controle hebben over het laadstroom setpoint.

        Overgang B/C → A: sessie beëindigd (auto losgekoppeld)
            → HA-sturing uitschakelen zodat de lader zichzelf beheert.

        Bij de eerste poll wordt geen overgang verwerkt om te voorkomen dat
        HA-sturing onterecht inschakelt als HA herstart terwijl een sessie loopt.
        """
        mode_prefix = (data.get("mode") or "A")[:1].upper()

        if self._prev_mode_prefix is None:
            # Eerste poll: toestand opslaan zonder actie.
            self._prev_mode_prefix = mode_prefix
            return

        was_idle = self._prev_mode_prefix == "A"
        is_active = mode_prefix in ("B", "C")
        was_active = self._prev_mode_prefix in ("B", "C")
        is_idle = mode_prefix == "A"

        if was_idle and is_active and self._desired_setpoint is None:
            # Nieuwe sessie gedetecteerd: HA-sturing inschakelen.
            setpoint = data.get("setpoint_current")
            self._desired_setpoint = max(float(setpoint), MIN_CURRENT_A) if setpoint else MIN_CURRENT_A
            _LOGGER.info(
                "ETAPpro: laadsessie gestart (modus %s), HA-sturing automatisch ingeschakeld op %.1f A",
                mode_prefix, self._desired_setpoint,
            )

        elif was_active and is_idle:
            # Sessie beëindigd: HA-sturing uitschakelen.
            if self._desired_setpoint is not None:
                _LOGGER.info(
                    "ETAPpro: laadsessie beëindigd (modus %s → A), HA-sturing automatisch uitgeschakeld",
                    self._prev_mode_prefix,
                )
            self._desired_setpoint = None

        self._prev_mode_prefix = mode_prefix
