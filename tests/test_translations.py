"""The integration's UI strings: ``strings.json`` and its English translation."""

from __future__ import annotations

import json
from pathlib import Path

STAYKEY = Path(__file__).resolve().parents[1] / "custom_components" / "staykey"


def _load(name: str) -> dict:
    return json.loads((STAYKEY / name).read_text(encoding="utf-8"))


def test_english_translation_matches_strings():
    assert _load("translations/en.json") == _load("strings.json")


def test_support_actions_option_is_labelled_and_explained():
    init = _load("strings.json")["options"]["step"]["init"]

    assert init["data"]["allow_support_actions"] == "Allow support access"
    description = init["data_description"]["allow_support_actions"]
    assert "Lets Staykey support run debugging commands" in description
    assert "Leave it off unless support asks." in description


def test_setup_failure_reason_is_explained():
    message = _load("strings.json")["exceptions"]["gateway_token_required"]["message"]

    assert "gateway token" in message


def test_no_webhook_strings_remain():
    strings = _load("strings.json")

    assert "issues" not in strings
    assert set(strings["options"]["step"]["init"]["data"]) == {
        "gateway_token",
        "gateway_url",
        "allow_support_actions",
    }


def test_token_check_errors_are_worded():
    config = _load("strings.json")["config"]

    assert "didn't accept this gateway token" in config["error"]["invalid_auth"]
    assert "Couldn't reach the Staykey gateway" in config["error"]["cannot_connect"]
    assert "already uses this gateway token" in config["error"]["token_in_use"]
    # The existing error stays.
    assert "gateway_token_required" in config["error"]


def test_reauth_form_and_outcome_are_worded():
    config = _load("strings.json")["config"]
    reauth = config["step"]["reauth_confirm"]

    assert reauth["title"] == "Re-authenticate Staykey"
    # The prompt can open while Staykey can't check tokens, and then closes
    # by itself, so it doesn't claim the token is no longer valid.
    assert reauth["description"] == (
        "Staykey didn't accept this entry's gateway token. Paste the current"
        " token from your Staykey dashboard (Properties > Integrations > Home"
        " Assistant). If you haven't changed it, this prompt closes on its own"
        " once Staykey accepts the token again."
    )
    assert reauth["data"] == {"gateway_token": "Gateway Token"}
    assert "Staykey is reconnecting" in config["abort"]["reauth_successful"]


def test_re_entering_the_same_token_is_worded():
    abort = _load("strings.json")["config"]["abort"]

    assert abort["reauth_retry"] == (
        "Staykey is trying this gateway token again. If it still isn't"
        " accepted, Home Assistant will ask again."
    )


def test_options_word_a_token_in_use_like_setup():
    strings = _load("strings.json")

    assert (
        strings["options"]["error"]["token_in_use"]
        == strings["config"]["error"]["token_in_use"]
    )


def test_a_disabled_integration_is_worded():
    error = _load("strings.json")["config"]["error"]

    assert error["integration_disabled"] == (
        "This integration is turned off in your Staykey dashboard. "
        "Turn it on there, then try again."
    )
    # The errors the forms already had stay.
    assert {
        "gateway_token_required",
        "invalid_auth",
        "cannot_connect",
        "token_in_use",
    } <= set(error)
