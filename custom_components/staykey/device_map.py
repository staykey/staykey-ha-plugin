"""In-memory device map for Staykey device ID <-> HA entity ID mapping."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Set

LOGGER = logging.getLogger(__name__)


class DeviceMap:
    """Manages the mapping between Staykey device IDs and HA entity IDs.

    No persistence — rebuilt from device_map_sync on every connect.
    """

    def __init__(self) -> None:
        self._forward: Dict[str, Dict[str, Any]] = {}
        self._reverse: Dict[str, str] = {}
        self._unique_id_index: Dict[str, str] = {}
        self._device_identifiers_index: Dict[str, str] = {}

    @property
    def tracked_entities(self) -> Set[str]:
        return set(self._reverse.keys())

    @property
    def tracked_device_ids(self) -> list[str]:
        return list(self._forward.keys())

    def load_sync(self, devices: list[Dict[str, Any]]) -> None:
        """Full replacement from a device_map_sync message."""
        self._forward.clear()
        self._reverse.clear()
        self._unique_id_index.clear()
        self._device_identifiers_index.clear()

        for device in devices:
            self._add_device(device)

        LOGGER.info("Device map synced: %d devices tracked", len(self._forward))

    def apply_update(self, action: str, device: Optional[Dict[str, Any]] = None, device_id: Optional[str] = None) -> None:
        """Apply an incremental device_map_update."""
        if action == "add" and device:
            self._add_device(device)
            LOGGER.info("Device added to map: %s -> %s", device.get("device_id"), device.get("external_id"))
        elif action == "remove" and device_id:
            self._remove_device(device_id)
            LOGGER.info("Device removed from map: %s", device_id)
        elif action == "update" and device:
            self._remove_device(device["device_id"])
            self._add_device(device)
            LOGGER.info("Device updated in map: %s -> %s", device.get("device_id"), device.get("external_id"))

    def get_entity_id(self, device_id: str) -> Optional[str]:
        """Look up HA entity_id by Staykey device_id (for command translation)."""
        info = self._forward.get(device_id)
        return info["external_id"] if info else None

    def get_device_id(self, entity_id: str) -> Optional[str]:
        """Look up Staykey device_id by HA entity_id (for event filtering)."""
        return self._reverse.get(entity_id)

    def get_device_info(self, device_id: str) -> Optional[Dict[str, Any]]:
        return self._forward.get(device_id)

    def get_device_by_unique_id(self, unique_id: str) -> Optional[str]:
        return self._unique_id_index.get(unique_id)

    def update_entity_id(self, device_id: str, old_entity_id: str, new_entity_id: str) -> None:
        """Handle entity_id rename: update both maps."""
        info = self._forward.get(device_id)
        if info:
            info["external_id"] = new_entity_id
            self._reverse.pop(old_entity_id, None)
            self._reverse[new_entity_id] = device_id

    def is_tracked(self, entity_id: str) -> bool:
        return entity_id in self._reverse

    def _add_device(self, device: Dict[str, Any]) -> None:
        device_id = device["device_id"]
        external_id = device.get("external_id", "")
        self._forward[device_id] = device
        if external_id:
            self._reverse[external_id] = device_id

        platform_ids = device.get("platform_identifiers", {})
        if uid := platform_ids.get("unique_id"):
            self._unique_id_index[uid] = device_id
        if dev_ids := platform_ids.get("device_identifiers"):
            key = _identifiers_key(dev_ids)
            if key:
                self._device_identifiers_index[key] = device_id

    def _remove_device(self, device_id: str) -> None:
        info = self._forward.pop(device_id, None)
        if info:
            external_id = info.get("external_id", "")
            if external_id:
                self._reverse.pop(external_id, None)

            platform_ids = info.get("platform_identifiers", {})
            if uid := platform_ids.get("unique_id"):
                self._unique_id_index.pop(uid, None)
            if dev_ids := platform_ids.get("device_identifiers"):
                key = _identifiers_key(dev_ids)
                if key:
                    self._device_identifiers_index.pop(key, None)


def _identifiers_key(identifiers: Any) -> Optional[str]:
    """Convert device_identifiers to a hashable key for indexing."""
    if isinstance(identifiers, list):
        try:
            return str(sorted(tuple(i) if isinstance(i, list) else i for i in identifiers))
        except TypeError:
            return str(identifiers)
    return str(identifiers) if identifiers else None
