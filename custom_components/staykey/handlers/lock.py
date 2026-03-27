"""Lock/unlock and access code command handlers.

Translates Staykey-owned schemas to HA service calls.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.core import HomeAssistant

from ..device_map import DeviceMap
from .utils import ProgressFn, wait_for_state

LOGGER = logging.getLogger(__name__)

_LOCK_STATE_TIMEOUT = 15  # seconds — matches Orion backend


async def handle_lock(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
    progress_fn: Optional[ProgressFn] = None,
) -> Dict[str, Any]:
    device_id = params.get("device_id", "")
    entity_id = device_map.get_entity_id(device_id)
    if not entity_id:
        raise ValueError(f"Unknown device_id: {device_id}")

    await hass.services.async_call(
        "lock", "lock", {"entity_id": entity_id}, blocking=True
    )

    status = await wait_for_state(
        hass, entity_id, "locked", _LOCK_STATE_TIMEOUT, progress_fn=progress_fn,
    )
    return {
        "state": status,
        "method": "remote",
    }


async def handle_unlock(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
    progress_fn: Optional[ProgressFn] = None,
) -> Dict[str, Any]:
    device_id = params.get("device_id", "")
    entity_id = device_map.get_entity_id(device_id)
    if not entity_id:
        raise ValueError(f"Unknown device_id: {device_id}")

    service_data: Dict[str, Any] = {"entity_id": entity_id}
    if code := params.get("code"):
        service_data["code"] = code

    await hass.services.async_call(
        "lock", "unlock", service_data, blocking=True
    )

    status = await wait_for_state(
        hass, entity_id, "unlocked", _LOCK_STATE_TIMEOUT, progress_fn=progress_fn,
    )
    return {
        "state": status,
        "method": "remote",
    }


async def handle_set_access_code(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Set an access code on a lock. Uses zwave_js service for Z-Wave locks."""
    device_id = params.get("device_id", "")
    entity_id = device_map.get_entity_id(device_id)
    if not entity_id:
        raise ValueError(f"Unknown device_id: {device_id}")

    slot = params.get("slot")
    code = params.get("code")
    name = params.get("name", "")

    if not slot or not code:
        raise ValueError("slot and code are required")

    await hass.services.async_call(
        "zwave_js",
        "set_lock_usercode",
        {
            "entity_id": entity_id,
            "code_slot": slot,
            "usercode": str(code),
        },
        blocking=True,
    )

    return {
        "slot": slot,
        "verified": False,
        "method": "zwave_set",
    }


async def handle_clear_access_code(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Clear an access code from a lock."""
    device_id = params.get("device_id", "")
    entity_id = device_map.get_entity_id(device_id)
    if not entity_id:
        raise ValueError(f"Unknown device_id: {device_id}")

    slot = params.get("slot")
    if not slot:
        raise ValueError("slot is required")

    await hass.services.async_call(
        "zwave_js",
        "clear_lock_usercode",
        {
            "entity_id": entity_id,
            "code_slot": slot,
        },
        blocking=True,
    )

    return {"slot": slot, "cleared": True}
