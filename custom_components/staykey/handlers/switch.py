"""Switch/light on/off command handlers.

Translates Staykey-owned schemas to HA service calls.
Automatically determines the correct HA domain from the entity_id.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.core import HomeAssistant

from ..device_map import DeviceMap

LOGGER = logging.getLogger(__name__)


async def handle_turn_on(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    device_id = params.get("device_id", "")
    entity_id = device_map.get_entity_id(device_id)
    if not entity_id:
        raise ValueError(f"Unknown device_id: {device_id}")

    domain = _extract_domain(entity_id)

    await hass.services.async_call(
        domain, "turn_on", {"entity_id": entity_id}, blocking=True
    )

    state = hass.states.get(entity_id)
    return {
        "state": state.state if state else "on",
    }


async def handle_turn_off(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    device_id = params.get("device_id", "")
    entity_id = device_map.get_entity_id(device_id)
    if not entity_id:
        raise ValueError(f"Unknown device_id: {device_id}")

    domain = _extract_domain(entity_id)

    await hass.services.async_call(
        domain, "turn_off", {"entity_id": entity_id}, blocking=True
    )

    state = hass.states.get(entity_id)
    return {
        "state": state.state if state else "off",
    }


def _extract_domain(entity_id: str) -> str:
    parts = entity_id.split(".", 1)
    return parts[0] if len(parts) == 2 else "light"
