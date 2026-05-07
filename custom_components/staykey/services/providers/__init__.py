"""Provider selection for the protocol-agnostic LockProvider abstraction.

Inspects the device-registry identifiers behind a HA ``entity_id`` and
returns the right provider.  Inferring from ``entity_id`` itself is
unreliable for Matter, which is why we walk entity_registry ->
device_registry and read ``device.identifiers`` (the same source
``handlers/device_discovery.py`` ``_infer_protocol`` reads).
"""

from __future__ import annotations

import logging
from typing import Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from ..lock_provider import LockProvider, UnsupportedProtocolError
from . import matter as matter_provider
from . import zwave as zwave_provider

LOGGER = logging.getLogger(__name__)

_ZWAVE = zwave_provider.ZwaveLockProvider()
_MATTER = matter_provider.MatterLockProvider()

_DOMAIN_TO_PROVIDER = {
    "zwave_js": _ZWAVE,
    "matter": _MATTER,
}


def select_provider(hass: HomeAssistant, entity_id: str) -> LockProvider:
    """Select a :class:`LockProvider` from the device-registry identifiers.

    Raises :class:`UnsupportedProtocolError` if the entity is on an
    integration we don't have a provider for.
    """
    protocol = _infer_integration(hass, entity_id)
    if protocol is None:
        raise UnsupportedProtocolError(
            f"Cannot determine integration for entity {entity_id}"
        )

    provider = _DOMAIN_TO_PROVIDER.get(protocol)
    if provider is None:
        raise UnsupportedProtocolError(
            f"No LockProvider registered for protocol {protocol!r} (entity {entity_id})"
        )

    LOGGER.debug("Selected %s provider for %s", provider.name, entity_id)
    return provider


def _infer_integration(hass: HomeAssistant, entity_id: str) -> Optional[str]:
    """Return the leading identifier domain (e.g. 'zwave_js', 'matter')
    for the device backing *entity_id*, or None if it can't be resolved.
    """
    entity_reg = er.async_get(hass)
    entity_entry = entity_reg.async_get(entity_id)
    if not entity_entry or not entity_entry.device_id:
        return None

    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(entity_entry.device_id)
    if not device or not device.identifiers:
        return None

    for ident in device.identifiers:
        parts = list(ident)
        if parts:
            domain = str(parts[0]).lower()
            if domain in _DOMAIN_TO_PROVIDER:
                return domain
    return None


__all__ = ["select_provider"]
