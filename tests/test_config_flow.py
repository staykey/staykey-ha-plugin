"""The options flow's form."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import voluptuous as vol
from homeassistant.config_entries import OptionsFlowWithReload

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))
from staykey.config_flow import StaykeyOptionsFlowHandler  # noqa: E402


def _options_form(options: dict | None = None, data: dict | None = None) -> dict:
    flow = StaykeyOptionsFlowHandler()
    flow.config_entry = SimpleNamespace(
        options=options or {}, data=data or {"gateway_token": "token-abc"}
    )
    return asyncio.run(flow.async_step_init())


def _defaults(form: dict) -> dict:
    """Each option the form offers, with the value it starts at.

    A required field has no default; the form pre-fills it with a suggested
    value instead.
    """
    return {
        str(key): key.default()
        if callable(key.default)
        else (key.description or {}).get("suggested_value")
        for key in form["data_schema"].schema
    }


def _key(form: dict, name: str):
    return next(key for key in form["data_schema"].schema if str(key) == name)


def test_support_actions_are_offered_and_start_off():
    assert _defaults(_options_form())["allow_support_actions"] is False


def test_support_actions_show_the_saved_choice():
    form = _options_form(options={"allow_support_actions": True})

    assert _defaults(form)["allow_support_actions"] is True


def test_saving_the_form_stores_the_choice():
    flow = StaykeyOptionsFlowHandler()
    flow.config_entry = SimpleNamespace(options={}, data={})
    user_input = {"gateway_token": "token-abc", "allow_support_actions": True}

    result = asyncio.run(flow.async_step_init(user_input))

    assert result["data"] == user_input


def test_saving_options_reloads_the_entry():
    assert issubclass(StaykeyOptionsFlowHandler, OptionsFlowWithReload)


def test_options_offer_only_the_gateway_settings():
    form = _options_form(
        data={
            "gateway_token": "token-abc",
            "endpoint_url": "https://example.invalid/hook",
        }
    )

    assert set(_defaults(form)) == {
        "gateway_token",
        "gateway_url",
        "allow_support_actions",
    }


def test_the_gateway_token_is_required_and_prefilled():
    form = _options_form(data={"gateway_token": "token-abc"})

    assert isinstance(_key(form, "gateway_token"), vol.Required)
    assert _defaults(form)["gateway_token"] == "token-abc"


def test_the_saved_options_token_is_prefilled_over_the_setup_token():
    form = _options_form(
        options={"gateway_token": "token-new"}, data={"gateway_token": "token-abc"}
    )

    assert _defaults(form)["gateway_token"] == "token-new"


def test_clearing_the_gateway_token_is_refused():
    form = _options_form(data={"gateway_token": "token-abc"})
    # The frontend drops an emptied field from what it submits.
    submitted = {
        "gateway_url": "wss://example.invalid/ws",
        "allow_support_actions": False,
    }

    with pytest.raises(vol.MultipleInvalid):
        form["data_schema"](submitted)


def test_an_entry_without_a_token_shows_an_empty_required_field():
    form = _options_form(data={"endpoint_url": "https://example.invalid/hook"})

    assert isinstance(_key(form, "gateway_token"), vol.Required)
    assert _defaults(form)["gateway_token"] is None
