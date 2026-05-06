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


def _register_credential_status(
    hass: _FakeHass,
    *,
    entity_id: str = "lock.front_door",
    exists: bool,
    user_index: int | None = None,
) -> None:
    """Pre-register a ``matter.get_lock_credential_status`` response.

    The provider always pre-flights the slot to choose Add vs Modify;
    every set_code / clear_code test needs this stub registered.
    """
    hass.services.register(
        "matter",
        "get_lock_credential_status",
        {
            entity_id: {
                "credential_exists": exists,
                "user_index": user_index,
                "next_credential_index": None,
            }
        },
    )


def test_matter_set_code_add_path_passes_user_type_and_omits_user_index():
    """Bolt SE-compatible Add path (slot empty):

    * ``credential_index`` = our slot
    * ``user_index`` is **not** sent (null on the wire) so the lock
      auto-allocates a fresh user.
    * ``user_type`` = ``unrestricted_user`` describes that new user.
    * ``user_status`` is **not** sent (lock defaults to
      ``kOccupiedEnabled``).

    This is the shape the HA Matter Lock Manager UI uses and the only
    one Bolt SE accepts on Add.
    """
    from services.providers.matter import MatterLockProvider

    hass = _FakeHass()
    _register_credential_status(hass, exists=False)
    hass.services.register(
        "matter",
        "set_lock_credential",
        {
            "lock.front_door": {
                "credential_index": 7,
                "user_index": 42,  # lock-allocated, not == slot
                "next_credential_index": 8,
            }
        },
    )

    provider = MatterLockProvider()
    result = _run(provider.set_code(hass, "lock.front_door", 7, "1234"))

    set_call = next(c for c in hass.services.calls if c[1] == "set_lock_credential")
    set_data = set_call[2]
    assert set_data["credential_index"] == 7
    assert set_data["credential_data"] == "1234"
    assert set_data.get("user_type") == "unrestricted_user", (
        "Add path must send user_type so the lock auto-creates a "
        "non-restricted user"
    )
    assert "user_index" not in set_data, (
        "Add path must omit user_index (null on the wire) so the lock "
        "auto-allocates a fresh user — Bolt SE rejects Add when "
        "userIndex is non-null and refers to a missing user"
    )
    assert "user_status" not in set_data, (
        "user_status must be omitted; the lock defaults to kOccupiedEnabled"
    )

    assert result.verified is True
    assert result.method == "matter_set_credential"
    assert result.extra["operation"] == "add"
    assert result.extra["user_index"] == 42


def test_matter_set_code_modify_path_passes_existing_user_index_and_omits_user_type():
    """Modify path (slot occupied):

    * ``credential_index`` = our slot
    * ``user_index`` = the existing user pulled from
      ``get_lock_credential_status`` (so the lock keeps the existing
      user-credential relationship).
    * ``user_type`` and ``user_status`` are **both** omitted — chip
      SDK validity check requires them null when userIndex is non-null
      on Modify; sending either yields ``InvalidField`` → 0x85.
    """
    from services.providers.matter import MatterLockProvider

    hass = _FakeHass()
    _register_credential_status(hass, exists=True, user_index=42)
    hass.services.register(
        "matter",
        "set_lock_credential",
        {
            "lock.front_door": {
                "credential_index": 7,
                "user_index": 42,
                "next_credential_index": 8,
            }
        },
    )

    provider = MatterLockProvider()
    result = _run(provider.set_code(hass, "lock.front_door", 7, "1234"))

    set_call = next(c for c in hass.services.calls if c[1] == "set_lock_credential")
    set_data = set_call[2]
    assert set_data["credential_index"] == 7
    assert set_data["user_index"] == 42, (
        "Modify path must send the existing user_index from "
        "get_lock_credential_status"
    )
    assert "user_type" not in set_data, (
        "user_type must be omitted on Modify; chip SDK validity check "
        "rejects with InvalidField → 0x85 if non-null"
    )
    assert "user_status" not in set_data
    assert result.verified is True
    assert result.extra["operation"] == "modify"


def test_matter_set_code_treats_duplicate_status_as_verified():
    from homeassistant.exceptions import HomeAssistantError

    from services.providers.matter import MatterLockProvider

    hass = _FakeHass()
    _register_credential_status(hass, exists=False)
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
    _register_credential_status(hass, exists=False)
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
    assert result.extra.get("matter_status") == "occupied"
    assert "matter_status=occupied" in (result.error or "")


def test_matter_set_code_surfaces_unknown_im_status_in_extra_and_error():
    """Reproduction of the Bolt SE rejection: HA renders IM-level
    status codes as ``unknown(<int>)`` because they aren't in
    ``SET_CREDENTIAL_STATUS_MAP``.  We must surface that verbatim so
    Orion's activity log shows the actual lock status.
    """
    from homeassistant.exceptions import HomeAssistantError

    from services.providers.matter import MatterLockProvider

    hass = _FakeHass()
    _register_credential_status(hass, exists=False)
    hass.services.register(
        "matter",
        "set_lock_credential",
        HomeAssistantError(
            "Failed to set credential: lock returned status `unknown(133)`.",
            translation_placeholders={"status": "unknown(133)"},
        ),
    )

    provider = MatterLockProvider()
    result = _run(provider.set_code(hass, "lock.front_door", 10, "5678"))

    assert result.verified is False
    assert result.extra.get("matter_status") == "unknown(133)"
    assert "matter_status=unknown(133)" in (result.error or "")


def test_matter_set_code_does_not_call_set_lock_user():
    """Regression guard: ``set_lock_user`` is structurally hostile to
    slot-based callers (raises UserSlotEmptyError on caller-supplied
    indices for empty slots).  We always go through
    ``set_lock_credential`` for both Add and Modify.
    """
    from services.providers.matter import MatterLockProvider

    hass = _FakeHass()
    _register_credential_status(hass, exists=False)
    hass.services.register(
        "matter",
        "set_lock_credential",
        {
            "lock.front_door": {
                "credential_index": 1,
                "user_index": 99,
                "next_credential_index": 2,
            }
        },
    )

    provider = MatterLockProvider()
    _run(provider.set_code(hass, "lock.front_door", 1, "9999"))

    services_called = {(d, s) for d, s, _ in hass.services.calls}
    assert ("matter", "set_lock_user") not in services_called


# ---------------------------------------------------------------------------
# Matter provider — clear_code (looks up user_index, then ClearUser)
# ---------------------------------------------------------------------------


def test_matter_clear_code_uses_user_index_from_credential_status():
    """``clear_code`` must read the user_index back from the lock,
    *not* assume slot == user_index, because the Add path leaves the
    user_index lock-allocated.
    """
    from services.providers.matter import MatterLockProvider

    hass = _FakeHass()
    _register_credential_status(hass, exists=True, user_index=42)
    hass.services.register("matter", "clear_lock_user", {})

    provider = MatterLockProvider()
    result = _run(provider.clear_code(hass, "lock.front_door", 7))

    clear_call = next(c for c in hass.services.calls if c[1] == "clear_lock_user")
    assert clear_call[2]["user_index"] == 42, (
        "ClearUser must use the existing user_index from "
        "get_lock_credential_status, not the slot number"
    )
    assert result.verified is True
    assert result.method == "matter_clear_user"
    assert result.extra["user_index"] == 42


def test_matter_clear_code_returns_verified_no_op_when_already_empty():
    """Important for Oban retries — clearing an already-empty slot
    must not surface as a failure.
    """
    from services.providers.matter import MatterLockProvider

    hass = _FakeHass()
    _register_credential_status(hass, exists=False)

    provider = MatterLockProvider()
    result = _run(provider.clear_code(hass, "lock.front_door", 7))

    services_called = {s for _, s, _ in hass.services.calls}
    assert "clear_lock_user" not in services_called, (
        "Already-empty slot must not call clear_lock_user"
    )
    assert result.verified is True
    assert result.method == "matter_clear_already_empty"


def test_matter_clear_code_falls_back_to_clear_credential_for_orphan():
    """Defensive: a credential that exists with no associated
    user_index is an orphan (shouldn't happen via our own writes).
    Fall back to clear_lock_credential which removes just the cred.
    """
    from services.providers.matter import MatterLockProvider

    hass = _FakeHass()
    _register_credential_status(hass, exists=True, user_index=None)
    hass.services.register("matter", "clear_lock_credential", {})

    provider = MatterLockProvider()
    result = _run(provider.clear_code(hass, "lock.front_door", 7))

    clear_call = next(
        c for c in hass.services.calls if c[1] == "clear_lock_credential"
    )
    assert clear_call[2]["credential_index"] == 7
    assert clear_call[2]["credential_type"] == "pin"
    assert result.verified is True
    assert result.method == "matter_clear_credential"
    assert result.extra.get("orphan") is True
