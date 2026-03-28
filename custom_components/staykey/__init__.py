"""Staykey Home Assistant integration."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import timezone
from typing import Any, Callable, Dict, Optional

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    CONF_ENDPOINT_URL,
    CONF_FORWARD_ALL_NOTIFICATIONS,
    CONF_GATEWAY_TOKEN,
    CONF_GATEWAY_URL,
    CONF_TIMEOUT,
    CONF_VERIFY_SSL,
    DEFAULT_FORWARD_ALL_NOTIFICATIONS,
    DEFAULT_GATEWAY_URL,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    ZWAVE_NOTIFICATION_EVENT,
    ZWAVE_VALUE_NOTIFICATION_EVENT,
    ZWAVE_VALUE_UPDATED_EVENT,
)
from .device_map import DeviceMap
from .gateway.client import GatewayClient
from .services.ha_bridge import create_command_handler

LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Staykey integration (YAML not supported)."""
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options updates by reloading the config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Staykey from a config entry."""
    data = entry.data
    options = entry.options

    gateway_token: str = (
        options.get(CONF_GATEWAY_TOKEN) or data.get(CONF_GATEWAY_TOKEN) or ""
    )
    gateway_url: str = (
        options.get(CONF_GATEWAY_URL) or data.get(CONF_GATEWAY_URL) or DEFAULT_GATEWAY_URL
    )
    endpoint_url: str = (
        options.get(CONF_ENDPOINT_URL) or data.get(CONF_ENDPOINT_URL) or ""
    )

    if not gateway_token and not endpoint_url:
        LOGGER.error("Staykey missing both gateway token and webhook URL; aborting setup")
        return False

    forward_all_notifications: bool = options.get(
        CONF_FORWARD_ALL_NOTIFICATIONS, DEFAULT_FORWARD_ALL_NOTIFICATIONS
    )
    verify_ssl: bool = options.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    timeout_seconds: int = options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT_SECONDS)

    integration = await async_get_integration(hass, DOMAIN)
    plugin_version: str = integration.version or "0.0.0"

    unsubscribers: list[CALLBACK_TYPE] = []
    gateway_client: Optional[GatewayClient] = None
    device_map = DeviceMap()

    # --- Gateway mode ---
    if gateway_token:
        command_handler = create_command_handler(hass, device_map)

        gateway_client = GatewayClient(
            hass=hass,
            gateway_url=gateway_url,
            gateway_token=gateway_token,
            agent_version=plugin_version,
            device_map=device_map,
            command_handler=command_handler,
        )

        await gateway_client.start()
        LOGGER.info("Staykey gateway client started (url=%s)", gateway_url)

        # State streaming: push state_changed events for tracked entities
        async def handle_state_changed(event: Event) -> None:
            entity_id = event.data.get("entity_id", "")
            if not device_map.is_tracked(entity_id):
                return

            sk_device_id = device_map.get_device_id(entity_id)
            if not sk_device_id:
                return

            new_state = event.data.get("new_state")
            if not new_state:
                return

            state_data: Dict[str, Any] = {
                "state": new_state.state,
                "last_changed": (
                    new_state.last_changed.isoformat()
                    if new_state.last_changed
                    else None
                ),
            }
            attrs = new_state.attributes or {}
            if "battery_level" in attrs:
                state_data["battery_level"] = attrs["battery_level"]

                battery = attrs["battery_level"]
                if isinstance(battery, (int, float)) and battery <= 15:
                    await gateway_client.send_health_alert(
                        "low_battery",
                        {
                            "device_id": sk_device_id,
                            "battery_level": battery,
                        },
                    )

            await gateway_client.send_state_update(sk_device_id, state_data)

        unsubscribers.append(
            hass.bus.async_listen("state_changed", handle_state_changed)
        )

        # Entity rename detection
        async def handle_entity_registry_updated(event: Event) -> None:
            action = event.data.get("action")
            if action == "update":
                old_entity_id = event.data.get("old_entity_id")
                new_entity_id = event.data.get("entity_id")
                if old_entity_id and new_entity_id and device_map.is_tracked(old_entity_id):
                    sk_device_id = device_map.get_device_id(old_entity_id)
                    if sk_device_id:
                        device_map.update_entity_id(
                            sk_device_id, old_entity_id, new_entity_id
                        )
                        await gateway_client.send_entity_id_changed(
                            sk_device_id, old_entity_id, new_entity_id
                        )
                        LOGGER.info(
                            "Entity renamed: %s -> %s (device %s)",
                            old_entity_id,
                            new_entity_id,
                            sk_device_id,
                        )
            elif action == "remove":
                entity_id = event.data.get("entity_id", "")
                if device_map.is_tracked(entity_id):
                    sk_device_id = device_map.get_device_id(entity_id)
                    if sk_device_id:
                        await gateway_client.send_health_alert(
                            "entity_removed",
                            {
                                "device_id": sk_device_id,
                                "external_id": entity_id,
                            },
                        )
                        LOGGER.warning(
                            "Tracked entity removed: %s (device %s)",
                            entity_id,
                            sk_device_id,
                        )

        unsubscribers.append(
            hass.bus.async_listen(
                "entity_registry_updated", handle_entity_registry_updated
            )
        )

        # HA restart detection
        async def handle_homeassistant_started(event: Event) -> None:
            if gateway_client and gateway_client.connected:
                await gateway_client.send_health_alert(
                    "ha_restarted",
                    {"event": "homeassistant_started"},
                )
                LOGGER.info("HA restart detected, notified gateway")

        unsubscribers.append(
            hass.bus.async_listen("homeassistant_started", handle_homeassistant_started)
        )

    # --- Webhook mode (legacy, always active if URL is configured) ---
    if endpoint_url:
        session = async_get_clientsession(hass)

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
            headers = {"Content-Type": "application/json"}
            try:
                async with asyncio.timeout(timeout_seconds):
                    resp = await session.post(
                        endpoint_url, data=body, headers=headers, ssl=verify_ssl
                    )
                    if resp.status >= 400:
                        text = await resp.text()
                        LOGGER.warning(
                            "Staykey webhook failed: %s %s - %s",
                            resp.status,
                            resp.reason,
                            text,
                        )
            except (asyncio.TimeoutError, ClientError) as err:
                LOGGER.warning("Staykey webhook error: %s", err)

        def is_whitelisted_event(event: Event) -> bool:
            if event.event_type != ZWAVE_NOTIFICATION_EVENT:
                return False
            d = event.data or {}
            if d.get("command_class") != 113:
                return False
            if d.get("type") != 6:
                return False
            if d.get("event") not in (1, 2, 6):
                return False
            return True

        async def handle_webhook_event(event: Event) -> None:
            if not forward_all_notifications and not is_whitelisted_event(event):
                return

            # If gateway is connected, let it handle events instead
            if gateway_client and gateway_client.connected:
                return

            origin = getattr(event, "origin", None)
            if origin is not None:
                origin = getattr(origin, "value", origin)
                origin = str(origin)
            time_fired = getattr(event, "time_fired", None)
            if time_fired is not None:
                occurred_at = (
                    time_fired.astimezone(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
            else:
                occurred_at = None

            d = event.data or {}
            raw_label: str = (d.get("event_label") or "").strip()

            def _to_snake(label: str) -> str:
                label = label.lower()
                label = re.sub(r"[^a-z0-9]+", "_", label)
                label = re.sub(r"_+", "_", label).strip("_")
                return label

            normalized_type = (
                _to_snake(raw_label) if raw_label else f"{event.event_type}"
            )

            params = d.get("parameters") or {}
            code_slot = (
                params.get("codeId")
                or params.get("userId")
                or d.get("code_slot")
                or d.get("code_slot_id")
            )
            evt_id = d.get("event")
            if evt_id in (1, 2):
                method = "manual"
            elif evt_id == 6:
                method = "keypad"
            else:
                method = "unknown"

            lower_label = raw_label.lower()
            result = (
                "failure"
                if any(x in lower_label for x in ("fail", "error", "invalid"))
                else "success"
            )

            device_reg = dr.async_get(hass)
            entity_reg = er.async_get(hass)
            device_id = d.get("device_id")
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
                    ents = er.async_entries_for_device(
                        entity_reg, device_id, include_disabled_entities=False
                    )
                    lock_entities = [e for e in ents if e.domain == "lock"]
                    chosen = lock_entities[0] if lock_entities else (ents[0] if ents else None)
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
                    "event_label": d.get("event_label"),
                    "node_id": d.get("node_id"),
                    "command_class_name": d.get("command_class_name") or d.get("command_class"),
                    "origin": origin,
                },
            }

            await send_webhook(payload)

        for event_type in (
            ZWAVE_NOTIFICATION_EVENT,
            ZWAVE_VALUE_NOTIFICATION_EVENT,
            ZWAVE_VALUE_UPDATED_EVENT,
        ):
            unsubscribers.append(
                hass.bus.async_listen(event_type, handle_webhook_event)
            )

    # --- Z-Wave event forwarding over gateway ---
    if gateway_client:

        async def handle_zwave_event_gateway(event: Event) -> None:
            if not gateway_client.connected:
                return

            d = event.data or {}

            device_reg = dr.async_get(hass)
            entity_reg = er.async_get(hass)
            ha_device_id = d.get("device_id")
            entity_id: Optional[str] = None
            sk_device_id: Optional[str] = None

            if ha_device_id:
                ents = er.async_entries_for_device(
                    entity_reg, ha_device_id, include_disabled_entities=False
                )
                lock_entities = [e for e in ents if e.domain == "lock"]
                chosen = lock_entities[0] if lock_entities else (ents[0] if ents else None)
                if chosen:
                    entity_id = chosen.entity_id
                    sk_device_id = device_map.get_device_id(entity_id)

            if not sk_device_id:
                return

            params = d.get("parameters") or {}
            code_slot = (
                params.get("codeId")
                or params.get("userId")
                or d.get("code_slot")
            )
            evt_id = d.get("event")
            if evt_id in (1, 2):
                method = "manual"
            elif evt_id == 6:
                method = "keypad"
            else:
                method = "unknown"

            raw_label = (d.get("event_label") or "").strip()
            lower_label = raw_label.lower()
            result = (
                "failure"
                if any(x in lower_label for x in ("fail", "error", "invalid"))
                else "success"
            )

            time_fired = getattr(event, "time_fired", None)
            timestamp = None
            if time_fired:
                timestamp = (
                    time_fired.astimezone(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )

            await gateway_client.send_event(
                "lock_activity",
                {
                    "device_id": sk_device_id,
                    "action": raw_label or event.event_type,
                    "method": method,
                    "code_slot": code_slot,
                    "result": result,
                    "timestamp": timestamp,
                },
            )

        for event_type in (
            ZWAVE_NOTIFICATION_EVENT,
            ZWAVE_VALUE_NOTIFICATION_EVENT,
            ZWAVE_VALUE_UPDATED_EVENT,
        ):
            unsubscribers.append(
                hass.bus.async_listen(event_type, handle_zwave_event_gateway)
            )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "unsub": unsubscribers,
        "gateway_client": gateway_client,
        "device_map": device_map,
    }

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    mode = "gateway" if gateway_token else "webhook-only"
    LOGGER.info(
        "Staykey set up in %s mode. Forwarding %s notifications.",
        mode,
        "all" if forward_all_notifications else "user-code",
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    store = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if store:
        if unsubs := store.get("unsub"):
            for unsub in unsubs:
                try:
                    unsub()
                except Exception:
                    LOGGER.debug("Error unsubscribing Staykey handler", exc_info=True)

        if gw := store.get("gateway_client"):
            await gw.stop()

    return True


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate config entries from older versions."""
    if config_entry.version == 1:
        LOGGER.info("Migrating Staykey config entry from version 1 to 2")
        new_data = {**config_entry.data}
        hass.config_entries.async_update_entry(config_entry, data=new_data, version=2)
    return True


from .config_flow import StaykeyOptionsFlowHandler  # noqa: E402


async def async_get_options_flow(entry: ConfigEntry) -> StaykeyOptionsFlowHandler:
    return StaykeyOptionsFlowHandler(entry)
