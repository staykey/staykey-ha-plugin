"""Staykey Home Assistant integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import instance_id
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    CONF_ALLOW_SUPPORT_ACTIONS,
    CONF_GATEWAY_TOKEN,
    CONF_GATEWAY_URL,
    DEFAULT_ALLOW_SUPPORT_ACTIONS,
    DEFAULT_GATEWAY_URL,
    DOMAIN,
    ZWAVE_NOTIFICATION_EVENT,
    ZWAVE_VALUE_NOTIFICATION_EVENT,
)
from .device_map import DeviceMap
from .events import device_event_payload, state_update_payload
from .gateway.client import GatewayClient
from .services.ha_bridge import create_command_handler
from .state_filter import should_forward_state

LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Staykey integration (YAML not supported)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Staykey from a config entry."""
    data = entry.data
    options = entry.options

    # Stripped: older versions saved the token as typed, spaces and all.
    gateway_token: str = (
        options.get(CONF_GATEWAY_TOKEN) or data.get(CONF_GATEWAY_TOKEN) or ""
    ).strip()
    gateway_url: str = (
        options.get(CONF_GATEWAY_URL)
        or data.get(CONF_GATEWAY_URL)
        or DEFAULT_GATEWAY_URL
    )

    # Staykey connects only through its gateway. An entry with no token, such
    # as one set up for the removed webhook mode, has nothing to run: setup
    # stops here and the reason shows on the entry. Saving a token through
    # Configure reloads the entry.
    if not gateway_token:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="gateway_token_required",
        )

    allow_support_actions: bool = options.get(
        CONF_ALLOW_SUPPORT_ACTIONS, DEFAULT_ALLOW_SUPPORT_ACTIONS
    )

    integration = await async_get_integration(hass, DOMAIN)
    plugin_version: str = integration.version or "0.0.0"

    # Home Assistant's installation ID, a random identifier it creates once.
    # Every Staykey entry on this Home Assistant reports the same one, which
    # is how Staykey recognises a lock that two properties share as one lock.
    ha_instance_id: str = await instance_id.async_get(hass)

    unsubscribers: list[CALLBACK_TYPE] = []
    device_map = DeviceMap()
    command_handler = create_command_handler(
        hass, device_map, allow_support_actions=allow_support_actions
    )

    gateway_client = GatewayClient(
        hass=hass,
        gateway_url=gateway_url,
        gateway_token=gateway_token,
        agent_version=plugin_version,
        device_map=device_map,
        command_handler=command_handler,
        config_entry=entry,
        instance_id=ha_instance_id,
    )

    await gateway_client.start()
    LOGGER.info("Staykey gateway client started (url=%s)", gateway_url)

    # State streaming: push state_changed events for tracked entities.
    #
    # The listener stays on the global state_changed bus rather than
    # being scoped to the tracked entities: the device map changes while
    # the integration is running (entity renames, device reconcile), so a
    # scoped subscription would have to be torn down and rebuilt on every
    # one of those changes.
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

        # Battery/health alerts fire regardless of state filtering, and
        # must stay ahead of it: a battery report is exactly the
        # attribute-only event the filter below drops, so only the state
        # forward is skipped for one, never the alert.
        attrs = new_state.attributes or {}
        if "battery_level" in attrs:
            battery = attrs["battery_level"]
            if isinstance(battery, (int, float)) and battery <= 15:
                await gateway_client.send_health_alert(
                    "low_battery",
                    {
                        "device_id": sk_device_id,
                        "battery_level": battery,
                    },
                )

        old_state = event.data.get("old_state")
        if old_state is None:
            # No previous state means Home Assistant is restoring the
            # entity (startup, or a config entry reload), not reporting a
            # change. A restart is already signalled once through the
            # homeassistant_started health alert, so it does not also
            # arrive as a burst of state updates.
            return

        # Compare against the state the event itself carries rather than
        # against a remembered one: nothing survives a restart or a
        # reload to compare with, and a hub with two config entries would
        # hold two separate memories of it.
        if not should_forward_state(entity_id, new_state.state, old_state.state):
            return

        await gateway_client.send_state_update(
            sk_device_id, state_update_payload(new_state)
        )

    unsubscribers.append(hass.bus.async_listen("state_changed", handle_state_changed))

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
        hass.bus.async_listen("entity_registry_updated", handle_entity_registry_updated)
    )

    # HA restart detection
    async def handle_homeassistant_started(event: Event) -> None:
        if gateway_client.connected:
            await gateway_client.send_health_alert(
                "ha_restarted",
                {"event": "homeassistant_started"},
            )
            LOGGER.info("HA restart detected, notified gateway")

    unsubscribers.append(
        hass.bus.async_listen("homeassistant_started", handle_homeassistant_started)
    )

    # --- Z-Wave event forwarding over gateway ---
    async def handle_zwave_event_gateway(event: Event) -> None:
        d = event.data or {}
        entity_reg = er.async_get(hass)
        ha_device_id = d.get("device_id")
        sk_device_id: str | None = None

        if ha_device_id:
            ents = er.async_entries_for_device(
                entity_reg, ha_device_id, include_disabled_entities=False
            )
            lock_entities = [e for e in ents if e.domain == "lock"]
            chosen = lock_entities[0] if lock_entities else (ents[0] if ents else None)
            if chosen:
                sk_device_id = device_map.get_device_id(chosen.entity_id)

        if not sk_device_id:
            return

        LOGGER.debug("Forwarding %s for device %s", event.event_type, sk_device_id)

        await gateway_client.send_event(
            "device_event", device_event_payload(event, sk_device_id)
        )

    for event_type in (
        ZWAVE_NOTIFICATION_EVENT,
        ZWAVE_VALUE_NOTIFICATION_EVENT,
    ):
        unsubscribers.append(
            hass.bus.async_listen(event_type, handle_zwave_event_gateway)
        )

    # KNOWN LIMITATION (early Matter lock support):
    #
    # Matter devices can produce detailed per-credential lock-operation
    # events, but Home Assistant's Matter integration (through 2026.4) does
    # not yet expose them on hass.bus like Z-Wave notification events.
    # The entity mostly updates `locked`/`unlocked`, so forwarded
    # activity is lower fidelity than Z-Wave. Programming operations
    # still return structured success/failure in their service responses;
    # richer keypad history awaits upstream HA event coverage.

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "unsub": unsubscribers,
        "gateway_client": gateway_client,
        "device_map": device_map,
    }

    LOGGER.info("Staykey set up in gateway mode.")
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
