"""DataUpdateCoordinator for the ETAPpro integration."""
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
    """Periodically fetches all data over Modbus TCP.

    HA control is tied to a charging session:
      - The session is active as soon as the charger is charging (IEC 61851
        mode C) and stays active through pauses (mode B) until the car is
        unplugged (mode A).
      - Only while a session is active does the coordinator re-write the
        desired setpoint on every poll (keepalive), so the charger does not
        revert the value set by HA or its automations to its own config.
      - Once the charger is free (mode A) HA releases completely: no keepalive
        and the desired setpoint is cleared, ready for a new session started
        either by an RFID card or the HA "Charging" switch.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: ETAPproModbusClient,
        charger_name: str,
    ) -> None:
        self.client = client
        self.charger_name = charger_name
        # Desired setpoint set from HA/automations; re-written on every poll as
        # a keepalive while a session is active.
        self._desired_setpoint: float | None = None
        # True while a charging session is running (mode C seen, no mode A yet).
        self._session_active: bool = False

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL_FAST),
        )

    def set_desired_setpoint(self, ampere: float | None) -> None:
        """Store the desired setpoint; written as a keepalive during a session."""
        self._desired_setpoint = ampere

    async def _async_update_data(self) -> dict[str, Any]:
        """Read all Modbus registers and maintain the keepalive during a session."""
        try:
            data = await self.hass.async_add_executor_job(self.client.read_all)
        except ETAPproModbusError as err:
            raise UpdateFailed(f"ETAPpro Modbus error: {err}") from err

        self._update_session_state(data)

        if self._session_active and self._desired_setpoint is not None:
            try:
                await self.hass.async_add_executor_job(
                    self.client.set_current_setpoint, self._desired_setpoint
                )
                _LOGGER.debug(
                    "ETAPpro keepalive: setpoint %.1f A re-written", self._desired_setpoint
                )
            except ETAPproModbusError as err:
                _LOGGER.warning("ETAPpro keepalive failed: %s", err)

        return data

    def _update_session_state(self, data: dict[str, Any]) -> None:
        """Update the session latch based on the IEC 61851 mode.

        Mode A → free: session ended, HA releases completely.
        Mode C → charging: session active.
        Mode B (or fault E/F): latch unchanged — a pause within a session if one
            was already running, or no session yet if charging never started.
        """
        mode_prefix = (data.get("mode") or "A")[:1].upper()

        if mode_prefix == "A":
            if self._session_active:
                _LOGGER.info("ETAPpro: charging session ended (charger free), HA releases")
            self._session_active = False
            self._desired_setpoint = None
        elif mode_prefix == "C":
            if not self._session_active:
                _LOGGER.info("ETAPpro: charging session started (mode C), HA control active")
            self._session_active = True
