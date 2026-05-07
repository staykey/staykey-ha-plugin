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
    * Matter: prefer concrete signals before falling back to the
      "registered service" assumption. The Matter integration
      registers ``matter.set_lock_credential`` on every Matter lock
      regardless of whether the device actually supports PIN credentials,
      so the bare presence of the service is a poor signal. Locks that
      don't support PIN management end up appearing in pickers and
      failing at programming time — bad UX. Use these signals in order:
        1. ``supported_credential_types`` includes ``"pin"`` — the
           Matter ``DoorLock`` cluster reports this when the lock
           advertises PIN support.
        2. ``max_pin_users`` / ``max_users`` is a positive int — the
           lock claims at least one PIN user slot.
        3. Fallback to ``supports_user_management`` flag if HA exposes it.
      If none of these are present, return ``False``. Locks the
      heuristic gets wrong (e.g. report PIN support but reject SetCredential)
      will still surface as a runtime failure; the host platform can
      then mark the device as unsuitable and stop scheduling codes for it.
    * Other protocols: keep the legacy heuristic.
    """
    if protocol == "matter":
        supported_types = attributes.get("supported_credential_types")
        if isinstance(supported_types, (list, tuple)) and any(
            isinstance(t, str) and t.lower() == "pin" for t in supported_types
        ):
            return True

        for key in ("max_pin_users", "max_users"):
            value = attributes.get(key)
            if isinstance(value, int) and value > 0:
                return True

        if attributes.get("supports_user_management") is True:
            return True

        return False

    features = attributes.get("supported_features")
    if isinstance(features, int) and features & 1:
        return True
    return False
