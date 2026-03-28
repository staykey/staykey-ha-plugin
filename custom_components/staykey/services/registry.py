"""Device/entity registry access for drift detection and capability discovery."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

LOGGER = logging.getLogger(__name__)


def resolve_entity_by_unique_id(
    hass: HomeAssistant, unique_id: str
) -> Optional[str]:
    """Look up an entity_id by its unique_id in the entity registry."""
    entity_reg = er.async_get(hass)
    for entry in entity_reg.entities.values():
        if entry.unique_id == unique_id:
            return entry.entity_id
    return None


def resolve_device_by_identifiers(
    hass: HomeAssistant, device_identifiers: List[List[str]]
) -> Optional[Dict[str, Any]]:
    """Look up a device by its identifiers in the device registry."""
    device_reg = dr.async_get(hass)
    id_sets = {tuple(ident) for ident in device_identifiers}

    for device in device_reg.devices.values():
        if device.identifiers & id_sets:
            return {
                "device_id": device.id,
                "name": device.name_by_user or device.name,
                "manufacturer": device.manufacturer,
                "model": device.model,
                "identifiers": [list(i) for i in device.identifiers],
            }
    return None


def get_entity_details(
    hass: HomeAssistant, entity_id: str
) -> Optional[Dict[str, Any]]:
    """Get full entity registry details for a given entity_id."""
    entity_reg = er.async_get(hass)
    entry = entity_reg.async_get(entity_id)
    if not entry:
        return None

    return {
        "entity_id": entry.entity_id,
        "unique_id": entry.unique_id,
        "domain": entry.domain,
        "platform": entry.platform,
        "device_id": entry.device_id,
        "name": entry.name or entry.original_name,
        "disabled": entry.disabled,
    }
