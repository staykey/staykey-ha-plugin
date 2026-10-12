"""The integration runs only through the Staykey gateway.

Webhook mode is gone: an entry without a gateway token stops at setup with a
reason its owner can act on, and a leftover webhook URL on a gateway entry is
ignored.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import ConfigEntryError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))
import staykey  # noqa: E402
from staykey.const import CONF_GATEWAY_TOKEN, DOMAIN  # noqa: E402

WEBHOOK_URL = "https://example.invalid/hook"


def _run(coro):
    return asyncio.run(coro)


class _FakeBus:
    def __init__(self):
        self.listeners: dict[str, list] = {}

    def async_listen(self, event_type, callback):
        self.listeners.setdefault(event_type, []).append(callback)
        return lambda: self.listeners[event_type].remove(callback)


class _FakeEntry:
    def __init__(self, data: dict, options: dict | None = None):
        self.entry_id = "entry-1"
        self.data = data
        self.options = options or {}
        self.update_listeners_added = 0

    def add_update_listener(self, _listener):
        self.update_listeners_added += 1
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


def _set_up(monkeypatch, entry):
    monkeypatch.setattr(staykey, "GatewayClient", _StubGatewayClient)
    hass = SimpleNamespace(bus=_FakeBus(), data={})
    return hass, _run(staykey.async_setup_entry(hass, entry))


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"endpoint_url": WEBHOOK_URL}, id="webhook-only"),
        pytest.param({}, id="no-token"),
    ],
)
def test_entry_without_a_gateway_token_fails_setup_with_a_reason(monkeypatch, data):
    with pytest.raises(ConfigEntryError) as excinfo:
        _set_up(monkeypatch, _FakeEntry(data))

    assert excinfo.value.translation_domain == DOMAIN
    assert excinfo.value.translation_key == "gateway_token_required"


def test_a_leftover_webhook_url_is_ignored_on_a_gateway_entry(monkeypatch):
    entry = _FakeEntry({CONF_GATEWAY_TOKEN: "token-abc", "endpoint_url": WEBHOOK_URL})

    hass, ok = _set_up(monkeypatch, entry)

    assert ok is True
    listeners = hass.bus.listeners
    assert "zwave_js_value_updated" not in listeners
    assert len(listeners["zwave_js_notification"]) == 1
    assert len(listeners["zwave_js_value_notification"]) == 1


def test_setup_registers_no_update_listener(monkeypatch):
    """Saving options reloads the entry through the options flow itself;
    Home Assistant refuses to combine that with an update listener."""
    entry = _FakeEntry({CONF_GATEWAY_TOKEN: "token-abc"})

    _set_up(monkeypatch, entry)

    assert entry.update_listeners_added == 0
