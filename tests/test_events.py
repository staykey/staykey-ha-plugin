"""Tests for the gateway payload builders in ``events``."""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "custom_components" / "staykey")
)
from events import device_event_payload, state_update_payload  # noqa: E402


def _zwave_event(**overrides):
    data = {
        "device_id": "ha-dev-1",
        "command_class": 113,
        "command_class_name": "Notification",
        "type": 6,
        "event": 6,
        "event_label": "Keypad unlock operation",
        "parameters": {"userId": 3},
    }
    data.update(overrides.pop("data", {}))
    fields = {
        "event_type": "zwave_js_notification",
        "data": data,
        "time_fired": dt.datetime(
            2026, 9, 15, 15, 42, 0, 250000, tzinfo=dt.timezone.utc
        ),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class TestDeviceEventPayload:
    def test_carries_the_raw_notification(self):
        payload = device_event_payload(_zwave_event(), "sk-device-1")

        assert uuid.UUID(payload["event_id"])
        assert payload["device_id"] == "sk-device-1"
        assert payload["protocol"] == "zwave"
        assert payload["source_event"] == "zwave_js_notification"
        assert payload["notification_type"] == 6
        assert payload["code"] == 6
        assert payload["label"] == "Keypad unlock operation"
        assert payload["params"] == {"userId": 3}
        assert payload["timestamp"] == "2026-09-15T15:42:00.250000Z"

    def test_carries_the_raw_event_data(self):
        ev = _zwave_event()
        payload = device_event_payload(ev, "sk")

        assert payload["data"] == ev.data
        assert payload["data"] is not ev.data

    def test_raw_data_survives_events_without_notification_fields(self):
        ev = _zwave_event(
            event_type="zwave_js_value_notification",
            data={
                "type": None,
                "event": None,
                "property": "scene",
                "value": 3,
            },
        )
        payload = device_event_payload(ev, "sk")

        assert payload["data"]["property"] == "scene"
        assert payload["data"]["value"] == 3

    def test_each_call_gets_a_fresh_event_id(self):
        ev = _zwave_event()
        a = device_event_payload(ev, "sk")["event_id"]
        b = device_event_payload(ev, "sk")["event_id"]
        assert a != b

    def test_missing_fields_are_tolerated(self):
        ev = _zwave_event(
            time_fired=None, data={"parameters": None, "event_label": None}
        )
        payload = device_event_payload(ev, "sk")
        assert payload["timestamp"] is None
        assert payload["params"] == {}
        assert payload["label"] is None

    def test_non_utc_time_is_converted(self):
        tz = dt.timezone(dt.timedelta(hours=-6))
        ev = _zwave_event(time_fired=dt.datetime(2026, 9, 15, 9, 42, tzinfo=tz))
        assert device_event_payload(ev, "sk")["timestamp"] == "2026-09-15T15:42:00Z"

    def test_naive_time_is_treated_as_utc(self):
        ev = _zwave_event(time_fired=dt.datetime(2026, 9, 15, 15, 42))
        assert device_event_payload(ev, "sk")["timestamp"] == "2026-09-15T15:42:00Z"

    def test_value_notification_events_keep_their_source(self):
        ev = _zwave_event(
            event_type="zwave_js_value_notification", data={"type": None, "event": None}
        )
        payload = device_event_payload(ev, "sk")
        assert payload["source_event"] == "zwave_js_value_notification"
        assert payload["notification_type"] is None and payload["code"] is None


class TestStateUpdatePayload:
    def _state(self, attributes=None, ctx_id="ctx-1"):
        return SimpleNamespace(
            state="unlocked",
            attributes=attributes or {},
            last_changed=dt.datetime(2026, 9, 15, 15, 42, tzinfo=dt.timezone.utc),
            context=SimpleNamespace(id=ctx_id),
        )

    def test_carries_state_time_and_context_id(self):
        payload = state_update_payload(self._state())
        assert payload["state"] == "unlocked"
        assert payload["last_changed"] == "2026-09-15T15:42:00Z"
        assert payload["context_id"] == "ctx-1"
        assert uuid.UUID(payload["event_id"])
        assert set(payload) == {"state", "last_changed", "event_id", "context_id"}

    def test_each_delivery_gets_a_fresh_event_id(self):
        state = self._state()
        a = state_update_payload(state)["event_id"]
        b = state_update_payload(state)["event_id"]
        assert a != b

    def test_event_id_is_not_the_context_id(self):
        payload = state_update_payload(self._state())
        assert payload["event_id"] != payload["context_id"]

    def test_naive_last_changed_is_treated_as_utc(self):
        state = self._state()
        state.last_changed = dt.datetime(2026, 9, 15, 15, 42)
        assert state_update_payload(state)["last_changed"] == "2026-09-15T15:42:00Z"

    def test_missing_last_changed_yields_none(self):
        state = self._state()
        state.last_changed = None
        assert state_update_payload(state)["last_changed"] is None

    def test_battery_only_when_present(self):
        assert "battery_level" not in state_update_payload(self._state())
        assert (
            state_update_payload(self._state({"battery_level": 40}))["battery_level"]
            == 40
        )

    def test_missing_context_yields_no_context_id(self):
        state = self._state()
        state.context = None
        payload = state_update_payload(state)
        assert payload["context_id"] is None
        assert uuid.UUID(payload["event_id"])
