"""Config flow for Staykey integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_ALLOW_SUPPORT_ACTIONS,
    CONF_GATEWAY_TOKEN,
    CONF_GATEWAY_URL,
    DEFAULT_ALLOW_SUPPORT_ACTIONS,
    DEFAULT_GATEWAY_URL,
    DOMAIN,
)


class StaykeyOptionsFlowHandler(config_entries.OptionsFlowWithReload):
    """Options for a Staykey entry.

    Saving the form reloads the entry. That also brings back an entry whose
    setup stopped for want of a gateway token, as soon as one is added here.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        data = self.config_entry.data

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_GATEWAY_TOKEN,
                    default=options.get(
                        CONF_GATEWAY_TOKEN, data.get(CONF_GATEWAY_TOKEN, "")
                    ),
                ): str,
                vol.Optional(
                    CONF_GATEWAY_URL,
                    default=options.get(
                        CONF_GATEWAY_URL,
                        data.get(CONF_GATEWAY_URL, DEFAULT_GATEWAY_URL),
                    ),
                ): str,
                vol.Optional(
                    CONF_ALLOW_SUPPORT_ACTIONS,
                    default=options.get(
                        CONF_ALLOW_SUPPORT_ACTIONS, DEFAULT_ALLOW_SUPPORT_ACTIONS
                    ),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)


class StaykeyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Staykey."""

    VERSION = 2

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> StaykeyOptionsFlowHandler:
        return StaykeyOptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        # Every entry connects through the Staykey gateway, so the token is
        # required.
        errors: dict[str, str] = {}

        if user_input is not None:
            gateway_token = user_input.get(CONF_GATEWAY_TOKEN, "").strip()

            if not gateway_token:
                errors["base"] = "gateway_token_required"
            else:
                await self.async_set_unique_id(gateway_token)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Staykey", data=user_input)

        data_schema = vol.Schema(
            {
                vol.Required(CONF_GATEWAY_TOKEN): str,
                vol.Optional(CONF_GATEWAY_URL, default=DEFAULT_GATEWAY_URL): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def async_step_import(self, user_input: dict[str, Any]) -> FlowResult:
        return await self.async_step_user(user_input)
