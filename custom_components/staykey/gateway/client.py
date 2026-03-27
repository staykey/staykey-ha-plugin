"""Persistent WebSocket client for connecting to the Staykey Gateway."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine, Dict, Optional

import aiohttp
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..device_map import DeviceMap
from . import protocol
from .event_queue import EventQueue

LOGGER = logging.getLogger(__name__)

INITIAL_BACKOFF_S = 2
MAX_BACKOFF_S = 300
HEARTBEAT_S = 25


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
            [str, str, Dict[str, Any]], Coroutine[Any, Any, Dict[str, Any]]
        ],
    ) -> None:
        self._hass = hass
        self._gateway_url = gateway_url
        self._gateway_token = gateway_token
        self._agent_version = agent_version
        self._device_map = device_map
        self._command_handler = command_handler
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._reconnect_task: Optional[asyncio.Task] = None
        self._listen_task: Optional[asyncio.Task] = None
        self._event_queue = EventQueue()

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def start(self) -> None:
        """Start the gateway connection loop."""
        self._running = True
        self._reconnect_task = asyncio.create_task(self._connection_loop())

    async def stop(self) -> None:
        """Disconnect and stop reconnecting."""
        self._running = False
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        await self._close_ws()
        if self._session and not self._session.closed:
            await self._session.close()

    async def send(self, text: str) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.send_str(text)

    async def send_or_queue(self, text: str) -> None:
        """Send immediately if connected, otherwise queue for later delivery."""
        if self.connected:
            await self.send(text)
        else:
            self._event_queue.enqueue(text)

    async def send_event(self, event_type: str, data: Dict[str, Any]) -> None:
        await self.send_or_queue(protocol.event_push_message(event_type, data))

    async def send_state_update(self, device_id: str, data: Dict[str, Any]) -> None:
        await self.send_or_queue(protocol.state_update_message(device_id, data))

    async def send_health_alert(self, alert_type: str, data: Dict[str, Any]) -> None:
        await self.send_or_queue(protocol.health_alert_message(alert_type, data))

    async def send_entity_id_changed(
        self, device_id: str, old_id: str, new_id: str
    ) -> None:
        await self.send(
            protocol.entity_id_changed_message(device_id, old_id, new_id)
        )

    async def _connection_loop(self) -> None:
        backoff = INITIAL_BACKOFF_S

        while self._running:
            try:
                connected = await self._connect_and_auth()
                if connected:
                    backoff = INITIAL_BACKOFF_S
                    if self._event_queue.size > 0:
                        await self._event_queue.drain(self.send)
                    await self._listen()
                else:
                    LOGGER.warning("Gateway authentication failed, retrying in %ds", backoff)
            except asyncio.CancelledError:
                break
            except Exception:
                LOGGER.exception("Gateway connection error")

            if not self._running:
                break

            LOGGER.info("Gateway reconnecting in %ds", backoff)
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                break
            backoff = min(backoff * 2, MAX_BACKOFF_S)

    async def _connect_and_auth(self) -> bool:
        await self._close_ws()

        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()

        try:
            self._ws = await self._session.ws_connect(
                self._gateway_url,
                heartbeat=HEARTBEAT_S,
                compress=9,
            )
        except Exception:
            LOGGER.exception("Failed to connect to gateway at %s", self._gateway_url)
            return False

        await self._ws.send_str(protocol.auth_message(self._gateway_token))

        try:
            msg = await asyncio.wait_for(self._ws.receive(), timeout=10)
        except asyncio.TimeoutError:
            LOGGER.error("Gateway auth response timeout")
            await self._close_ws()
            return False

        if msg.type != aiohttp.WSMsgType.TEXT:
            LOGGER.error("Unexpected message type during auth: %s", msg.type)
            await self._close_ws()
            return False

        response = protocol.decode(msg.data)

        if response.get("type") != "auth_ok":
            LOGGER.error(
                "Gateway auth failed: %s", response.get("message", "unknown error")
            )
            await self._close_ws()
            return False

        LOGGER.info(
            "Gateway authenticated (gateway_version=%s)",
            response.get("gateway_version"),
        )

        ha_version = HA_VERSION
        features = [
            "lock_control",
            "access_code_management",
            "zwave_code_slots",
            "state_streaming",
            "device_discovery",
            "capability_discovery",
            "health_monitoring",
            "diagnostics",
            "batch_operations",
        ]

        await self._ws.send_str(
            protocol.capabilities_message(
                agent_version=self._agent_version,
                ha_version=ha_version,
                protocol_version=1,
                features=features,
                tracked_devices=self._device_map.tracked_device_ids,
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

    async def _handle_request(self, message: Dict[str, Any]) -> None:
        request_id = message.get("id", "")
        action = message.get("action", "")
        params = message.get("params", {})

        try:
            result = await self._command_handler(action, request_id, params)
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
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None
