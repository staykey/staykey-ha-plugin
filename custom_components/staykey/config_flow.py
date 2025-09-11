"""Config flow for StayKey integration."""

from __future__ import annotations

from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_BACKEND_URL,
    CONF_ENDPOINT_PATH,
    CONF_FORWARD_ALL_NOTIFICATIONS,
    CONF_INTEGRATION_ID,
    CONF_SIGNING_SECRET,
    CONF_TIMEOUT,
    CONF_VERIFY_SSL,
    DEFAULT_ENDPOINT_PATH,
    DEFAULT_FORWARD_ALL_NOTIFICATIONS,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)


class StayKeyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for StayKey."""

    VERSION = 1

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        errors: Dict[str, str] = {}

        if user_input is not None:
            # Use integration_id as unique id to prevent duplicates per HA instance
            await self.async_set_unique_id(user_input[CONF_INTEGRATION_ID])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"StayKey {user_input[CONF_INTEGRATION_ID]}", data=user_input
            )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_INTEGRATION_ID): str,
                vol.Required(CONF_BACKEND_URL): str,
                vol.Required(CONF_SIGNING_SECRET): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

    async def async_step_import(self, user_input: Dict[str, Any]) -> FlowResult:
        # Not supporting YAML, but keep for forward compatibility
        return await self.async_step_user(user_input)


class StayKeyOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        errors: Dict[str, str] = {}
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options

        schema = vol.Schema(
            {
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
                    CONF_ENDPOINT_PATH,
                    default=options.get(CONF_ENDPOINT_PATH, DEFAULT_ENDPOINT_PATH),
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)


async def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> StayKeyOptionsFlowHandler:
    return StayKeyOptionsFlowHandler(config_entry)


