"""Climate (thermostat) command handlers.

Translates Staykey-owned schemas to HA service calls for the climate domain.
All responses use canonical attribute names (matching Types.normalize_attributes):
  HA temperature        -> target_temperature
  HA target_temp_high   -> target_temperature_high
  HA target_temp_low    -> target_temperature_low
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.core import HomeAssistant, State

from ..device_map import DeviceMap

LOGGER = logging.getLogger(__name__)


def _build_climate_result(
    state: Optional[State],
    entity_id: str,
    action: str,
    **extra: Any,
) -> Dict[str, Any]:
    """Build a normalized climate action response with all temperature fields."""
    attrs = dict(state.attributes) if state and state.attributes else {}

    result: Dict[str, Any] = {
        "entity_id": entity_id,
        "action": action,
        "status": state.state if state else "unknown",
        "current_temperature": attrs.get("current_temperature"),
        "target_temperature": attrs.get("temperature"),
        "target_temperature_high": attrs.get("target_temp_high"),
        "target_temperature_low": attrs.get("target_temp_low"),
        "hvac_modes": attrs.get("hvac_modes", []),
    }
    result.update(extra)
    return result


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
    return _build_climate_result(state, entity_id, "set_temperature")


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
    return _build_climate_result(
        state, entity_id, "set_hvac_mode", hvac_mode=hvac_mode,
    )
