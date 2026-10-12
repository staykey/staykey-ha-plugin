"""Persistent WebSocket client for connecting to the Staykey Gateway."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import GATEWAY_FEATURES
from ..device_map import DeviceMap
from . import protocol
from .event_queue import EventQueue

LOGGER = logging.getLogger(__name__)

INITIAL_BACKOFF_S = 2
MAX_BACKOFF_S = 300
HEARTBEAT_S = 25
AUTH_TIMEOUT_S = 10

# How a token check ends: the gateway accepted the token, rejected it, or
# could not be asked (no connection, no answer, or a refusal that is not
# about the token). The last two double as the forms' error keys.
AUTH_OK = "ok"
AUTH_INVALID = "invalid_auth"
AUTH_CANNOT_CONNECT = "cannot_connect"

# The gateway closes the connection with this code (policy violation) when it
# refuses a token, giving the reason as an ``auth_failed`` message.
_REFUSED_CLOSE_CODE = 1008
_INVALID_TOKEN_MESSAGE = "Invalid token"


@dataclass(frozen=True)
class _AuthResult:
    outcome: str
    # Why the token was not accepted, for the log.
    message: str = ""
    # The gateway's ``auth_ok`` message, when it accepted the token.
    response: dict[str, Any] = field(default_factory=dict)


def _decode_object(text: Any) -> dict[str, Any] | None:
    """The JSON object in ``text``, or None if it holds none."""
    try:
        decoded = protocol.decode(text)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _refusal(message: str) -> _AuthResult:
    """Classify an ``auth_failed`` refusal by its message.

    Only "Invalid token" is about the token. Anything else (a disabled
    integration, the gateway's own auth timeout) can clear up without a new
    token, so it is retried.
    """
    if message == _INVALID_TOKEN_MESSAGE:
        return _AuthResult(AUTH_INVALID, message)
    return _AuthResult(AUTH_CANNOT_CONNECT, message)


def _classify_close(code: Any, reason: Any) -> _AuthResult:
    if code != _REFUSED_CLOSE_CODE:
        return _AuthResult(
            AUTH_CANNOT_CONNECT,
            f"gateway closed the connection (code {code}: {reason or 'no reason'})",
        )
    detail = _decode_object(reason)
    if detail is None:
        # A refusal without a readable reason: the token is what the gateway
        # checks at this point.
        return _AuthResult(AUTH_INVALID, f"gateway refused the token ({reason!r})")
    if detail.get("type") != "auth_failed":
        return _AuthResult(
            AUTH_CANNOT_CONNECT, f"gateway closed the connection ({reason})"
        )
    return _refusal(str(detail.get("message") or "unknown error"))


async def _authenticate(ws: aiohttp.ClientWebSocketResponse, token: str) -> _AuthResult:
    """Send ``token`` over ``ws`` and classify the gateway's answer.

    Errors sending the token or reading the answer are raised to the caller.
    """
    await ws.send_str(protocol.auth_message(token))

    try:
        msg = await asyncio.wait_for(ws.receive(), timeout=AUTH_TIMEOUT_S)
    except asyncio.TimeoutError:
        return _AuthResult(
            AUTH_CANNOT_CONNECT, f"no auth response within {AUTH_TIMEOUT_S}s"
        )

    if msg.type == aiohttp.WSMsgType.TEXT:
        response = _decode_object(msg.data)
        if response is None:
            return _AuthResult(AUTH_CANNOT_CONNECT, "unreadable auth response")
        if response.get("type") == "auth_ok":
            return _AuthResult(AUTH_OK, response=response)
        if response.get("type") == "auth_failed":
            return _refusal(str(response.get("message") or "unknown error"))
        return _AuthResult(
            AUTH_CANNOT_CONNECT,
            f"unexpected auth response type {response.get('type')!r}",
        )

    if msg.type == aiohttp.WSMsgType.CLOSE:
        return _classify_close(msg.data, msg.extra)

    return _AuthResult(
        AUTH_CANNOT_CONNECT,
        f"unexpected message type during auth: {getattr(msg.type, 'name', msg.type)}",
    )


async def probe_token(hass: HomeAssistant, gateway_url: str, token: str) -> str:
    """Check ``token`` with the gateway once, then hang up.

    Returns ``AUTH_OK``, ``AUTH_INVALID`` or ``AUTH_CANNOT_CONNECT``, and never
    raises. The check sends only the token, never the capabilities, so it
    sets nothing up. The gateway gives a token's connection to the newest one,
    so never check a token a running entry uses: closing the check would cut
    that entry off.
    """
    ws: aiohttp.ClientWebSocketResponse | None = None
    try:
        ws = await asyncio.wait_for(
            async_get_clientsession(hass).ws_connect(gateway_url),
            timeout=AUTH_TIMEOUT_S,
        )
        result = await _authenticate(ws, token)
    except Exception as exc:
        LOGGER.debug("Gateway token check failed: %r", exc)
        return AUTH_CANNOT_CONNECT
    finally:
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()

    if result.outcome != AUTH_OK:
        LOGGER.debug("Gateway token check: %s (%s)", result.outcome, result.message)
    return result.outcome


class GatewayClient:
    """Manages a persistent WSS connection to the Staykey Gateway.

    Handles authentication, heartbeat, reconnection with exponential backoff,
    and message routing to command handlers.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        gateway_url: str,
        gateway_token: str,
        agent_version: str,
        device_map: DeviceMap,
        command_handler: Callable[
            [str, str, dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]
        ],
        config_entry: ConfigEntry | None = None,
        instance_id: str | None = None,
    ) -> None:
        self._hass = hass
        self._gateway_url = gateway_url
        self._gateway_token = gateway_token
        self._agent_version = agent_version
        self._device_map = device_map
        self._command_handler = command_handler
        self._config_entry = config_entry
        # Home Assistant's installation ID, sent with the capabilities on
        # every connect.
        self._instance_id = instance_id
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._running = False
        # How the latest connection attempt's token check ended.
        self._auth_outcome: str | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._listen_task: asyncio.Task | None = None
        self._event_queue = EventQueue()

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def start(self) -> None:
        """Start the gateway connection loop."""
        self._running = True
        self._reconnect_task = asyncio.create_task(self._connection_loop())
        self._reconnect_task.add_done_callback(self._on_loop_done)

    def _on_loop_done(self, task: asyncio.Task) -> None:
        """Restart the connection loop if it died unexpectedly."""
        if not self._running:
            return
        if task.cancelled():
            LOGGER.debug("Gateway connection loop was cancelled")
            return
        if exc := task.exception():
            LOGGER.error("Gateway connection loop crashed: %s", exc, exc_info=exc)
        else:
            LOGGER.warning("Gateway connection loop exited unexpectedly, restarting")
        self._reconnect_task = asyncio.create_task(self._connection_loop())
        self._reconnect_task.add_done_callback(self._on_loop_done)

    async def stop(self) -> None:
        """Disconnect and stop reconnecting."""
        self._running = False
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        await self._close_ws()

    async def send(self, text: str) -> None:
        if self._ws and not self._ws.closed:
            try:
                await self._ws.send_str(text)
            except (ConnectionResetError, aiohttp.ClientError) as exc:
                LOGGER.debug("Send failed (connection lost): %s", exc)

    async def send_or_queue(self, text: str) -> None:
        """Send immediately if connected, otherwise queue for later delivery."""
        if self.connected:
            await self.send(text)
        else:
            self._event_queue.enqueue(text)

    async def send_event(self, event_type: str, data: dict[str, Any]) -> None:
        await self.send_or_queue(protocol.event_push_message(event_type, data))

    async def send_state_update(self, device_id: str, data: dict[str, Any]) -> None:
        await self.send_or_queue(protocol.state_update_message(device_id, data))

    async def send_health_alert(self, alert_type: str, data: dict[str, Any]) -> None:
        await self.send_or_queue(protocol.health_alert_message(alert_type, data))

    async def send_entity_id_changed(
        self, device_id: str, old_id: str, new_id: str
    ) -> None:
        await self.send(protocol.entity_id_changed_message(device_id, old_id, new_id))

    async def _connection_loop(self) -> None:
        LOGGER.info("Gateway connection loop started")
        backoff = INITIAL_BACKOFF_S

        while self._running:
            try:
                connected = await self._connect_and_auth()
                if connected:
                    backoff = INITIAL_BACKOFF_S
                    if self._event_queue.size > 0:
                        await self._event_queue.drain(self.send)
                    await self._listen()
                    LOGGER.info("Gateway listen loop ended, will reconnect")
                elif self._auth_outcome == AUTH_INVALID:
                    # Retrying can't help: stop for good, and let the owner
                    # paste a new token.
                    self._stop_for_rejected_token()
                else:
                    LOGGER.warning(
                        "Gateway connection/auth failed, retrying in %ds", backoff
                    )
            except asyncio.CancelledError:
                LOGGER.info("Gateway connection loop cancelled")
                raise
            except Exception:
                LOGGER.exception("Gateway connection error")

            await self._close_ws()

            if not self._running:
                break

            LOGGER.info("Gateway reconnecting in %ds", backoff)
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                LOGGER.info("Gateway reconnect sleep cancelled")
                raise
            backoff = min(backoff * 2, MAX_BACKOFF_S)

        LOGGER.info("Gateway connection loop stopped (running=%s)", self._running)

    def _stop_for_rejected_token(self) -> None:
        """Stop reconnecting and ask Home Assistant for a new token."""
        # Cleared first, so the loop's done callback doesn't restart it.
        self._running = False
        LOGGER.warning(
            "Staykey rejected the gateway token; Home Assistant will ask for a new one"
        )
        if self._config_entry is not None:
            self._config_entry.async_start_reauth(self._hass)

    async def _connect_and_auth(self) -> bool:
        """Connect and authenticate; True once the gateway accepts the token.

        How the token check ended is left in ``self._auth_outcome``.
        """
        await self._close_ws()
        self._auth_outcome = None

        session = async_get_clientsession(self._hass)

        try:
            self._ws = await session.ws_connect(
                self._gateway_url,
                heartbeat=HEARTBEAT_S,
                compress=9,
            )
        except Exception:
            LOGGER.exception("Failed to connect to gateway at %s", self._gateway_url)
            self._auth_outcome = AUTH_CANNOT_CONNECT
            return False

        result = await _authenticate(self._ws, self._gateway_token)
        self._auth_outcome = result.outcome

        if result.outcome != AUTH_OK:
            # A rejected token is reported once, by the connection loop.
            if result.outcome == AUTH_CANNOT_CONNECT:
                LOGGER.error("Gateway auth failed: %s", result.message)
            await self._close_ws()
            return False

        response = result.response

        LOGGER.info(
            "Gateway authenticated (gateway_version=%s)",
            response.get("gateway_version"),
        )

        property_name = response.get("property_name")
        if (
            property_name
            and self._config_entry
            and property_name != self._config_entry.title
        ):
            self._hass.config_entries.async_update_entry(
                self._config_entry, title=property_name
            )
            LOGGER.info("Updated config entry title to %r", property_name)

        ha_version = HA_VERSION
        features = list(GATEWAY_FEATURES)

        await self._ws.send_str(
            protocol.capabilities_message(
                agent_version=self._agent_version,
                ha_version=ha_version,
                protocol_version=1,
                features=features,
                tracked_devices=self._device_map.tracked_device_ids,
                instance_id=self._instance_id,
            )
        )

        return True

    async def _listen(self) -> None:
        if not self._ws:
            return

        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                await self._handle_message(msg.data)
            elif msg.type in (
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.ERROR,
            ):
                LOGGER.info("Gateway WebSocket closed: %s", msg.type)
                break

    async def _handle_message(self, text: str) -> None:
        try:
            message = protocol.decode(text)
        except Exception:
            LOGGER.warning("Invalid JSON from gateway")
            return

        msg_type = message.get("type")

        if msg_type == "ping":
            await self.send(protocol.pong_message())
            return

        if msg_type == "request":
            asyncio.create_task(self._handle_request(message))
            return

        if msg_type == "device_map_sync":
            self._device_map.load_sync(message.get("devices", []))
            asyncio.create_task(self._detect_drift())
            return

        if msg_type == "device_map_update":
            self._device_map.apply_update(
                action=message.get("action", ""),
                device=message.get("device"),
                device_id=message.get("device_id"),
            )
            return

        LOGGER.debug("Unhandled gateway message type: %s", msg_type)

    async def _handle_request(self, message: dict[str, Any]) -> None:
        request_id = message.get("id", "")
        action = message.get("action", "")
        params = message.get("params", {})

        async def progress_fn(current_state: str) -> None:
            await self.send(protocol.progress_message(request_id, current_state))

        try:
            result = await self._command_handler(
                action,
                request_id,
                params,
                progress_fn,
            )
            await self.send(
                protocol.response_message(request_id, status="ok", data=result)
            )
        except Exception as exc:
            LOGGER.exception("Command handler error for action=%s", action)
            await self.send(
                protocol.response_message(
                    request_id,
                    status="error",
                    error={"code": "handler_error", "message": str(exc)},
                )
            )

    async def _detect_drift(self) -> None:
        """Validate device map entries against the local HA entity registry on reconnect."""
        from ..services.registry import resolve_entity_by_unique_id

        entity_reg = er.async_get(self._hass)

        for device_id in list(self._device_map.tracked_device_ids):
            info = self._device_map.get_device_info(device_id)
            if not info:
                continue

            external_id = info.get("external_id", "")
            platform_ids = info.get("platform_identifiers", {})

            existing = entity_reg.async_get(external_id) if external_id else None

            if existing:
                continue

            unique_id = platform_ids.get("unique_id")
            if unique_id:
                resolved = resolve_entity_by_unique_id(self._hass, unique_id)
                if resolved and resolved != external_id:
                    self._device_map.update_entity_id(device_id, external_id, resolved)
                    await self.send_entity_id_changed(device_id, external_id, resolved)
                    LOGGER.info(
                        "Drift detected: %s renamed to %s (device %s)",
                        external_id,
                        resolved,
                        device_id,
                    )
                    continue

            await self.send_health_alert(
                "entity_not_found",
                {
                    "device_id": device_id,
                    "external_id": external_id,
                    "unique_id": unique_id,
                },
            )
            LOGGER.warning(
                "Entity not found for device %s (external_id=%s, unique_id=%s)",
                device_id,
                external_id,
                unique_id,
            )

    async def _close_ws(self) -> None:
        if self._ws and not self._ws.closed:
            with contextlib.suppress(Exception):
                await self._ws.close()
        self._ws = None
