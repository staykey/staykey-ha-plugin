"""The options flow's form."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))
from staykey.config_flow import StaykeyOptionsFlowHandler  # noqa: E402


def _options_form(options: dict | None = None, data: dict | None = None) -> dict:
    flow = StaykeyOptionsFlowHandler()
    flow.config_entry = SimpleNamespace(
        options=options or {}, data=data or {"gateway_token": "token-abc"}
    )
    return asyncio.run(flow.async_step_init())


def _defaults(form: dict) -> dict:
    """Each option the form offers, with the value it starts at."""
    return {str(key): key.default() for key in form["data_schema"].schema}


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
