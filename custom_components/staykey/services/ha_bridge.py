"""Bridge between Staykey command schemas and HA service calls.

Routes incoming commands to the appropriate handler based on action name.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Coroutine, Dict, Optional

from homeassistant.core import HomeAssistant

from ..device_map import DeviceMap
from ..handlers import (
    batch,
    capability,
    climate,
    cover,
    device_discovery,
    diagnostics,
    lock,
    state,
    switch,
)
from ..handlers.utils import ProgressFn
from ..services import zwave

LOGGER = logging.getLogger(__name__)

_ACTION_MAP = {
    "lock": lock.handle_lock,
    "unlock": lock.handle_unlock,
    "clear_access_code": lock.handle_clear_access_code,
    "clear_lock_access_code": lock.handle_clear_access_code,
    "get_state": state.handle_get_state,
    "get_capabilities": capability.handle_get_capabilities,
    "get_diagnostics": diagnostics.handle_get_diagnostics,
    "open_cover": cover.handle_open_cover,
    "close_cover": cover.handle_close_cover,
    "stop_cover": cover.handle_stop_cover,
    "turn_on": switch.handle_turn_on,
    "turn_off": switch.handle_turn_off,
    "set_temperature": climate.handle_set_temperature,
    "set_hvac_mode": climate.handle_set_hvac_mode,
}

_PROGRESS_ACTIONS = {"lock", "unlock", "open_cover", "close_cover"}

CommandHandler = Callable[
    [str, str, Dict[str, Any], Optional[ProgressFn]],
    Coroutine[Any, Any, Dict[str, Any]],
]


def create_command_handler(
    hass: HomeAssistant,
    device_map: DeviceMap,
) -> CommandHandler:
    """Create a command handler function that routes actions to handlers."""

    async def handle_command(
        action: str,
        request_id: str,
        params: Dict[str, Any],
        progress_fn: Optional[ProgressFn] = None,
    ) -> Dict[str, Any]:
        LOGGER.debug("Handling command: action=%s id=%s", action, request_id)

        handler = _ACTION_MAP.get(action)
        if handler is not None:
            if action in _PROGRESS_ACTIONS and progress_fn is not None:
                return await handler(hass, device_map, params, progress_fn=progress_fn)
            return await handler(hass, device_map, params)

        if action in ("set_access_code", "set_lock_access_code"):
            return await _handle_set_access_code(hass, device_map, params)
        if action in ("get_access_codes", "get_lock_access_codes"):
            return await _handle_get_access_codes(hass, device_map, params)
        if action == "discover_devices":
            return await device_discovery.handle_discover_devices(hass, params)
        if action == "list_entities":
            return await _handle_list_entities(hass, params)
        if action == "batch":
            return await batch.handle_batch(handle_command, params)

        raise ValueError(f"Unknown action: {action}")

    return handle_command


def _resolve_entity_id(
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> str:
    """Resolve HA entity_id from params.

    Orion sends ``external_id`` (the HA entity_id) for worker-driven
    actions, while dashboard actions send ``device_id`` (Staykey internal
    UUID that needs a device_map lookup).  Accept both conventions.
    """
    external_id = params.get("external_id", "")
    if external_id:
        return external_id

    device_id = params.get("device_id", "")
    entity_id = device_map.get_entity_id(device_id)
    if entity_id:
        return entity_id

    raise ValueError(
        f"Cannot resolve entity: device_id={device_id!r}, external_id={external_id!r}"
    )


async def _handle_set_access_code(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Set access code with optional verification via Z-Wave readback."""
    entity_id = _resolve_entity_id(device_map, params)

    slot = params.get("slot") or params.get("code_slot")
    code = params.get("code") or params.get("access_code")
    verify = params.get("verify") if "verify" in params else params.get("validate", True)

    if not slot or not code:
        raise ValueError("slot/code_slot and code/access_code are required")

    if verify:
        return await zwave.set_and_verify_code(
            hass, entity_id, slot=slot, code=str(code)
        )

    normalized = {**params, "device_id": params.get("device_id", ""), "slot": slot, "code": code}
    return await lock.handle_set_access_code(hass, device_map, normalized)


async def _handle_get_access_codes(
    hass: HomeAssistant,
    device_map: DeviceMap,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Read code slot contents from a lock."""
    entity_id = _resolve_entity_id(device_map, params)

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
