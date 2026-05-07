"""Bridge between Staykey command schemas and HA service calls.

Routes incoming commands to the appropriate handler based on action name.
Access-code commands (``set_lock_access_code`` / ``clear_lock_access_code``
/ ``get_lock_access_codes``) all go through ``handlers/lock.py``, which
in turn dispatches to the protocol-specific
:class:`LockProvider <.lock_provider.LockProvider>`.
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
    event_tap,
    lock,
    passthrough,
    state,
    switch,
)
from ..handlers.utils import ProgressFn

LOGGER = logging.getLogger(__name__)

_ACTION_MAP = {
    "lock": lock.handle_lock,
    "unlock": lock.handle_unlock,
    "set_access_code": lock.handle_set_access_code,
    "set_lock_access_code": lock.handle_set_access_code,
    "clear_access_code": lock.handle_clear_access_code,
    "clear_lock_access_code": lock.handle_clear_access_code,
    "get_access_codes": lock.handle_read_codes,
    "get_lock_access_codes": lock.handle_read_codes,
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
    "ha_service_call": passthrough.handle_ha_service_call,
    "tap_events": event_tap.handle_tap_events,
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

        if action == "discover_devices":
            return await device_discovery.handle_discover_devices(hass, params)
        if action == "list_entities":
            return await _handle_list_entities(hass, params)
        if action == "batch":
            return await batch.handle_batch(handle_command, params)

        raise ValueError(f"Unknown action: {action}")

    return handle_command


async def _handle_list_entities(
    hass: HomeAssistant,
    params: Dict[str, Any],
) -> list:
    """Flatten discovered devices for list_entities consumers.

    Wraps ``discover_devices`` and returns the flat array of entities the
    Staykey host API expects (entity id, friendly name, type, etc.).
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
