"""Climate (thermostat) command handlers.

Translates Staykey-owned schemas to HA service calls for the climate domain.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.core import HomeAssistant

from ..device_map import DeviceMap

LOGGER = logging.getLogger(__name__)


async def handle_set_temperature(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    device_id = params.get("device_id", "")
    entity_id = device_map.get_entity_id(device_id)
    if not entity_id:
        raise ValueError(f"Unknown device_id: {device_id}")

    service_data: Dict[str, Any] = {"entity_id": entity_id}

    target_temp = params.get("target_temperature")
    target_low = params.get("target_temperature_low")
    target_high = params.get("target_temperature_high")

    if target_temp is not None:
        service_data["temperature"] = target_temp
    if target_low is not None:
        service_data["target_temp_low"] = target_low
    if target_high is not None:
        service_data["target_temp_high"] = target_high

    await hass.services.async_call(
        "climate", "set_temperature", service_data, blocking=True
    )

    state = hass.states.get(entity_id)
    attrs = dict(state.attributes) if state and state.attributes else {}

    result: Dict[str, Any] = {
        "entity_id": entity_id,
        "action": "set_temperature",
        "status": state.state if state else "unknown",
        "current_temperature": attrs.get("current_temperature"),
    }

    if target_temp is not None:
        result["target_temperature"] = attrs.get("temperature")
        result["requested_temperature"] = target_temp
    if target_low is not None:
        result["target_temperature_low"] = attrs.get("target_temp_low")
    if target_high is not None:
        result["target_temperature_high"] = attrs.get("target_temp_high")

    return result


async def handle_set_hvac_mode(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    device_id = params.get("device_id", "")
    entity_id = device_map.get_entity_id(device_id)
    if not entity_id:
        raise ValueError(f"Unknown device_id: {device_id}")

    hvac_mode = params.get("hvac_mode")
    if not hvac_mode:
        raise ValueError("hvac_mode is required")

    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": entity_id, "hvac_mode": hvac_mode},
        blocking=True,
    )

    state = hass.states.get(entity_id)
    attrs = dict(state.attributes) if state and state.attributes else {}

    return {
        "entity_id": entity_id,
        "action": "set_hvac_mode",
        "hvac_mode": hvac_mode,
        "status": state.state if state else "unknown",
        "current_temperature": attrs.get("current_temperature"),
        "target_temperature": attrs.get("temperature"),
        "hvac_modes": attrs.get("hvac_modes", []),
    }
