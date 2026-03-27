"""Bridge between Staykey command schemas and HA service calls.

Routes incoming commands to the appropriate handler based on action name.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine, Dict

from homeassistant.core import HomeAssistant

from ..device_map import DeviceMap
from ..handlers import batch, capability, device_discovery, diagnostics, lock, state
from ..services import zwave

LOGGER = logging.getLogger(__name__)


def create_command_handler(
    hass: HomeAssistant,
    device_map: DeviceMap,
) -> Callable[[str, str, Dict[str, Any]], Coroutine[Any, Any, Dict[str, Any]]]:
    """Create a command handler function that routes actions to handlers."""

    async def handle_command(
        action: str,
        request_id: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        LOGGER.debug("Handling command: action=%s id=%s", action, request_id)

        if action == "lock":
            return await lock.handle_lock(hass, device_map, params)
        elif action == "unlock":
            return await lock.handle_unlock(hass, device_map, params)
        elif action == "set_access_code":
            return await _handle_set_access_code(hass, device_map, params)
        elif action == "clear_access_code":
            return await lock.handle_clear_access_code(hass, device_map, params)
        elif action == "get_access_codes":
            return await _handle_get_access_codes(hass, device_map, params)
        elif action == "get_state":
            return await state.handle_get_state(hass, device_map, params)
        elif action == "discover_devices":
            return await device_discovery.handle_discover_devices(hass, params)
        elif action == "list_entities":
            return await _handle_list_entities(hass, params)
        elif action == "get_capabilities":
            return await capability.handle_get_capabilities(hass, device_map, params)
        elif action == "get_diagnostics":
            return await diagnostics.handle_get_diagnostics(hass, device_map, params)
        elif action == "batch":
            return await batch.handle_batch(handle_command, params)
        else:
            raise ValueError(f"Unknown action: {action}")

    return handle_command


async def _handle_set_access_code(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Set access code with optional verification via Z-Wave readback."""
    device_id = params.get("device_id", "")
    entity_id = device_map.get_entity_id(device_id)
    if not entity_id:
        raise ValueError(f"Unknown device_id: {device_id}")

    slot = params.get("slot")
    code = params.get("code")
    verify = params.get("verify", True)

    if not slot or not code:
        raise ValueError("slot and code are required")

    if verify:
        return await zwave.set_and_verify_code(
            hass, entity_id, slot=slot, code=str(code)
        )
    else:
        return await lock.handle_set_access_code(hass, device_map, params)


async def _handle_get_access_codes(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Read code slot contents from a lock."""
    device_id = params.get("device_id", "")
    entity_id = device_map.get_entity_id(device_id)
    if not entity_id:
        raise ValueError(f"Unknown device_id: {device_id}")

    max_slots = params.get("max_slots", 30)
    slots = await zwave.read_code_slots(hass, entity_id, max_slots=max_slots)
    return {"slots": slots}


async def _handle_list_entities(
    hass: HomeAssistant,
    params: Dict[str, Any],
) -> list:
    """List entities in the format expected by the Orion list_ha_entities endpoint.

    Calls discover_devices internally and reformats the response to match the
    flat array format that Nimbus expects (entity_id, friendly_name, type, etc.).
    """
    result = await device_discovery.handle_discover_devices(hass, params)
    devices = result.get("devices", [])

    return [
        {
            "entity_id": d.get("external_id", ""),
            "friendly_name": d.get("name", ""),
            "type": d.get("type", ""),
            "manufacturer": d.get("manufacturer"),
            "model": d.get("model"),
            "integration": d.get("protocol"),
            "attributes": d.get("capabilities", []),
        }
        for d in devices
    ]
