"""The integration tells Staykey which Home Assistant it runs on.

Home Assistant gives every installation a random ID. The integration reads it
at setup and sends it in the capabilities message that opens each gateway
connection. Every Staykey entry on one Home Assistant therefore reports the
same ID, which lets Staykey recognise a lock that two properties share as one
lock.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from homeassistant.helpers import instance_id as instance_id_helper

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))
import staykey  # noqa: E402
from staykey.const import CONF_GATEWAY_TOKEN  # noqa: E402
from staykey.device_map import DeviceMap  # noqa: E402
from staykey.gateway import client as gateway_client  # noqa: E402

HA_INSTANCE_ID = "5c9f3a1e2b7d4e6f8a0b1c2d3e4f5a6b"
TEXT = "text"


def _run(coro):
    return asyncio.run(coro)


async def _no_commands(*_args):
    raise AssertionError("these tests send no commands")


class _FakeWebSocket:
    """A gateway socket that accepts the token and records what it is sent."""

    def __init__(self):
        self.closed = False
        self.sent: list[dict] = []

    async def send_str(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def receive(self):
        return SimpleNamespace(
            type=TEXT,
            data=json.dumps({"type": "auth_ok", "gateway_version": "1.0"}),
        )

    async def close(self) -> None:
        self.closed = True


class _FakeSession:
    def __init__(self, ws: _FakeWebSocket):
        self._ws = ws

    async def ws_connect(self, _url, **_kwargs):
        return self._ws


def test_each_connection_names_the_home_assistant_installation(monkeypatch):
    ws = _FakeWebSocket()
    monkeypatch.setattr(
        gateway_client, "async_get_clientsession", lambda _hass: _FakeSession(ws)
    )
    # The client compares a received message's type with WSMsgType.TEXT;
    # the tests run without aiohttp's real message types.
    monkeypatch.setattr(
        gateway_client.aiohttp, "WSMsgType", SimpleNamespace(TEXT=TEXT), raising=False
    )
    client = gateway_client.GatewayClient(
        hass=SimpleNamespace(),
        gateway_url="wss://gateway.example.invalid/ws",
        gateway_token="token-abc",
        agent_version="9.9.9",
        device_map=DeviceMap(),
        command_handler=_no_commands,
        instance_id=HA_INSTANCE_ID,
    )

    assert _run(client._connect_and_auth()) is True

    assert [message["type"] for message in ws.sent] == ["auth", "capabilities"]
    capabilities = ws.sent[1]
    assert capabilities["instance_id"] == HA_INSTANCE_ID
    assert capabilities["agent_version"] == "9.9.9"


class _RecordingGatewayClient:
    """Stands in for ``GatewayClient`` and keeps what it was built with."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.connected = True

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


class _FakeBus:
    def async_listen(self, _event_type, _callback):
        return lambda: None


class _FakeEntry:
    def __init__(self):
        self.entry_id = "entry-1"
        self.data = {CONF_GATEWAY_TOKEN: "token-abc"}
        self.options: dict = {}


def test_setup_gives_the_gateway_client_this_installations_id(monkeypatch):
    built: list[_RecordingGatewayClient] = []

    def build_client(**kwargs):
        client = _RecordingGatewayClient(**kwargs)
        built.append(client)
        return client

    async def instance_id_of(_hass):
        return HA_INSTANCE_ID

    monkeypatch.setattr(staykey, "GatewayClient", build_client)
    monkeypatch.setattr(instance_id_helper, "async_get", instance_id_of)
    hass = SimpleNamespace(bus=_FakeBus(), data={})

    assert _run(staykey.async_setup_entry(hass, _FakeEntry())) is True

    assert [client.kwargs.get("instance_id") for client in built] == [HA_INSTANCE_ID]
