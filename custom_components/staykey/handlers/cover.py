"""Cover (garage door, blinds, etc.) command handlers.

Translates Staykey-owned schemas to HA service calls for the cover domain.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.core import HomeAssistant

from ..device_map import DeviceMap

LOGGER = logging.getLogger(__name__)


async def handle_open_cover(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    device_id = params.get("device_id", "")
    entity_id = device_map.get_entity_id(device_id)
    if not entity_id:
        raise ValueError(f"Unknown device_id: {device_id}")

    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": entity_id}, blocking=True
    )

    state = hass.states.get(entity_id)
    return {
        "entity_id": entity_id,
        "action": "open_cover",
        "state": state.state if state else "unknown",
    }


async def handle_close_cover(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    device_id = params.get("device_id", "")
    entity_id = device_map.get_entity_id(device_id)
    if not entity_id:
        raise ValueError(f"Unknown device_id: {device_id}")

    await hass.services.async_call(
        "cover", "close_cover", {"entity_id": entity_id}, blocking=True
    )

    state = hass.states.get(entity_id)
    return {
        "entity_id": entity_id,
        "action": "close_cover",
        "state": state.state if state else "unknown",
    }


async def handle_stop_cover(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    device_id = params.get("device_id", "")
    entity_id = device_map.get_entity_id(device_id)
    if not entity_id:
        raise ValueError(f"Unknown device_id: {device_id}")

    await hass.services.async_call(
        "cover", "stop_cover", {"entity_id": entity_id}, blocking=True
    )

    state = hass.states.get(entity_id)
    return {
        "entity_id": entity_id,
        "action": "stop_cover",
        "state": state.state if state else "unknown",
    }
