"""Shared handler utilities."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from homeassistant.core import HomeAssistant

LOGGER = logging.getLogger(__name__)


async def wait_for_state(
    hass: HomeAssistant,
    entity_id: str,
    target_state: str,
    timeout: float,
    poll_interval: float = 0.5,
) -> str:
    """Poll entity state until it matches *target_state* or *timeout* elapses.

    Physical devices (Z-Wave locks, covers) report state asynchronously after
    a service call completes.  ``blocking=True`` only guarantees the command
    was dispatched — the entity state object may still hold the old value.

    Returns the entity state string once it matches, or whatever the current
    state is when the deadline is reached.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout

    state_obj = hass.states.get(entity_id)
    if state_obj and state_obj.state == target_state:
        return target_state

    while loop.time() < deadline:
        await asyncio.sleep(poll_interval)
        state_obj = hass.states.get(entity_id)
        if state_obj and state_obj.state == target_state:
            return target_state

    current = state_obj.state if state_obj else "unknown"
    LOGGER.warning(
        "Timed out waiting for %s to reach '%s' (current: '%s', timeout: %.1fs)",
        entity_id,
        target_state,
        current,
        timeout,
    )
    return current
