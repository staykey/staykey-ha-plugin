"""Tests for the gateway state-streaming listener.

``async_setup_entry`` registers a ``state_changed`` listener that decides
which updates reach the gateway.  These tests drive that listener directly,
through a fake bus and a recording gateway client, so the decision is
exercised the way Home Assistant delivers it: one event at a time, with
nothing remembered in between.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))
import staykey  # noqa: E402
from staykey.const import CONF_GATEWAY_TOKEN, DOMAIN  # noqa: E402

CLIMATE = "climate.hallway"
COVER = "cover.garage_door"
LOCK = "lock.front_door"

DEVICES = [
    {"device_id": "sk-climate", "external_id": CLIMATE},
    {"device_id": "sk-cover", "external_id": COVER},
    {"device_id": "sk-lock", "external_id": LOCK},
]


def _run(coro):
    return asyncio.run(coro)


def _state(state_value: str, **attributes):
    """Build a stand-in for ``homeassistant.core.State``."""
    return SimpleNamespace(
        state=state_value,
        attributes=attributes,
        last_changed=dt.datetime(2026, 9, 15, 15, 42, tzinfo=dt.timezone.utc),
        context=SimpleNamespace(id="ctx-1"),
    )


def _state_changed(entity_id: str, new_state, old_state):
    """Build a stand-in for a Home Assistant ``state_changed`` event."""
    return SimpleNamespace(
        event_type="state_changed",
        data={
            "entity_id": entity_id,
            "new_state": new_state,
            "old_state": old_state,
        },
    )


class _RecordingGatewayClient:
    """Stands in for ``GatewayClient`` and records what it was asked to send."""

    def __init__(self, **kwargs):
        self.device_map = kwargs.get("device_map")
        self.connected = True
        self.state_updates: list[tuple[str, dict]] = []
        self.health_alerts: list[tuple[str, dict]] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send_state_update(self, device_id, payload):
        self.state_updates.append((device_id, payload))

    async def send_health_alert(self, alert_type, payload):
        self.health_alerts.append((alert_type, payload))


class _FakeBus:
    def __init__(self):
        self.listeners: dict[str, list] = {}

    def async_listen(self, event_type, callback):
        self.listeners.setdefault(event_type, []).append(callback)

        def _unsub():
            self.listeners[event_type].remove(callback)

        return _unsub


class _FakeEntry:
    """Stands in for a gateway-mode ``ConfigEntry``."""

    def __init__(self, entry_id: str = "entry-1"):
        self.entry_id = entry_id
        self.data = {CONF_GATEWAY_TOKEN: "token-abc"}
        self.options: dict = {}

    def add_update_listener(self, _listener):
        return lambda: None

    def async_on_unload(self, _unsub):
        return None


def _set_up_gateway_entry(monkeypatch, entry_id: str = "entry-1"):
    """Run ``async_setup_entry`` in gateway mode against fakes.

    Returns the recording gateway client, a callable that fires one event at
    the registered ``state_changed`` listener, and the entry's stored data.
    """
    client = _RecordingGatewayClient()
    monkeypatch.setattr(staykey, "GatewayClient", lambda **kwargs: client)

    bus = _FakeBus()
    hass = SimpleNamespace(bus=bus, data={})
    entry = _FakeEntry(entry_id)

    assert _run(staykey.async_setup_entry(hass, entry)) is True

    store = hass.data[DOMAIN][entry.entry_id]
    store["device_map"].load_sync(DEVICES)

    listeners = bus.listeners["state_changed"]
    assert len(listeners) == 1

    def fire(event):
        _run(listeners[0](event))

    return client, fire, store


class TestStateForwarding:
    """What reaches the gateway for a given ``state_changed`` event."""

    def test_attribute_only_change_is_not_forwarded(self, monkeypatch):
        client, fire, _store = _set_up_gateway_entry(monkeypatch)

        # An HVAC cycle: the mode (the entity's state) is unchanged and only
        # hvac_action moved. Nothing is primed beforehand, which is the state
        # the integration is in right after a restart or an entry reload.
        fire(
            _state_changed(
                CLIMATE,
                _state("heat_cool", hvac_action="cooling"),
                _state("heat_cool", hvac_action="idle"),
            )
        )

        assert client.state_updates == []

    def test_state_change_is_forwarded(self, monkeypatch):
        client, fire, _store = _set_up_gateway_entry(monkeypatch)

        fire(
            _state_changed(
                CLIMATE,
                _state("heat_cool", hvac_action="idle"),
                _state("off"),
            )
        )

        assert len(client.state_updates) == 1
        device_id, payload = client.state_updates[0]
        assert device_id == "sk-climate"
        assert payload["state"] == "heat_cool"

    def test_restored_state_is_not_forwarded(self, monkeypatch):
        client, fire, _store = _set_up_gateway_entry(monkeypatch)

        # A missing old_state means HA is restoring the entity, not reporting
        # a change; the restart itself is signalled by homeassistant_started.
        fire(_state_changed(CLIMATE, _state("heat_cool"), None))

        assert client.state_updates == []

    def test_repeat_of_the_same_state_is_not_forwarded_per_entry(self, monkeypatch):
        # A hub can hold two Staykey entries; both make the same call, because
        # the comparison value comes from the event rather than from either
        # entry's own memory.
        attribute_only = _state_changed(
            CLIMATE,
            _state("heat_cool", hvac_action="cooling"),
            _state("heat_cool", hvac_action="idle"),
        )

        first, fire_first, _ = _set_up_gateway_entry(monkeypatch, "entry-1")
        second, fire_second, _ = _set_up_gateway_entry(monkeypatch, "entry-2")

        fire_first(attribute_only)
        fire_second(attribute_only)

        assert first.state_updates == []
        assert second.state_updates == []

    def test_untracked_entity_is_ignored(self, monkeypatch):
        client, fire, _store = _set_up_gateway_entry(monkeypatch)

        fire(_state_changed("light.not_ours", _state("on"), _state("off")))

        assert client.state_updates == []


class TestCoverFiltering:
    """Cover transitional states stay filtered out."""

    def test_cover_transitional_state_is_not_forwarded(self, monkeypatch):
        client, fire, _store = _set_up_gateway_entry(monkeypatch)

        fire(_state_changed(COVER, _state("opening"), _state("closed")))

        assert client.state_updates == []

    def test_cover_terminal_state_is_forwarded(self, monkeypatch):
        client, fire, _store = _set_up_gateway_entry(monkeypatch)

        fire(_state_changed(COVER, _state("open"), _state("opening")))

        assert len(client.state_updates) == 1
        device_id, payload = client.state_updates[0]
        assert device_id == "sk-cover"
        assert payload["state"] == "open"


class TestHealthAlerts:
    """Battery alerts are decided before, and independently of, the filter."""

    def test_low_battery_alert_fires_for_an_attribute_only_change(self, monkeypatch):
        client, fire, _store = _set_up_gateway_entry(monkeypatch)

        # A battery report is exactly the attribute-only event the state
        # filter drops: the alert must still go out, only the state update
        # is skipped.
        fire(
            _state_changed(
                LOCK,
                _state("locked", battery_level=9),
                _state("locked", battery_level=16),
            )
        )

        assert client.state_updates == []
        assert client.health_alerts == [
            ("low_battery", {"device_id": "sk-lock", "battery_level": 9})
        ]

    def test_healthy_battery_raises_no_alert(self, monkeypatch):
        client, fire, _store = _set_up_gateway_entry(monkeypatch)

        fire(
            _state_changed(
                LOCK,
                _state("locked", battery_level=80),
                _state("unlocked", battery_level=80),
            )
        )

        assert client.health_alerts == []
        assert len(client.state_updates) == 1


class TestEntryStore:
    """What the config entry keeps in ``hass.data``."""

    def test_store_keeps_no_remembered_states(self, monkeypatch):
        _client, _fire, store = _set_up_gateway_entry(monkeypatch)

        assert set(store) == {"unsub", "gateway_client", "device_map"}
