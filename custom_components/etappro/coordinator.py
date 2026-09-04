"""DataUpdateCoordinator voor de ETAPpro integratie."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from modbus_connection import ModbusError

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL_FAST
from .device import ETAPproDevice

_LOGGER = logging.getLogger(__name__)


class ETAPproCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Haalt periodiek alle data op via Modbus TCP.

    HA-sturing is gekoppeld aan een laadsessie:
      - De sessie is actief zodra de lader laadt (IEC 61851 modus C) en blijft
        actief tijdens pauzes (modus B) tot de auto loskoppelt (modus A).
      - Alleen tijdens een actieve sessie schrijft de coordinator het gewenste
        setpoint bij elke poll opnieuw (keepalive), zodat de lader de door HA /
        automatiseringen ingestelde waarde niet naar zijn eigen config terugzet.
      - Zodra de lader vrij is (modus A) laat HA volledig los: geen keepalive en
        het gewenste setpoint wordt gewist, klaar voor een nieuwe sessie
        (gestart via laadpas of de HA "Laden"-schakelaar).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        device: ETAPproDevice,
        charger_name: str,
    ) -> None:
        self.device = device
        self.charger_name = charger_name
        # Gewenste setpoint ingesteld vanuit HA/automatiseringen; wordt tijdens
        # een actieve sessie bij elke poll hergeschreven als keepalive.
        self._desired_setpoint: float | None = None
        # True zolang er een laadsessie loopt (modus C gezien, nog geen modus A).
        self._session_active: bool = False

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL_FAST),
        )

    def set_desired_setpoint(self, ampere: float | None) -> None:
        """Sla het gewenste setpoint op; tijdens een sessie wordt dit als keepalive geschreven."""
        self._desired_setpoint = ampere

    async def _async_update_data(self) -> dict[str, Any]:
        """Lees alle Modbus-registers en onderhoud de keepalive tijdens een sessie."""
        try:
            data = await self.device.async_read_all()
        except ModbusError as err:
            raise UpdateFailed(f"ETAPpro Modbus-fout: {err}") from err

        self._update_session_state(data)

        if self._session_active and self._desired_setpoint is not None:
            try:
                await self.device.async_set_current_setpoint(self._desired_setpoint)
                _LOGGER.debug(
                    "ETAPpro keepalive: setpoint %.1f A hergeschreven", self._desired_setpoint
                )
            except ModbusError as err:
                _LOGGER.warning("ETAPpro keepalive mislukt: %s", err)

        return data

    def _update_session_state(self, data: dict[str, Any]) -> None:
        """Werk de sessie-latch bij op basis van de IEC 61851 modus.

        Modus A → vrij: sessie beëindigd, HA laat volledig los.
        Modus C → laden: sessie actief.
        Modus B (of fout E/F): latch ongewijzigd — pauze binnen een sessie
            als die al liep, of nog geen sessie als er nog niet geladen is.
        """
        mode_prefix = (data.get("mode") or "A")[:1].upper()

        if mode_prefix == "A":
            if self._session_active:
                _LOGGER.info("ETAPpro: laadsessie beëindigd (lader vrij), HA laat los")
            self._session_active = False
            self._desired_setpoint = None
        elif mode_prefix == "C":
            if not self._session_active:
                _LOGGER.info("ETAPpro: laadsessie gestart (modus C), HA-sturing actief")
            self._session_active = True
