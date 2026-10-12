"""Config flow for Staykey integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_ALLOW_SUPPORT_ACTIONS,
    CONF_GATEWAY_TOKEN,
    CONF_GATEWAY_URL,
    DEFAULT_ALLOW_SUPPORT_ACTIONS,
    DEFAULT_GATEWAY_URL,
    DOMAIN,
)
from .gateway.client import AUTH_OK, probe_token


def _current_token(entry: config_entries.ConfigEntry) -> str:
    """The token ``entry`` connects with: its options token, if set, over the
    one it was set up with."""
    return (
        entry.options.get(CONF_GATEWAY_TOKEN)
        or entry.data.get(CONF_GATEWAY_TOKEN)
        or ""
    ).strip()


def _token_in_use_elsewhere(
    hass: HomeAssistant, token: str, entry_id: str | None = None
) -> bool:
    """Whether a Staykey entry other than ``entry_id`` uses ``token``.

    Checking such a token with the gateway would cut that entry off: the
    gateway gives a token's connection to the newest one, and the check
    closes it straight away. A token that is another entry's unique ID counts
    too, since saving it would give two entries the same unique ID.
    """
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == entry_id:
            continue
        if token in (_current_token(entry), entry.unique_id):
            return True
    return False


class StaykeyOptionsFlowHandler(config_entries.OptionsFlowWithReload):
    """Options for a Staykey entry.

    Saving the form reloads the entry. That also brings back an entry whose
    setup stopped for want of a gateway token, as soon as one is added here.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        options = self.config_entry.options
        data = self.config_entry.data
        token = options.get(CONF_GATEWAY_TOKEN, data.get(CONF_GATEWAY_TOKEN)) or None

        if user_input is not None:
            new_token = (user_input.get(CONF_GATEWAY_TOKEN) or "").strip()
            # The token isn't checked with the gateway here: this entry's own
            # connection is live, and a check would take it over. After the
            # reload, the entry asks for a new token if Staykey rejects it.
            if new_token and _token_in_use_elsewhere(
                self.hass, new_token, self.config_entry.entry_id
            ):
                errors["base"] = "token_in_use"
            else:
                if new_token and new_token != _current_token(self.config_entry):
                    # The entry's unique ID follows the token it uses.
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, unique_id=new_token
                    )
                if new_token:
                    user_input = {**user_input, CONF_GATEWAY_TOKEN: new_token}
                return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema(
            {
                # Required, and pre-filled rather than defaulted: a default
                # would quietly put the saved token back when the field is
                # cleared, where required makes the form say it is needed.
                vol.Required(
                    CONF_GATEWAY_TOKEN,
                    description={"suggested_value": token},
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
        if user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)
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
                # The unique ID check misses an entry first set up for
                # webhook mode, or one whose token was changed under
                # Configure while the unique ID stayed behind. Checking a
                # token such an entry uses would cut it off.
                if _token_in_use_elsewhere(self.hass, gateway_token):
                    errors["base"] = "token_in_use"
                else:
                    gateway_url = (
                        user_input.get(CONF_GATEWAY_URL) or DEFAULT_GATEWAY_URL
                    )
                    outcome = await probe_token(self.hass, gateway_url, gateway_token)
                    if outcome == AUTH_OK:
                        return self.async_create_entry(
                            title="Staykey",
                            data={**user_input, CONF_GATEWAY_TOKEN: gateway_token},
                        )
                    errors["base"] = outcome

        data_schema = vol.Schema(
            {
                vol.Required(CONF_GATEWAY_TOKEN): str,
                vol.Optional(CONF_GATEWAY_URL, default=DEFAULT_GATEWAY_URL): str,
            }
        )
        if user_input is not None:
            # Keep what was typed, to correct.
            data_schema = self.add_suggested_values_to_schema(data_schema, user_input)
        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def async_step_import(self, user_input: dict[str, Any]) -> FlowResult:
        return await self.async_step_user(user_input)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """Started by the entry when Staykey rejects its gateway token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            entry = self._get_reauth_entry()
            gateway_token = (user_input.get(CONF_GATEWAY_TOKEN) or "").strip()

            if not gateway_token:
                errors["base"] = "gateway_token_required"
            elif gateway_token == _current_token(entry):
                # This entry is still retrying that token, so checking it as
                # well would take the entry's connection over. Reload the
                # entry to try it again instead.
                return self.async_update_reload_and_abort(entry)
            elif _token_in_use_elsewhere(self.hass, gateway_token, entry.entry_id):
                errors["base"] = "token_in_use"
            else:
                # A different token from the one this entry is retrying, so
                # checking it takes no connection over.
                gateway_url = (
                    entry.options.get(CONF_GATEWAY_URL)
                    or entry.data.get(CONF_GATEWAY_URL)
                    or DEFAULT_GATEWAY_URL
                )
                outcome = await probe_token(self.hass, gateway_url, gateway_token)
                if outcome == AUTH_OK:
                    # Setup reads the token from the options first, so a
                    # token saved there is replaced too.
                    options = (
                        {**entry.options, CONF_GATEWAY_TOKEN: gateway_token}
                        if CONF_GATEWAY_TOKEN in entry.options
                        else entry.options
                    )
                    return self.async_update_reload_and_abort(
                        entry,
                        unique_id=gateway_token,
                        data_updates={CONF_GATEWAY_TOKEN: gateway_token},
                        options=options,
                    )
                errors["base"] = outcome

        data_schema = vol.Schema({vol.Required(CONF_GATEWAY_TOKEN): str})
        if user_input is not None:
            # Keep what was typed, to correct.
            data_schema = self.add_suggested_values_to_schema(data_schema, user_input)
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=data_schema, errors=errors
        )
