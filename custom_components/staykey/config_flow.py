"""Config flow for Staykey integration."""

from __future__ import annotations

from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_ENDPOINT_URL,
    CONF_FORWARD_ALL_NOTIFICATIONS,
    CONF_GATEWAY_TOKEN,
    CONF_GATEWAY_URL,
    CONF_TIMEOUT,
    CONF_VERIFY_SSL,
    DEFAULT_FORWARD_ALL_NOTIFICATIONS,
    DEFAULT_GATEWAY_URL,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)


class StaykeyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Staykey."""

    VERSION = 2

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        errors: Dict[str, str] = {}

        if user_input is not None:
            gateway_token = user_input.get(CONF_GATEWAY_TOKEN, "").strip()
            endpoint_url = user_input.get(CONF_ENDPOINT_URL, "").strip()

            if not gateway_token and not endpoint_url:
                errors["base"] = "must_provide_token_or_url"
            else:
                unique_id = gateway_token or endpoint_url
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Staykey", data=user_input)

        data_schema = vol.Schema(
            {
                vol.Optional(CONF_GATEWAY_TOKEN, default=""): str,
                vol.Optional(CONF_GATEWAY_URL, default=DEFAULT_GATEWAY_URL): str,
                vol.Optional(CONF_ENDPOINT_URL, default=""): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

    async def async_step_import(self, user_input: Dict[str, Any]) -> FlowResult:
        return await self.async_step_user(user_input)


class StaykeyOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        errors: Dict[str, str] = {}
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        data = self.config_entry.data

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_GATEWAY_TOKEN,
                    default=options.get(CONF_GATEWAY_TOKEN, data.get(CONF_GATEWAY_TOKEN, "")),
                ): str,
                vol.Optional(
                    CONF_GATEWAY_URL,
                    default=options.get(CONF_GATEWAY_URL, data.get(CONF_GATEWAY_URL, DEFAULT_GATEWAY_URL)),
                ): str,
                vol.Optional(
                    CONF_FORWARD_ALL_NOTIFICATIONS,
                    default=options.get(
                        CONF_FORWARD_ALL_NOTIFICATIONS, DEFAULT_FORWARD_ALL_NOTIFICATIONS
                    ),
                ): bool,
                vol.Optional(
                    CONF_VERIFY_SSL, default=options.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
                ): bool,
                vol.Optional(
                    CONF_TIMEOUT, default=options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT_SECONDS)
                ): int,
                vol.Optional(
                    CONF_ENDPOINT_URL,
                    default=options.get(
                        CONF_ENDPOINT_URL,
                        data.get(CONF_ENDPOINT_URL, ""),
                    ),
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)


async def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> StaykeyOptionsFlowHandler:
    return StaykeyOptionsFlowHandler(config_entry)
