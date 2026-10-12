"""The setup, re-authentication and options forms.

The setup and re-authentication forms check a new gateway token with Staykey
before saving it. No form ever checks a token that another Staykey entry on
this Home Assistant uses: the gateway hands a token's connection to the
newest one, so a check would cut the running entry off.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import voluptuous as vol
from homeassistant.config_entries import OptionsFlowWithReload
from homeassistant.data_entry_flow import AbortFlow

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))
from staykey import config_flow  # noqa: E402
from staykey.config_flow import (  # noqa: E402
    StaykeyConfigFlow,
    StaykeyOptionsFlowHandler,
)
from staykey.const import DEFAULT_GATEWAY_URL, DOMAIN  # noqa: E402

GATEWAY_URL = "wss://gateway.example.invalid/ws"
WEBHOOK_URL = "https://example.invalid/hook"


def _run(coro):
    return asyncio.run(coro)


def _entry(entry_id: str, *, data=None, options=None, unique_id=None):
    return SimpleNamespace(
        entry_id=entry_id,
        domain=DOMAIN,
        title="Staykey",
        data=data or {},
        options=options or {},
        unique_id=unique_id,
    )


class _FakeConfigEntries:
    """The config entries on a Home Assistant, and the updates made to them."""

    def __init__(self, entries):
        self._entries = list(entries)
        self.updates: list[tuple[str, dict]] = []

    def async_entries(self, domain=None, include_ignore=True, include_disabled=True):
        return [e for e in self._entries if domain is None or e.domain == domain]

    def async_get_known_entry(self, entry_id):
        return next(e for e in self._entries if e.entry_id == entry_id)

    def async_update_entry(self, entry, **changes):
        self.updates.append((entry.entry_id, changes))
        return True


def _hass(*entries):
    return SimpleNamespace(config_entries=_FakeConfigEntries(entries))


class _ProbeRecorder:
    """Stands in for ``probe_token``: answers with one outcome and keeps
    which gateway URL and token it was asked to check."""

    def __init__(self, outcome: str):
        self.outcome = outcome
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, _hass, gateway_url: str, token: str) -> str:
        self.calls.append((gateway_url, token))
        return self.outcome


@pytest.fixture
def probe(monkeypatch):
    def install(outcome: str = "ok") -> _ProbeRecorder:
        recorder = _ProbeRecorder(outcome)
        monkeypatch.setattr(config_flow, "probe_token", recorder)
        return recorder

    return install


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
    flow.config_entry = _entry("entry-1")
    flow.hass = _hass(flow.config_entry)
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


def _suggested(form: dict, name: str):
    return (_key(form, name).description or {}).get("suggested_value")


# Options: the token another entry uses, and the unique ID


def _options_flow(entry, *others):
    flow = StaykeyOptionsFlowHandler()
    flow.hass = _hass(entry, *others)
    flow.config_entry = entry
    return flow


@pytest.mark.parametrize(
    "other",
    [
        pytest.param(
            _entry("entry-2", data={"gateway_token": "token-other"}), id="in-data"
        ),
        pytest.param(
            _entry(
                "entry-2",
                data={"endpoint_url": WEBHOOK_URL},
                options={"gateway_token": "token-other"},
                unique_id=WEBHOOK_URL,
            ),
            id="in-options",
        ),
    ],
)
def test_options_refuse_a_token_another_entry_uses(probe, other):
    recorder = probe()
    entry = _entry(
        "entry-1", data={"gateway_token": "token-abc"}, unique_id="token-abc"
    )
    flow = _options_flow(entry, other)
    submitted = {
        "gateway_token": " token-other ",
        "gateway_url": GATEWAY_URL,
        "allow_support_actions": False,
    }

    result = _run(flow.async_step_init(submitted))

    assert result["type"] == "form"
    assert result["errors"] == {"base": "token_in_use"}
    assert _suggested(result, "gateway_token") == " token-other "
    assert flow.hass.config_entries.updates == []
    assert recorder.calls == []


def test_options_with_a_new_token_move_the_unique_id_to_it(probe):
    recorder = probe()
    entry = _entry(
        "entry-1", data={"gateway_token": "token-abc"}, unique_id="token-abc"
    )
    flow = _options_flow(entry)
    submitted = {"gateway_token": "token-new", "allow_support_actions": False}

    result = _run(flow.async_step_init(submitted))

    assert result["type"] == "create_entry"
    assert result["data"] == submitted
    assert flow.hass.config_entries.updates == [("entry-1", {"unique_id": "token-new"})]
    # The entry's own connection is live; the reload reports a bad token.
    assert recorder.calls == []


@pytest.mark.parametrize(
    ("data", "options"),
    [
        pytest.param({"gateway_token": "token-abc"}, {}, id="setup-token"),
        pytest.param(
            {"gateway_token": "token-old"},
            {"gateway_token": "token-abc"},
            id="options-token",
        ),
    ],
)
def test_options_with_the_same_token_leave_the_unique_id_alone(probe, data, options):
    recorder = probe()
    entry = _entry("entry-1", data=data, options=options, unique_id="token-abc")
    flow = _options_flow(entry)
    submitted = {"gateway_token": "token-abc", "allow_support_actions": True}

    result = _run(flow.async_step_init(submitted))

    assert result["type"] == "create_entry"
    assert flow.hass.config_entries.updates == []
    assert recorder.calls == []


# Setup


def _setup_flow(hass):
    flow = StaykeyConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    return flow


@pytest.mark.parametrize("outcome", ["invalid_auth", "cannot_connect"])
def test_setup_refuses_a_token_the_gateway_does_not_accept(probe, outcome):
    recorder = probe(outcome)
    submitted = {"gateway_token": " token-typo ", "gateway_url": GATEWAY_URL}

    result = _run(_setup_flow(_hass()).async_step_user(submitted))

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": outcome}
    # What was typed stays in the form, to correct.
    assert _suggested(result, "gateway_token") == " token-typo "
    assert _suggested(result, "gateway_url") == GATEWAY_URL
    assert recorder.calls == [(GATEWAY_URL, "token-typo")]


def test_setup_creates_the_entry_once_the_gateway_accepts_the_token(probe):
    recorder = probe("ok")
    flow = _setup_flow(_hass())

    result = _run(flow.async_step_user({"gateway_token": " token-abc "}))

    assert result["type"] == "create_entry"
    assert result["title"] == "Staykey"
    assert result["data"] == {"gateway_token": "token-abc"}
    assert flow.unique_id == "token-abc"
    assert recorder.calls == [(DEFAULT_GATEWAY_URL, "token-abc")]


def test_setup_refuses_an_empty_token_without_checking_it(probe):
    recorder = probe("ok")

    result = _run(_setup_flow(_hass()).async_step_user({"gateway_token": "   "}))

    assert result["errors"] == {"base": "gateway_token_required"}
    assert recorder.calls == []


def test_setup_of_a_token_already_set_up_stops_before_checking_it(probe):
    recorder = probe("ok")
    hass = _hass(
        _entry("entry-1", data={"gateway_token": "token-abc"}, unique_id="token-abc")
    )

    with pytest.raises(AbortFlow) as excinfo:
        _run(_setup_flow(hass).async_step_user({"gateway_token": "token-abc"}))

    assert excinfo.value.reason == "already_configured"
    assert recorder.calls == []


def test_setup_refuses_a_token_another_entry_uses_without_checking_it(probe):
    """An entry set up for webhook mode keeps its webhook URL as its unique
    ID, so only its options show that it uses the token."""
    recorder = probe("ok")
    hass = _hass(
        _entry(
            "entry-1",
            data={"endpoint_url": WEBHOOK_URL},
            options={"gateway_token": "token-abc"},
            unique_id=WEBHOOK_URL,
        )
    )

    result = _run(_setup_flow(hass).async_step_user({"gateway_token": "token-abc"}))

    assert result["type"] == "form"
    assert result["errors"] == {"base": "token_in_use"}
    assert recorder.calls == []


# Re-authentication


def _reauth_flow(hass, entry_id: str = "entry-1"):
    flow = StaykeyConfigFlow()
    flow.hass = hass
    flow.context = {"source": "reauth", "entry_id": entry_id}
    return flow


def test_reauth_asks_only_for_a_new_token():
    entry = _entry("entry-1", data={"gateway_token": "token-old"})

    result = _run(_reauth_flow(_hass(entry)).async_step_reauth(dict(entry.data)))

    assert result["type"] == "form"
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {}
    [key] = result["data_schema"].schema
    assert str(key) == "gateway_token"
    assert isinstance(key, vol.Required)
    assert result["data_schema"].schema[key] is str
    assert (key.description or {}).get("suggested_value") is None


def test_reauth_refuses_an_empty_token(probe):
    recorder = probe("ok")
    entry = _entry("entry-1", data={"gateway_token": "token-old"})

    result = _run(
        _reauth_flow(_hass(entry)).async_step_reauth_confirm({"gateway_token": " "})
    )

    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "gateway_token_required"}
    assert recorder.calls == []


@pytest.mark.parametrize(
    "other",
    [
        pytest.param(
            _entry("entry-2", data={"gateway_token": "token-other"}), id="in-data"
        ),
        pytest.param(
            _entry(
                "entry-2",
                data={"gateway_token": "token-first"},
                options={"gateway_token": "token-other"},
            ),
            id="in-options",
        ),
    ],
)
def test_reauth_refuses_a_token_another_entry_uses_without_checking_it(probe, other):
    recorder = probe("ok")
    entry = _entry("entry-1", data={"gateway_token": "token-old"})

    result = _run(
        _reauth_flow(_hass(entry, other)).async_step_reauth_confirm(
            {"gateway_token": "token-other"}
        )
    )

    assert result["errors"] == {"base": "token_in_use"}
    assert recorder.calls == []


@pytest.mark.parametrize("outcome", ["invalid_auth", "cannot_connect"])
def test_reauth_refuses_a_token_the_gateway_does_not_accept(probe, outcome):
    recorder = probe(outcome)
    entry = _entry(
        "entry-1",
        data={"gateway_token": "token-old", "gateway_url": "wss://setup.invalid/ws"},
        options={"gateway_url": GATEWAY_URL},
    )

    result = _run(
        _reauth_flow(_hass(entry)).async_step_reauth_confirm(
            {"gateway_token": " token-new "}
        )
    )

    assert result["type"] == "form"
    assert result["errors"] == {"base": outcome}
    # The entry's gateway URL: its options first, then its setup data.
    assert recorder.calls == [(GATEWAY_URL, "token-new")]


def test_reauth_saves_the_accepted_token_and_reloads(probe):
    recorder = probe("ok")
    entry = _entry(
        "entry-1",
        data={"gateway_token": "token-old", "gateway_url": GATEWAY_URL},
        options={"allow_support_actions": True},
        unique_id="token-old",
    )

    result = _run(
        _reauth_flow(_hass(entry)).async_step_reauth_confirm(
            {"gateway_token": " token-new "}
        )
    )

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert result["entry"] is entry
    assert result["changes"] == {
        "unique_id": "token-new",
        "data_updates": {"gateway_token": "token-new"},
        "options": {"allow_support_actions": True},
    }
    assert recorder.calls == [(GATEWAY_URL, "token-new")]


def test_reauth_replaces_a_token_saved_in_the_options(probe):
    recorder = probe("ok")
    entry = _entry(
        "entry-1",
        data={"gateway_token": "token-first"},
        options={"gateway_token": "token-old", "allow_support_actions": True},
        unique_id="token-first",
    )

    result = _run(
        _reauth_flow(_hass(entry)).async_step_reauth_confirm(
            {"gateway_token": "token-new"}
        )
    )

    assert result["changes"] == {
        "unique_id": "token-new",
        "data_updates": {"gateway_token": "token-new"},
        "options": {"gateway_token": "token-new", "allow_support_actions": True},
    }
    # No gateway URL saved: the default one.
    assert recorder.calls == [(DEFAULT_GATEWAY_URL, "token-new")]
