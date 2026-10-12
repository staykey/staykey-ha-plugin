"""The gateway token is checked, and a rejected token asks for a new one.

The gateway answers a token it accepts with an ``auth_ok`` message. It turns
an unknown token away by closing the connection with code 1008 and an
``auth_failed`` reason saying "Invalid token". It closes a disabled
integration's connection the same way, with a different message, and that is
not a problem with the token. When it can't check a token at all, it says
"Authentication unavailable" with close code 1013.

``probe_token`` checks a token once and hangs up; the setup and
re-authentication forms use it. A running entry whose token is rejected asks
Home Assistant for a new token but keeps retrying, because an older gateway
also says "Invalid token" when it can't check the token. When the token works
again, the entry closes the prompt itself.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))
from staykey.device_map import DeviceMap  # noqa: E402
from staykey.gateway import client as gateway_client  # noqa: E402

GATEWAY_URL = "wss://gateway.example.invalid/ws"
AUTH_OK = {"type": "auth_ok", "gateway_version": "1.0", "property_name": "Beach House"}
INVALID_TOKEN = json.dumps({"type": "auth_failed", "message": "Invalid token"})
DISABLED = json.dumps({"type": "auth_failed", "message": "Integration is disabled"})
UNAVAILABLE = json.dumps(
    {"type": "auth_failed", "message": "Authentication unavailable"}
)


def _run(coro):
    return asyncio.run(coro)


def _text(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        type=gateway_client.aiohttp.WSMsgType.TEXT,
        data=json.dumps(payload),
        extra=None,
    )


def _close(code: int, reason: str | None) -> SimpleNamespace:
    """A close frame, as aiohttp's ``receive()`` returns it."""
    return SimpleNamespace(
        type=gateway_client.aiohttp.WSMsgType.CLOSE, data=code, extra=reason
    )


class _FakeWebSocket:
    """A gateway socket that answers the token with one scripted reply."""

    def __init__(self, reply=None, *, hang: bool = False, error=None):
        self.reply = reply
        self.hang = hang
        self.error = error
        self.closed = False
        self.sent: list[dict] = []

    async def send_str(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def receive(self):
        if self.error is not None:
            raise self.error
        if self.hang:
            await asyncio.Event().wait()
        return self.reply

    async def close(self) -> None:
        self.closed = True

    # After an accepted token the connection is read until it ends; this
    # gateway hangs up straight away.
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeSession:
    """Opens a new socket for each connection and keeps them all."""

    def __init__(self, make_socket, *, on_connect=None):
        self._make_socket = make_socket
        self._on_connect = on_connect
        self.urls: list[str] = []
        self.sockets: list[_FakeWebSocket] = []

    async def ws_connect(self, url, **_kwargs):
        self.urls.append(url)
        if self._on_connect is not None:
            self._on_connect(len(self.urls))
        ws = self._make_socket()
        self.sockets.append(ws)
        return ws


class _RefusingSession:
    def __init__(self):
        self.attempts = 0

    async def ws_connect(self, _url, **_kwargs):
        self.attempts += 1
        raise OSError("Connection refused")


class _HangingSession:
    """A gateway that never finishes opening the connection."""

    def __init__(self):
        self.attempts = 0

    async def ws_connect(self, _url, **_kwargs):
        self.attempts += 1
        await asyncio.Event().wait()


def _probe(monkeypatch, session) -> str:
    monkeypatch.setattr(
        gateway_client, "async_get_clientsession", lambda _hass: session
    )
    # Bounded here too, so a probe that never returns fails instead of hanging.
    return _run(
        asyncio.wait_for(
            gateway_client.probe_token(SimpleNamespace(), GATEWAY_URL, "token-abc"),
            timeout=2,
        )
    )


# probe_token


def test_probe_reports_an_accepted_token_and_hangs_up(monkeypatch):
    session = _FakeSession(lambda: _FakeWebSocket(_text(AUTH_OK)))

    assert _probe(monkeypatch, session) == "ok"

    assert session.urls == [GATEWAY_URL]
    [ws] = session.sockets
    assert ws.closed
    # Only the token goes out: a check never announces this Home Assistant.
    assert ws.sent == [{"type": "auth", "token": "token-abc"}]


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(
            lambda: _close(1008, INVALID_TOKEN), id="closed-1008-invalid-token"
        ),
        pytest.param(
            lambda: _text({"type": "auth_failed", "message": "Invalid token"}),
            id="text-invalid-token",
        ),
        pytest.param(lambda: _close(1008, "not json"), id="closed-1008-unreadable"),
    ],
)
def test_probe_reports_a_rejected_token(monkeypatch, reply):
    session = _FakeSession(lambda: _FakeWebSocket(reply()))

    assert _probe(monkeypatch, session) == "invalid_auth"

    assert session.sockets[0].closed


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(lambda: _close(1008, DISABLED), id="closed-1008"),
        pytest.param(
            lambda: _text(json.loads(DISABLED)),
            id="text",
        ),
    ],
)
def test_probe_reports_a_disabled_integration(monkeypatch, reply):
    session = _FakeSession(lambda: _FakeWebSocket(reply()))

    assert _probe(monkeypatch, session) == "integration_disabled"

    assert session.sockets[0].closed


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(
            lambda: _close(1013, UNAVAILABLE), id="closed-1013-auth-unavailable"
        ),
        pytest.param(
            lambda: _text({"type": "auth_failed", "message": "Authentication timeout"}),
            id="gateway-auth-timeout",
        ),
        pytest.param(lambda: _close(1011, ""), id="closed-server-error"),
        pytest.param(lambda: _text({"type": "ping"}), id="unexpected-message"),
    ],
)
def test_probe_reports_other_refusals_as_not_reachable(monkeypatch, reply):
    session = _FakeSession(lambda: _FakeWebSocket(reply()))

    assert _probe(monkeypatch, session) == "cannot_connect"

    assert session.sockets[0].closed


def test_probe_reports_a_failed_connection(monkeypatch):
    session = _RefusingSession()

    assert _probe(monkeypatch, session) == "cannot_connect"

    assert session.attempts == 1


def test_probe_reports_a_socket_error_and_hangs_up(monkeypatch):
    session = _FakeSession(
        lambda: _FakeWebSocket(error=ConnectionResetError("reset by peer"))
    )

    assert _probe(monkeypatch, session) == "cannot_connect"

    assert session.sockets[0].closed


def test_probe_gives_up_on_a_gateway_that_does_not_answer(monkeypatch):
    monkeypatch.setattr(gateway_client, "AUTH_TIMEOUT_S", 0.01)
    session = _FakeSession(lambda: _FakeWebSocket(hang=True))

    assert _probe(monkeypatch, session) == "cannot_connect"

    assert session.sockets[0].closed


def test_probe_gives_up_on_a_connection_that_does_not_open(monkeypatch):
    monkeypatch.setattr(gateway_client, "AUTH_TIMEOUT_S", 0.01)
    session = _HangingSession()

    assert _probe(monkeypatch, session) == "cannot_connect"

    assert session.attempts == 1


# The connection loop


class _FakeFlows:
    """Home Assistant's config flows in progress, and the ones aborted."""

    def __init__(self):
        self.in_progress: list[dict] = []
        self.aborted: list[str] = []

    def open(self, source: str, entry_id: str) -> None:
        flow_id = f"flow-{len(self.in_progress) + len(self.aborted) + 1}"
        self.in_progress.append(
            {"flow_id": flow_id, "context": {"source": source, "entry_id": entry_id}}
        )

    def async_abort(self, flow_id: str) -> None:
        [flow] = [f for f in self.in_progress if f["flow_id"] == flow_id]
        self.in_progress.remove(flow)
        self.aborted.append(flow_id)


class _FakeEntry:
    """A config entry whose re-authentication prompt opens a flow."""

    def __init__(self):
        self.entry_id = "entry-1"
        # The name the gateway gives, so an accepted token renames nothing.
        self.title = "Beach House"
        self.reauth_started_with: list = []
        self.flow_queries: list[set[str]] = []

    def async_start_reauth(self, hass, context=None, data=None):
        self.reauth_started_with.append(hass)
        hass.config_entries.flow.open("reauth", self.entry_id)

    def async_get_active_flows(self, hass, sources):
        self.flow_queries.append(set(sources))
        return (
            flow
            for flow in list(hass.config_entries.flow.in_progress)
            if flow["context"]["entry_id"] == self.entry_id
            and flow["context"]["source"] in sources
        )


async def _no_commands(*_args):
    raise AssertionError("these tests send no commands")


def _replies(*replies):
    """Sockets that answer the token with each of ``replies`` in turn."""
    remaining = iter(replies)
    return lambda: _FakeWebSocket(next(remaining)())


def _ok():
    return _text(AUTH_OK)


def _rejected():
    return _close(1008, INVALID_TOKEN)


def _run_connection_loop(monkeypatch, make_socket, *, give_up_after: int = 3):
    """Start a client against ``make_socket`` sockets and wait for its loop
    to end.

    Reconnects don't wait. Once the loop has made ``give_up_after``
    connections, the session stops the client, so a test never hangs.
    """
    monkeypatch.setattr(gateway_client, "INITIAL_BACKOFF_S", 0)
    hass = SimpleNamespace(config_entries=SimpleNamespace(flow=_FakeFlows()))
    entry = _FakeEntry()
    client = gateway_client.GatewayClient(
        hass=hass,
        gateway_url=GATEWAY_URL,
        gateway_token="token-abc",
        agent_version="9.9.9",
        device_map=DeviceMap(),
        command_handler=_no_commands,
        config_entry=entry,
    )

    def stop_retrying(connections: int) -> None:
        if connections >= give_up_after:
            client._running = False

    session = _FakeSession(make_socket, on_connect=stop_retrying)
    monkeypatch.setattr(
        gateway_client, "async_get_clientsession", lambda _hass: session
    )

    async def scenario():
        await client.start()
        loop_task = client._reconnect_task
        await asyncio.wait_for(loop_task, timeout=2)
        # Let the loop's done callback run.
        await asyncio.sleep(0)
        return loop_task

    loop_task = _run(scenario())
    return SimpleNamespace(
        client=client,
        entry=entry,
        hass=hass,
        flows=hass.config_entries.flow,
        session=session,
        loop_task=loop_task,
    )


def _logged(caplog, level: int, text: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno == level and text in r.getMessage()]


def test_a_rejected_token_asks_for_a_new_one_once_and_keeps_retrying(
    monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG, logger=gateway_client.LOGGER.name)

    run = _run_connection_loop(
        monkeypatch, _replies(_rejected, _rejected, _rejected), give_up_after=3
    )

    # Still retrying when the test stopped it.
    assert len(run.session.sockets) == 3
    assert all(ws.closed for ws in run.session.sockets)
    assert run.entry.reauth_started_with == [run.hass]
    # The loop ended only because the test stopped it, and was not restarted.
    assert run.client._reconnect_task is run.loop_task
    assert not run.loop_task.cancelled() and run.loop_task.exception() is None
    problems = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(problems) == 1
    assert "rejected the gateway token" in problems[0].getMessage()
    assert problems[0].exc_info is None
    # The prompt stays open while the token is rejected.
    assert run.flows.aborted == []
    assert [f["flow_id"] for f in run.flows.in_progress] == ["flow-1"]


def test_the_prompt_closes_when_the_token_works_again(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=gateway_client.LOGGER.name)

    run = _run_connection_loop(
        monkeypatch,
        _replies(_ok, _rejected, _rejected, _ok, _rejected),
        give_up_after=5,
    )

    assert len(run.session.sockets) == 5
    # One prompt per run of rejections: the first was closed when the token
    # worked again, and the second rejection opened a new one.
    assert run.entry.reauth_started_with == [run.hass, run.hass]
    assert run.flows.aborted == ["flow-1"]
    assert [f["flow_id"] for f in run.flows.in_progress] == ["flow-2"]
    # Only this entry's re-authentication flows are looked up, and only after
    # a rejection: the first accepted token had nothing to close.
    assert run.entry.flow_queries == [{"reauth"}]
    warnings = _logged(caplog, logging.WARNING, "rejected the gateway token")
    assert len(warnings) == 2
    assert all(r.exc_info is None for r in warnings)
    assert len(_logged(caplog, logging.INFO, "accepted the gateway token again")) == 1
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(lambda: _close(1008, DISABLED), id="integration-disabled"),
        pytest.param(
            lambda: _close(1013, UNAVAILABLE), id="closed-1013-auth-unavailable"
        ),
        pytest.param(lambda: _close(1011, ""), id="closed-server-error"),
        pytest.param(
            lambda: _text({"type": "auth_failed", "message": "Authentication timeout"}),
            id="gateway-auth-timeout",
        ),
    ],
)
def test_other_auth_failures_keep_retrying_without_asking(monkeypatch, reply):
    run = _run_connection_loop(
        monkeypatch, lambda: _FakeWebSocket(reply()), give_up_after=3
    )

    assert len(run.session.sockets) == 3
    assert run.entry.reauth_started_with == []
    assert run.flows.in_progress == []
    assert run.entry.flow_queries == []
