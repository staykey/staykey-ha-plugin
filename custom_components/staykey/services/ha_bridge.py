"""Bridge between Staykey command schemas and HA service calls.

Routes incoming commands to the appropriate handler based on action name.
Access-code commands (``set_lock_access_code`` / ``clear_lock_access_code``
/ ``get_lock_access_codes``) all go through ``handlers/lock.py``, which
in turn dispatches to the protocol-specific
:class:`LockProvider <.lock_provider.LockProvider>`.

Staykey support sends support actions (:data:`SUPPORT_ACTIONS`) through the
gateway to debug a Home Assistant. They are refused unless the Home Assistant
owner turned on the entry's "Allow support access" option
(``allow_support_actions``). The check runs in the dispatcher itself, which
``batch`` re-enters for each of its commands, so a batch cannot carry a
support action past it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

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

# Support and troubleshooting actions. Staykey support sends them through the
# gateway to debug a Home Assistant. They call any Home Assistant service,
# watch the event bus, read the codes stored on a lock, run discover_devices,
# or run a batch of other commands, so the dispatcher refuses each one unless
# the Home Assistant owner turns on "Allow support access" for the entry. The
# legacy set_access_code / clear_access_code aliases are here too, because
# Staykey sends the *_lock_access_code names. Listing entities is not gated:
# list_entities, get_state and get_diagnostics stay available whatever the
# option says, because Staykey uses them day to day.
SUPPORT_ACTIONS: frozenset[str] = frozenset(
    {
        "batch",
        "clear_access_code",
        "discover_devices",
        "get_access_codes",
        "get_lock_access_codes",
        "ha_service_call",
        "set_access_code",
        "tap_events",
    }
)


class SupportActionDisabledError(PermissionError):
    """A support action arrived while the entry's support actions are off."""

    def __init__(self, action: str) -> None:
        super().__init__(
            f"Action {action!r} needs Allow support access, which is off for "
            "this Staykey entry"
        )


CommandHandler = Callable[
    [str, str, dict[str, Any], ProgressFn | None],
    Coroutine[Any, Any, dict[str, Any]],
]


def create_command_handler(
    hass: HomeAssistant,
    device_map: DeviceMap,
    *,
    allow_support_actions: bool = False,
) -> CommandHandler:
    """Create a command handler function that routes actions to handlers.

    ``allow_support_actions`` mirrors the entry option of the same name. It is
    off unless the caller says otherwise.
    """

    async def handle_command(
        action: str,
        request_id: str,
        params: dict[str, Any],
        progress_fn: ProgressFn | None = None,
    ) -> dict[str, Any]:
        LOGGER.debug("Handling command: action=%s id=%s", action, request_id)

        if action in SUPPORT_ACTIONS and not allow_support_actions:
            raise SupportActionDisabledError(action)

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
    params: dict[str, Any],
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
