"""Support actions run only when the Home Assistant owner allows them.

``ha_service_call`` and the other support actions reach past the devices
Staykey gave this entry, so the dispatcher refuses them unless the entry's
``allow_support_actions`` option is on. Everyday actions are unaffected.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))
import staykey  # noqa: E402
from staykey.const import (  # noqa: E402
    CONF_ALLOW_SUPPORT_ACTIONS,
    CONF_GATEWAY_TOKEN,
    DEFAULT_ALLOW_SUPPORT_ACTIONS,
)
from staykey.device_map import DeviceMap  # noqa: E402
from staykey.handlers import batch  # noqa: E402
from staykey.services import ha_bridge  # noqa: E402
from staykey.services.ha_bridge import (  # noqa: E402
    SUPPORT_ACTIONS,
    SupportActionDisabledError,
    create_command_handler,
)

LOCK_DEVICE = "sk-lock"

SUPPORT_ACTION_NAMES = [
    "batch",
    "clear_access_code",
    "discover_devices",
    "get_access_codes",
    "get_lock_access_codes",
    "ha_service_call",
    "set_access_code",
    "tap_events",
]


def _run(coro):
    return asyncio.run(coro)


def _device_map() -> DeviceMap:
    device_map = DeviceMap()
    device_map.load_sync([{"device_id": LOCK_DEVICE, "external_id": "lock.front_door"}])
    return device_map


def _handler(**kwargs):
    return create_command_handler(SimpleNamespace(), _device_map(), **kwargs)


@pytest.fixture
def reached(monkeypatch):
    """Swap every routed handler for a recorder and return what was reached."""
    calls: list[str] = []

    def recorder(name):
        async def record(*_args, **_kwargs):
            calls.append(name)
            return {"devices": []} if name == "discover_devices" else {"ran": name}

        return record

    for action in list(ha_bridge._ACTION_MAP):
        monkeypatch.setitem(ha_bridge._ACTION_MAP, action, recorder(action))
    monkeypatch.setattr(
        ha_bridge.device_discovery,
        "handle_discover_devices",
        recorder("discover_devices"),
    )
    return calls


def test_support_actions_are_off_by_default():
    assert DEFAULT_ALLOW_SUPPORT_ACTIONS is False


def test_the_gated_actions_are_the_support_and_legacy_actions():
    assert sorted(SUPPORT_ACTIONS) == SUPPORT_ACTION_NAMES


@pytest.mark.parametrize("action", SUPPORT_ACTION_NAMES)
def test_support_action_is_refused_while_the_option_is_off(reached, action):
    handle = _handler(allow_support_actions=False)
    params = {
        "commands": [{"action": "get_state", "params": {"device_id": LOCK_DEVICE}}]
    }

    with pytest.raises(SupportActionDisabledError, match=action):
        _run(handle(action, "req-1", params))

    assert reached == []


def test_the_dispatcher_refuses_support_actions_unless_told_otherwise(reached):
    with pytest.raises(SupportActionDisabledError):
        _run(_handler()("ha_service_call", "req-1", {}))

    assert reached == []


@pytest.mark.parametrize("action", [a for a in SUPPORT_ACTION_NAMES if a != "batch"])
def test_support_action_runs_once_the_owner_allows_it(reached, action):
    _run(_handler(allow_support_actions=True)(action, "req-1", {}))

    assert reached == [action]


@pytest.mark.parametrize(
    ("action", "handler_reached"),
    [
        ("get_state", "get_state"),
        ("get_diagnostics", "get_diagnostics"),
        ("list_entities", "discover_devices"),
    ],
)
def test_everyday_actions_run_while_the_option_is_off(reached, action, handler_reached):
    handle = _handler(allow_support_actions=False)

    _run(handle(action, "req-1", {"device_id": LOCK_DEVICE}))

    assert reached == [handler_reached]


def test_a_batch_cannot_carry_a_support_action_past_the_option(reached):
    """The check sits in the dispatcher that ``batch`` re-enters for each of
    its commands, so a nested support action is refused like a lone one."""
    handle = _handler(allow_support_actions=False)
    commands = [
        {"id": "a", "action": "get_state", "params": {"device_id": LOCK_DEVICE}},
        {"id": "b", "action": "ha_service_call", "params": {}},
    ]

    result = _run(batch.handle_batch(handle, {"commands": commands}))

    by_id = {r["id"]: r for r in result["results"]}
    assert by_id["a"]["status"] == "ok"
    assert by_id["b"]["status"] == "error"
    assert "support action" in by_id["b"]["error"]["message"]
    assert reached == ["get_state"]


def test_an_allowed_batch_runs_each_command_through_the_dispatcher(reached):
    handle = _handler(allow_support_actions=True)
    commands = [
        {"id": "a", "action": "get_state", "params": {"device_id": LOCK_DEVICE}},
        {"id": "b", "action": "ha_service_call", "params": {}},
    ]

    result = _run(handle("batch", "req-1", {"commands": commands}))

    assert result["succeeded"] == 2
    assert sorted(reached) == ["get_state", "ha_service_call"]


class _FakeBus:
    def __init__(self):
        self.listeners: dict[str, list] = {}

    def async_listen(self, event_type, callback):
        self.listeners.setdefault(event_type, []).append(callback)
        return lambda: self.listeners[event_type].remove(callback)


class _FakeEntry:
    def __init__(self, options: dict):
        self.entry_id = "entry-1"
        self.data = {CONF_GATEWAY_TOKEN: "token-abc"}
        self.options = options

    def add_update_listener(self, _listener):
        return lambda: None

    def async_on_unload(self, _unsub):
        return None


class _StubGatewayClient:
    def __init__(self, **_kwargs):
        self.connected = True

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


@pytest.mark.parametrize(
    ("options", "expected"),
    [({}, False), ({CONF_ALLOW_SUPPORT_ACTIONS: True}, True)],
)
def test_setup_hands_the_entry_option_to_the_dispatcher(monkeypatch, options, expected):
    seen: dict[str, bool] = {}

    def recording_create_command_handler(hass, device_map, *, allow_support_actions):
        seen["allow_support_actions"] = allow_support_actions
        return create_command_handler(
            hass, device_map, allow_support_actions=allow_support_actions
        )

    monkeypatch.setattr(
        staykey, "create_command_handler", recording_create_command_handler
    )
    monkeypatch.setattr(staykey, "GatewayClient", _StubGatewayClient)
    hass = SimpleNamespace(bus=_FakeBus(), data={})

    assert _run(staykey.async_setup_entry(hass, _FakeEntry(options))) is True
    assert seen == {"allow_support_actions": expected}
