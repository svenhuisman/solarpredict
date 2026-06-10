"""Config flow for SolarPredict."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_API_TOKEN,
    CONF_DAMPING_EVENING,
    CONF_DAMPING_MORNING,
    CONF_DAYS,
    CONF_EFFICIENCY,
    CONF_HORIZON,
    CONF_HOST,
    CONF_INVERTER_KW,
    CONF_PLANES,
    CONF_RESOLUTION,
    DEFAULT_HOST,
    DEFAULT_PLANES,
    DOMAIN,
)


class SolarPredictConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].rstrip("/")
            user_input[CONF_HOST] = host

            await self.async_set_unique_id(host)
            self._abort_if_unique_id_configured()

            if await self._can_connect(host):
                return self.async_create_entry(title="SolarPredict", data=user_input)
            errors["base"] = "cannot_connect"

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
                vol.Required(
                    CONF_LATITUDE, default=self.hass.config.latitude
                ): vol.Coerce(float),
                vol.Required(
                    CONF_LONGITUDE, default=self.hass.config.longitude
                ): vol.Coerce(float),
                vol.Required(CONF_PLANES, default=DEFAULT_PLANES): str,
                vol.Optional(CONF_API_TOKEN, default=""): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow changing host, location, planes and token after setup."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].rstrip("/")
            user_input[CONF_HOST] = host

            existing = self.hass.config_entries.async_entry_for_domain_unique_id(
                self.handler, host
            )
            if existing is not None and existing.entry_id != entry.entry_id:
                return self.async_abort(reason="already_configured")

            if await self._can_connect(host):
                return self.async_update_reload_and_abort(
                    entry, unique_id=host, data_updates=user_input
                )
            errors["base"] = "cannot_connect"

        data = {**entry.data, **(user_input or {})}
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=data[CONF_HOST]): str,
                vol.Required(CONF_LATITUDE, default=data[CONF_LATITUDE]): vol.Coerce(
                    float
                ),
                vol.Required(CONF_LONGITUDE, default=data[CONF_LONGITUDE]): vol.Coerce(
                    float
                ),
                vol.Required(
                    CONF_PLANES, default=data.get(CONF_PLANES, DEFAULT_PLANES)
                ): str,
                vol.Optional(CONF_API_TOKEN, default=data.get(CONF_API_TOKEN, "")): str,
            }
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )

    async def _can_connect(self, host: str) -> bool:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                f"{host}/healthz", timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                return resp.status == 200
        except aiohttp.ClientError:
            return False

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> SolarPredictOptionsFlow:
        return SolarPredictOptionsFlow()


class SolarPredictOptionsFlow(OptionsFlow):
    """Advanced options: horizon, damping, inverter, efficiency, resolution."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_HORIZON, default=options.get(CONF_HORIZON, "")
                ): str,
                vol.Optional(
                    CONF_DAMPING_MORNING, default=options.get(CONF_DAMPING_MORNING, 0.0)
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
                vol.Optional(
                    CONF_DAMPING_EVENING, default=options.get(CONF_DAMPING_EVENING, 0.0)
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
                vol.Optional(
                    CONF_INVERTER_KW, default=options.get(CONF_INVERTER_KW, 0.0)
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
                vol.Optional(
                    CONF_EFFICIENCY, default=options.get(CONF_EFFICIENCY, 0.90)
                ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=1.0)),
                vol.Optional(
                    CONF_DAYS, default=options.get(CONF_DAYS, 3)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=7)),
                vol.Optional(
                    CONF_RESOLUTION, default=options.get(CONF_RESOLUTION, 15)
                ): vol.In([15, 60]),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
