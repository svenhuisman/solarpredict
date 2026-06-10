"""Data update coordinator for SolarPredict."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_DAMPING_EVENING,
    CONF_DAMPING_MORNING,
    CONF_DAYS,
    CONF_EFFICIENCY,
    CONF_HORIZON,
    CONF_HOST,
    CONF_INVERTER_KW,
    CONF_PLANES,
    CONF_RESOLUTION,
    DOMAIN,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

_PARAM_MAP = [
    (CONF_LATITUDE, "lat"),
    (CONF_LONGITUDE, "lon"),
    (CONF_PLANES, "planes"),
    (CONF_HORIZON, "horizon"),
    (CONF_DAMPING_MORNING, "damping_morning"),
    (CONF_DAMPING_EVENING, "damping_evening"),
    (CONF_INVERTER_KW, "inverter_kw"),
    (CONF_EFFICIENCY, "efficiency"),
    (CONF_DAYS, "days"),
    (CONF_RESOLUTION, "resolution"),
]


class SolarPredictCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the SolarPredict server's /api/forecast endpoint."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.entry = entry

    @property
    def host(self) -> str:
        return self.entry.data[CONF_HOST].rstrip("/")

    def _params(self) -> dict[str, str]:
        merged = {**self.entry.data, **self.entry.options}
        params: dict[str, str] = {}
        if self.hass.config.time_zone:
            params["tz"] = self.hass.config.time_zone
        for conf_key, query_key in _PARAM_MAP:
            value = merged.get(conf_key)
            if value in (None, ""):
                continue
            # inverter_kw 0 means "no limit" in the options UI
            if conf_key == CONF_INVERTER_KW and float(value) == 0.0:
                continue
            params[query_key] = str(value)
        return params

    async def _async_update_data(self) -> dict[str, Any]:
        session = async_get_clientsession(self.hass)
        url = f"{self.host}/api/forecast"
        try:
            async with session.get(
                url, params=self._params(), timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status != 200:
                    body = (await resp.text())[:200]
                    raise UpdateFailed(f"SolarPredict returned HTTP {resp.status}: {body}")
                data = await resp.json()
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Cannot reach SolarPredict at {url}: {err}") from err

        if "summary" not in data or "series" not in data:
            raise UpdateFailed("Unexpected response from SolarPredict (missing summary/series)")
        return data
