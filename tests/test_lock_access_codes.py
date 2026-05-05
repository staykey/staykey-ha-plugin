"""Tests for the LockProvider abstraction and Matter provider helpers.

These tests exercise pure helpers that don't require a running Home
Assistant instance — handler routing, response unwrapping, and the
device-discovery capability heuristic for Matter vs. Z-Wave.
"""

from __future__ import annotations

# HA module stubs and sys.path setup live in conftest.py.


def test_provider_result_default_extra_is_independent_per_instance():
    from services.lock_provider import ProviderResult

    a = ProviderResult(slot=1, method="x", verified=True)
    b = ProviderResult(slot=2, method="y", verified=False)
    a.extra["k"] = "v"
    assert b.extra == {}, "default factory must give each instance its own dict"


def test_slot_info_basic_shape():
    from services.lock_provider import SlotInfo

    s = SlotInfo(slot=3, occupied=True, code="1234")
    assert s.slot == 3
    assert s.occupied is True
    assert s.code == "1234"


def test_capability_info_defaults():
    from services.lock_provider import CapabilityInfo

    c = CapabilityInfo(supports_access_codes=True)
    assert c.max_slots is None
    assert c.extra == {}


# ---------------------------------------------------------------------------
# Matter provider — response unwrapping
# ---------------------------------------------------------------------------


def test_matter_extract_entity_response_returns_per_entity_when_present():
    from services.providers.matter import _extract_entity_response

    response = {
        "lock.front_door": {
            "credential_index": 9,
            "user_index": 9,
            "next_credential_index": 10,
        }
    }
    assert _extract_entity_response(response, "lock.front_door") == {
        "credential_index": 9,
        "user_index": 9,
        "next_credential_index": 10,
    }


def test_matter_extract_entity_response_falls_back_to_top_level_when_unkeyed():
    from services.providers.matter import _extract_entity_response

    response = {"credential_index": 9, "user_index": 9}
    assert _extract_entity_response(response, "lock.front_door") == response


def test_matter_extract_entity_response_handles_none():
    from services.providers.matter import _extract_entity_response

    assert _extract_entity_response(None, "lock.front_door") is None


# ---------------------------------------------------------------------------
# Device discovery — Matter access-code heuristic
# ---------------------------------------------------------------------------


def test_lock_supports_access_codes_matter_always_true():
    from lock_capability_heuristics import lock_supports_access_codes

    # Matter locks: trust the integration registration; supported_features
    # bit 1 is set independently for unbolt support, not credentials.
    assert lock_supports_access_codes({"supported_features": 0}, "matter") is True
    assert lock_supports_access_codes({}, "matter") is True


def test_lock_supports_access_codes_zwave_uses_supported_features_bit():
    from lock_capability_heuristics import lock_supports_access_codes

    assert lock_supports_access_codes({"supported_features": 1}, "zwave_js") is True
    assert lock_supports_access_codes({"supported_features": 0}, "zwave_js") is False


def test_lock_supports_access_codes_unknown_protocol_falls_back_to_heuristic():
    from lock_capability_heuristics import lock_supports_access_codes

    assert lock_supports_access_codes({"supported_features": 1}, None) is True
    assert lock_supports_access_codes({}, "wifi") is False


# ---------------------------------------------------------------------------
# Matter provider — duplicate-status detection
# ---------------------------------------------------------------------------


def test_is_duplicate_credential_error_via_translation_placeholders():
    from homeassistant.exceptions import HomeAssistantError

    from services.providers.matter import _is_duplicate_credential_error

    exc = HomeAssistantError(
        "Failed to set credential: lock returned status `duplicate`.",
        translation_domain="matter",
        translation_key="set_credential_failed",
        translation_placeholders={"status": "duplicate"},
    )
    assert _is_duplicate_credential_error(exc) is True


def test_is_duplicate_credential_error_via_message_substring():
    from homeassistant.exceptions import HomeAssistantError

    from services.providers.matter import _is_duplicate_credential_error

    exc = HomeAssistantError(
        "Failed to set credential: lock returned status `duplicate`."
    )
    assert _is_duplicate_credential_error(exc) is True


def test_is_duplicate_credential_error_false_for_other_statuses():
    from homeassistant.exceptions import HomeAssistantError

    from services.providers.matter import _is_duplicate_credential_error

    exc = HomeAssistantError(
        "Failed to set credential: lock returned status `occupied`.",
        translation_placeholders={"status": "occupied"},
    )
    assert _is_duplicate_credential_error(exc) is False

    other = HomeAssistantError("Some unrelated transport failure")
    assert _is_duplicate_credential_error(other) is False


# ---------------------------------------------------------------------------
# Matter provider — set_code happy path + duplicate handling
# ---------------------------------------------------------------------------


class _RecordingServices:
    """Minimal hass.services stand-in that records calls and lets the
    test queue per-call return values or exceptions.
    """

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self._handlers: dict[tuple[str, str], object] = {}

    def register(self, domain, service, handler):
        self._handlers[(domain, service)] = handler

    async def async_call(
        self, domain, service, data, *, blocking=True, return_response=False
    ):
        self.calls.append((domain, service, dict(data)))
        handler = self._handlers.get((domain, service))
        if handler is None:
            raise AssertionError(
                f"unexpected service call {domain}.{service} with {data!r}"
            )
        if isinstance(handler, BaseException):
            raise handler
        if callable(handler):
            return handler(data)
        return handler


class _FakeHass:
    def __init__(self):
        self.services = _RecordingServices()


def _run(coro):
    import asyncio

    return asyncio.new_event_loop().run_until_complete(coro)


def test_matter_set_code_calls_only_set_lock_credential_on_happy_path():
    from services.providers.matter import MatterLockProvider

    hass = _FakeHass()
    hass.services.register(
        "matter",
        "set_lock_credential",
        {
            "lock.front_door": {
                "credential_index": 7,
                "user_index": 7,
                "next_credential_index": 8,
            }
        },
    )

    provider = MatterLockProvider()
    result = _run(provider.set_code(hass, "lock.front_door", 7, "1234"))

    assert [(d, s) for d, s, _ in hass.services.calls] == [
        ("matter", "set_lock_credential")
    ]
    set_call_data = hass.services.calls[0][2]
    assert set_call_data["credential_index"] == 7
    assert set_call_data["user_index"] == 7
    assert set_call_data["credential_data"] == "1234"

    assert result.verified is True
    assert result.method == "matter_set_credential"
    assert result.extra["credential_index"] == 7


def test_matter_set_code_treats_duplicate_status_as_verified():
    from homeassistant.exceptions import HomeAssistantError

    from services.providers.matter import MatterLockProvider

    hass = _FakeHass()
    hass.services.register(
        "matter",
        "set_lock_credential",
        HomeAssistantError(
            "Failed to set credential: lock returned status `duplicate`.",
            translation_placeholders={"status": "duplicate"},
        ),
    )

    provider = MatterLockProvider()
    result = _run(provider.set_code(hass, "lock.front_door", 7, "1234"))

    assert result.verified is True
    assert result.method == "matter_set_credential_duplicate"
    assert result.extra.get("status") == "duplicate"
    assert result.error is None


def test_matter_set_code_surfaces_non_duplicate_error_as_failure():
    from homeassistant.exceptions import HomeAssistantError

    from services.providers.matter import MatterLockProvider

    hass = _FakeHass()
    hass.services.register(
        "matter",
        "set_lock_credential",
        HomeAssistantError(
            "Failed to set credential: lock returned status `occupied`.",
            translation_placeholders={"status": "occupied"},
        ),
    )

    provider = MatterLockProvider()
    result = _run(provider.set_code(hass, "lock.front_door", 7, "1234"))

    assert result.verified is False
    assert "set_lock_credential" in (result.error or "")


def test_matter_set_code_does_not_call_set_lock_user():
    """Regression guard: dropping ``set_lock_user`` is what unblocked
    the Bolt SE; this test fails loudly if anyone reintroduces it.
    """
    from services.providers.matter import MatterLockProvider

    hass = _FakeHass()
    hass.services.register(
        "matter",
        "set_lock_credential",
        {
            "lock.front_door": {
                "credential_index": 1,
                "user_index": 1,
                "next_credential_index": 2,
            }
        },
    )

    provider = MatterLockProvider()
    _run(provider.set_code(hass, "lock.front_door", 1, "9999"))

    services_called = {(d, s) for d, s, _ in hass.services.calls}
    assert ("matter", "set_lock_user") not in services_called
