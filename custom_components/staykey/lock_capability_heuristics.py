"""Pure helpers for inferring lock capabilities from HA entity attributes.

Kept HA-import-free so they're trivially testable without a running
Home Assistant install.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


def lock_supports_access_codes(
    attributes: Mapping[str, Any], protocol: Optional[str]
) -> bool:
    """Decide whether a lock entity supports access codes.

    The HA ``lock`` ``supported_features`` bit 1 historically indicated
    access-code capability for Z-Wave but is set independently for Matter
    (it tracks unbolt support, not credential management).  So we branch:

    * Z-Wave: trust the existing ``supported_features & 1`` heuristic.
    * Matter: the HA 2026.4 Matter integration registers per-entity
      services for the lock manager (``matter.set_lock_credential``
      etc.) on every Matter lock; if the entity is on the Matter
      integration, advertise access-code support.  Locks that don't
      actually support credentials will fail the call at runtime, which
      Orion already handles via ``mark_assignment_skipped``.
    * Other protocols: keep the legacy heuristic.
    """
    if protocol == "matter":
        return True

    features = attributes.get("supported_features")
    if isinstance(features, int) and features & 1:
        return True
    return False
