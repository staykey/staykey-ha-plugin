"""Tests for the ``tap_events`` passthrough.

Covers the input coercion / clamping rules and the event-serialization
path that turns HA's rich ``Event`` objects into JSON-friendly dicts.
The actual ``hass.bus.async_listen`` -> sleep -> unsubscribe loop is
exercised with a tiny fake bus to keep tests sync-free of a real HA
runtime.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest


def _ev(event_type: str, data, origin="LOCAL", time_fired=None, ctx=None):
    """Build a stand-in for ``homeassistant.core.Event`` for serializer tests."""
    return SimpleNamespace(
        event_type=event_type,
        data=data,
        origin=SimpleNamespace(value=origin) if isinstance(origin, str) else origin,
        time_fired=time_fired or dt.datetime(2026, 5, 6, 5, 30, tzinfo=dt.timezone.utc),
        context=ctx
        or SimpleNamespace(id="ctx-1", user_id=None, parent_id=None),
    )


# ---------------------------------------------------------------------------
# Input coercion
# ---------------------------------------------------------------------------


def test_coerce_duration_defaults_when_missing():
    from handlers.event_tap import _coerce_duration

    assert _coerce_duration(None) == 10.0


def test_coerce_duration_clamps_above_max():
    from handlers.event_tap import _coerce_duration

    # The max cap is 60s — anything larger should clamp, not reject.
    assert _coerce_duration(600) == 60.0


def test_coerce_duration_rejects_non_positive():
    from handlers.event_tap import _coerce_duration

    with pytest.raises(ValueError):
        _coerce_duration(0)
    with pytest.raises(ValueError):
        _coerce_duration(-1)


def test_coerce_duration_rejects_non_numeric():
    from handlers.event_tap import _coerce_duration

    with pytest.raises(ValueError):
        _coerce_duration("forever")


def test_coerce_max_events_defaults_when_missing():
    from handlers.event_tap import _coerce_max_events

    assert _coerce_max_events(None) == 200


def test_coerce_max_events_clamps_to_hard_max():
    from handlers.event_tap import _coerce_max_events

    assert _coerce_max_events(10_000) == 500


def test_coerce_max_events_rejects_bool_even_though_python_treats_it_as_int():
    from handlers.event_tap import _coerce_max_events

    # Catches the ``isinstance(True, int) is True`` footgun.
    with pytest.raises(ValueError):
        _coerce_max_events(True)


def test_coerce_max_events_rejects_non_positive():
    from handlers.event_tap import _coerce_max_events

    with pytest.raises(ValueError):
        _coerce_max_events(0)


def test_coerce_event_types_returns_none_for_empty_or_missing():
    from handlers.event_tap import _coerce_event_types

    assert _coerce_event_types(None) is None
    assert _coerce_event_types([]) is None


def test_coerce_event_types_validates_entries():
    from handlers.event_tap import _coerce_event_types

    assert _coerce_event_types(["state_changed", "matter_event"]) == [
        "state_changed",
        "matter_event",
    ]
    with pytest.raises(ValueError):
        _coerce_event_types([""])
    with pytest.raises(ValueError):
        _coerce_event_types([None])
    with pytest.raises(ValueError):
        _coerce_event_types("state_changed")  # not a list


# ---------------------------------------------------------------------------
# Event serialization
# ---------------------------------------------------------------------------


def test_serialize_event_unwraps_primitives_and_metadata():
    from handlers.event_tap import _serialize_event

    payload = _serialize_event(
        _ev(
            "state_changed",
            data={"entity_id": "lock.foo", "new_state": "locked"},
        )
    )
    assert payload["event_type"] == "state_changed"
    assert payload["origin"] == "LOCAL"
    assert payload["data"] == {"entity_id": "lock.foo", "new_state": "locked"}
    assert payload["context"] == {"id": "ctx-1", "user_id": None, "parent_id": None}
    # Datetime is rendered as ISO 8601, UTC.
    assert payload["time_fired"].startswith("2026-05-06T05:30:00")


def test_serialize_event_handles_missing_optional_fields():
    from handlers.event_tap import _serialize_event

    payload = _serialize_event(
        SimpleNamespace(
            event_type="x",
            data=None,
            origin=None,
            time_fired=None,
            context=None,
        )
    )
    assert payload == {
        "event_type": "x",
        "time_fired": None,
        "origin": None,
        "context": None,
        "data": None,
    }


def test_make_serializable_unwraps_as_dict_objects():
    from handlers.event_tap import _make_serializable

    state_like = SimpleNamespace(
        as_dict=lambda: {
            "state": "unlocked",
            "attributes": {"changed_by": "User 1"},
            "last_changed": dt.datetime(2026, 5, 6, 5, 30, tzinfo=dt.timezone.utc),
        }
    )
    out = _make_serializable({"new_state": state_like})
    assert out == {
        "new_state": {
            "state": "unlocked",
            "attributes": {"changed_by": "User 1"},
            "last_changed": "2026-05-06T05:30:00+00:00",
        }
    }


def test_make_serializable_falls_back_to_str_for_unknown_objects():
    from handlers.event_tap import _make_serializable

    class Weird:
        def __repr__(self):
            return "<weird>"

    assert _make_serializable(Weird()) == "<weird>"


# ---------------------------------------------------------------------------
# End-to-end loop with a fake bus
# ---------------------------------------------------------------------------


class _FakeBus:
    """Minimal stand-in for ``hass.bus`` exposing async_listen."""

    def __init__(self):
        self._subs: dict = {}

    def async_listen(self, event_type, callback):
        self._subs.setdefault(event_type, []).append(callback)

        def _unsub():
            self._subs[event_type].remove(callback)

        return _unsub

    def fire(self, event):
        for et in (event.event_type, "*"):
            for cb in list(self._subs.get(et, [])):
                cb(event)


def test_tap_events_returns_captured_events_during_window(monkeypatch):
    from handlers import event_tap

    bus = _FakeBus()
    hass = SimpleNamespace(bus=bus)

    async def _run():
        # Speed up the test: replace asyncio.sleep so we don't actually wait.
        async def _instant_sleep(_seconds):
            # Fire a couple of events synchronously while the listener
            # is registered, then return.
            bus.fire(_ev("state_changed", {"entity_id": "lock.foo"}))
            bus.fire(_ev("matter_event", {"node_id": 1, "user_index": 1}))
            bus.fire(_ev("state_changed", {"entity_id": "lock.bar"}))

        monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
        return await event_tap.handle_tap_events(
            hass,
            None,
            {"duration_seconds": 1, "max_events": 10},
        )

    out = asyncio.run(_run())
    assert out["captured"] == 3
    assert out["truncated"] is False
    assert {e["event_type"] for e in out["events"]} == {
        "state_changed",
        "matter_event",
    }


def test_tap_events_filters_by_entity_id(monkeypatch):
    from handlers import event_tap

    bus = _FakeBus()
    hass = SimpleNamespace(bus=bus)

    async def _run():
        async def _instant_sleep(_seconds):
            bus.fire(_ev("state_changed", {"entity_id": "lock.foo"}))
            bus.fire(_ev("state_changed", {"entity_id": "lock.bar"}))
            bus.fire(_ev("state_changed", {"entity_id": "lock.foo"}))

        monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
        return await event_tap.handle_tap_events(
            hass,
            None,
            {
                "event_types": ["state_changed"],
                "entity_id": "lock.foo",
                "duration_seconds": 1,
            },
        )

    out = asyncio.run(_run())
    assert out["captured"] == 2
    assert all(e["data"]["entity_id"] == "lock.foo" for e in out["events"])


def test_tap_events_truncates_at_max_events(monkeypatch):
    from handlers import event_tap

    bus = _FakeBus()
    hass = SimpleNamespace(bus=bus)

    async def _run():
        async def _instant_sleep(_seconds):
            for i in range(5):
                bus.fire(_ev("state_changed", {"entity_id": f"lock.{i}"}))

        monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
        return await event_tap.handle_tap_events(
            hass,
            None,
            {"duration_seconds": 1, "max_events": 3},
        )

    out = asyncio.run(_run())
    assert out["captured"] == 3
    assert out["truncated"] is True


def test_tap_events_subscribes_to_wildcard_when_event_types_omitted(monkeypatch):
    from handlers import event_tap

    bus = _FakeBus()
    hass = SimpleNamespace(bus=bus)

    async def _run():
        async def _instant_sleep(_seconds):
            bus.fire(_ev("anything_at_all", {"foo": "bar"}))

        monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
        return await event_tap.handle_tap_events(hass, None, {"duration_seconds": 1})

    out = asyncio.run(_run())
    assert out["subscribed_event_types"] == "*"
    assert out["captured"] == 1
    assert out["events"][0]["event_type"] == "anything_at_all"
