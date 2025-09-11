"""StayKey Home Assistant integration."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Dict, Optional

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant, Event, CALLBACK_TYPE
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_ENDPOINT_URL,
    CONF_FORWARD_ALL_NOTIFICATIONS,
    CONF_INTEGRATION_ID,
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

    integration_id: str = data.get(CONF_INTEGRATION_ID) or ""
    endpoint_url: str = data.get(CONF_ENDPOINT_URL) or DEFAULT_ENDPOINT_URL

    if not integration_id or not endpoint_url:
        LOGGER.error("StayKey missing required configuration; aborting setup")
        return False

    forward_all_notifications: bool = options.get(
        CONF_FORWARD_ALL_NOTIFICATIONS, DEFAULT_FORWARD_ALL_NOTIFICATIONS
    )
    verify_ssl: bool = options.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    timeout_seconds: int = options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT_SECONDS)
    

    session = async_get_clientsession(hass)

    async def send_webhook(payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":"))

        url = endpoint_url
        headers = {
            "Content-Type": "application/json",
            HDR_STAYKEY_ID: integration_id,
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

    def is_user_code_event(event: Event) -> bool:
        et = event.event_type
        data = event.data or {}

        # zwave_js_notification: prefer events with userId parameter or keypad labels
        if et == ZWAVE_NOTIFICATION_EVENT:
            params = data.get("parameters") or {}
            event_label = data.get("event_label", "").lower()
            if "userid" in params or "userId" in params:
                return True
            # Common keypad-related labels
            keypad_keywords = [
                "keypad",
                "code",
                "user code",
                "unlock",
            ]
            return any(word in event_label for word in keypad_keywords)

        # zwave_js_value_notification and value_updated: check for userCode-like properties
        if et in (ZWAVE_VALUE_NOTIFICATION_EVENT, ZWAVE_VALUE_UPDATED_EVENT):
            property_name = (data.get("property_name") or "").lower()
            if property_name in {"usercode", "user code", "code", "credential"}:
                return True

        return False

    async def handle_event(event: Event) -> None:
        if not forward_all_notifications and not is_user_code_event(event):
            return

        payload: Dict[str, Any] = {
            "integration_id": integration_id,
            "event_type": event.event_type,
            "hass_event": {
                "origin": getattr(event, "origin", None),
                "time_fired": getattr(event, "time_fired", None).isoformat()
                if getattr(event, "time_fired", None)
                else None,
                "data": event.data,
            },
            "context": {
                "hass_instance": hass.config.external_url or hass.config.internal_url,
                "component": DOMAIN,
            },
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


