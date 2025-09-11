"""StayKey Home Assistant integration."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Dict, Optional
import re

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant, Event, CALLBACK_TYPE
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.loader import async_get_integration
import uuid
from datetime import timezone

from .const import (
    CONF_ENDPOINT_URL,
    CONF_FORWARD_ALL_NOTIFICATIONS,
    CONF_PROPERTY_ID,
    CONF_TIMEOUT,
    CONF_VERIFY_SSL,
    DEFAULT_ENDPOINT_URL,
    DEFAULT_FORWARD_ALL_NOTIFICATIONS,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    HDR_STAYKEY_ID,
    ZWAVE_NOTIFICATION_EVENT,
    ZWAVE_VALUE_NOTIFICATION_EVENT,
    ZWAVE_VALUE_UPDATED_EVENT,
)

LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the StayKey integration (YAML not supported)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up StayKey from a config entry."""
    data = entry.data
    options = entry.options

    property_id: str = data.get(CONF_PROPERTY_ID) or ""
    endpoint_url: str = data.get(CONF_ENDPOINT_URL) or DEFAULT_ENDPOINT_URL

    if not property_id or not endpoint_url:
        LOGGER.error("StayKey missing required configuration; aborting setup")
        return False

    forward_all_notifications: bool = options.get(
        CONF_FORWARD_ALL_NOTIFICATIONS, DEFAULT_FORWARD_ALL_NOTIFICATIONS
    )
    verify_ssl: bool = options.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    timeout_seconds: int = options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT_SECONDS)
    

    session = async_get_clientsession(hass)
    integration = await async_get_integration(hass, DOMAIN)
    plugin_version: str = integration.version or "0.0.0"

    def _json_default(obj: Any) -> str:
        iso = getattr(obj, "isoformat", None)
        if callable(iso):
            return iso()
        value = getattr(obj, "value", None)
        if value is not None and not callable(value):
            return str(value)
        return str(obj)

    async def send_webhook(payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":"), default=_json_default)

        url = endpoint_url
        headers = {
            "Content-Type": "application/json",
            HDR_STAYKEY_ID: property_id,
        }

        try:
            async with asyncio.timeout(timeout_seconds):
                resp = await session.post(url, data=body, headers=headers, ssl=verify_ssl)
                if resp.status >= 400:
                    text = await resp.text()
                    LOGGER.warning(
                        "StayKey webhook failed: %s %s - %s", resp.status, resp.reason, text
                    )
        except (asyncio.TimeoutError, ClientError) as err:
            LOGGER.warning("StayKey webhook error: %s", err)

    def is_whitelisted_event(event: Event) -> bool:
        # Whitelist only specific Z-Wave JS Notification events for door locks.
        # command_class 113 = Notification Command Class
        # type 6 = Access Control (lock/door related)
        # event ids under type 6 used here:
        #   1 = Manual lock operation
        #   2 = Manual unlock operation
        #   6 = Keypad unlock operation
        if event.event_type != ZWAVE_NOTIFICATION_EVENT:
            return False
        data = event.data or {}
        # Enforce: Notification CC (113), Access Control type (6), events {1,2,6}
        if data.get("command_class") != 113:
            return False
        if data.get("type") != 6:
            return False
        if data.get("event") not in (1, 2, 6):
            return False
        return True

    async def handle_event(event: Event) -> None:
        if not forward_all_notifications and not is_whitelisted_event(event):
            return

        # Normalize origin and time
        origin = getattr(event, "origin", None)
        if origin is not None:
            origin = getattr(origin, "value", origin)
            origin = str(origin)
        time_fired = getattr(event, "time_fired", None)
        if time_fired is not None:
            occurred_at = time_fired.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        else:
            occurred_at = None

        # Map event label -> normalized event_type (snake_case of label)
        data = event.data or {}
        raw_label: str = (data.get("event_label") or "").strip()
        def _to_snake(label: str) -> str:
            label = label.lower()
            label = re.sub(r"[^a-z0-9]+", "_", label)
            label = re.sub(r"_+", "_", label).strip("_")
            return label
        normalized_type = _to_snake(raw_label) if raw_label else f"{event.event_type}"

        # Access info
        params = data.get("parameters") or {}
        code_slot = (
            params.get("codeId")
            or params.get("userId")
            or data.get("code_slot")
            or data.get("code_slot_id")
        )
        lower_label = raw_label.lower()
        # Determine access method from event id when available
        #   1/2 are physical/manual lock/unlock; 6 is keypad unlock
        evt_id = data.get("event")
        if evt_id in (1, 2):
            method = "manual"
        elif evt_id == 6:
            method = "keypad"
        else:
            method = "unknown"
        result = "failure" if any(x in lower_label for x in ("fail", "error", "invalid")) else "success"

        # Device/entity enrichment
        device_reg = dr.async_get(hass)
        entity_reg = er.async_get(hass)
        device_id = data.get("device_id")
        entity_id: Optional[str] = None
        device_name: Optional[str] = None
        manufacturer: Optional[str] = None
        model: Optional[str] = None

        if device_id:
            device = device_reg.async_get(device_id)
            if device:
                device_name = device.name_by_user or device.name
                manufacturer = device.manufacturer
                model = device.model
                ents = er.async_entries_for_device(entity_reg, device_id, include_disabled_entities=False)
                # Prefer lock domain
                lock_entities = [e for e in ents if e.domain == "lock"]
                chosen = (lock_entities[0] if lock_entities else (ents[0] if ents else None))
                if chosen:
                    entity_id = chosen.entity_id

        payload: Dict[str, Any] = {
            "schema_version": "1.0",
            "event_id": str(uuid.uuid4()),
            "occurred_at": occurred_at,
            "event_type": normalized_type,
            "device": {
                "device_id": device_id,
                "entity_id": entity_id,
                "name": device_name,
                "manufacturer": manufacturer,
                "model": model,
            },
            "access": {
                "method": method,
                "code_slot": code_slot,
                "result": result,
            },
            "plugin": {
                "version": plugin_version,
                "instance_url": hass.config.external_url or hass.config.internal_url,
            },
            "ha": {
                "event_type": event.event_type,
                "event_label": data.get("event_label"),
                "node_id": data.get("node_id"),
                "command_class_name": data.get("command_class_name") or data.get("command_class"),
                "origin": origin,
            },
            "property_id": property_id,
        }

        await send_webhook(payload)

    # Subscribe to Z-Wave JS events
    unsubscribers: list[CALLBACK_TYPE] = []
    for event_type in (
        ZWAVE_NOTIFICATION_EVENT,
        ZWAVE_VALUE_NOTIFICATION_EVENT,
        ZWAVE_VALUE_UPDATED_EVENT,
    ):
        unsubscribers.append(hass.bus.async_listen(event_type, handle_event))

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "unsub": unsubscribers,
    }

    LOGGER.info("StayKey set up. Forwarding %s notifications.",
                "all" if forward_all_notifications else "user-code")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    store = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if store and (unsubs := store.get("unsub")):
        for unsub in unsubs:
            try:
                unsub()
            except Exception:  # pragma: no cover
                LOGGER.debug("Error unsubscribing StayKey handler", exc_info=True)
    return True


