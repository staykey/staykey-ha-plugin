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

    assert init["data"]["allow_support_actions"] == "Allow support actions"
    assert (
        "this integration refuses troubleshooting commands"
        in init["data_description"]["allow_support_actions"]
    )


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
