"""Generic HA service-call passthrough.

Lets the gateway (and therefore Orion or a curl-to-``/internal/command``)
fire arbitrary Home Assistant service calls without going through one of
the typed Staykey handlers in :mod:`..handlers.lock` /
:mod:`..handlers.switch` / etc.

## Why this exists

When debugging vendor-specific Matter / Z-Wave quirks (e.g. the Ultraloq
Bolt SE rejecting ``matter.set_lock_credential`` with an unmapped 0x85
status), it's enormously faster to iterate against the real lock by
poking different payload shapes from the dev machine than it is to
ship a plugin release and wait for HACS / restart.

The passthrough also makes the ``/internal/command`` interface useful
for Orion-side integration tests against a real HA — ``call_service``
becomes the primitive, and Staykey-typed actions become higher-level
conveniences on top of it.

## Security model

* The action is gated by the gateway WebSocket auth (``GATEWAY_TOKEN``)
  that's already required to reach this handler at all.
* It is NOT exposed via the Staykey REST webhook surface — it's
  reachable only over the agent gateway connection.
* It mirrors the privileges of the HA process Staykey is running
  inside, so it's no more dangerous than HA's own Developer Tools
  service caller.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from ..device_map import DeviceMap

LOGGER = logging.getLogger(__name__)


async def handle_ha_service_call(
    hass: HomeAssistant,
    device_map: DeviceMap,  # noqa: ARG001 - kept for handler signature uniformity
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Dispatch ``hass.services.async_call`` with the supplied payload.

    Expected ``params`` shape::

        {
          "domain": "matter",
          "service": "set_lock_credential",
          "service_data": { ... },         # what HA service expects
          "return_response": true|false,    # default true (we always
                                            # try to capture results)
          "blocking": true|false            # default true
        }

    Returns::

        {
          "domain": "matter",
          "service": "set_lock_credential",
          "response": { ... } | null,
          "service_data": { ... }            # echoed back, useful when
                                              # the caller batches
        }

    Raises ``ValueError`` for malformed input and ``HomeAssistantError``
    propagates verbatim so the caller sees the structured Matter
    status (translation_placeholders).
    """
    domain = params.get("domain")
    service = params.get("service")
    service_data = params.get("service_data") or {}
    return_response = params.get("return_response", True)
    blocking = params.get("blocking", True)

    if not isinstance(domain, str) or not domain:
        raise ValueError("ha_service_call: 'domain' is required")
    if not isinstance(service, str) or not service:
        raise ValueError("ha_service_call: 'service' is required")
    if not isinstance(service_data, dict):
        raise ValueError("ha_service_call: 'service_data' must be an object")

    LOGGER.info(
        "ha_service_call dispatch domain=%s service=%s "
        "return_response=%s blocking=%s data_keys=%s",
        domain,
        service,
        return_response,
        blocking,
        sorted(service_data.keys()),
    )

    try:
        if return_response:
            response = await hass.services.async_call(
                domain,
                service,
                service_data,
                blocking=blocking,
                return_response=True,
            )
        else:
            await hass.services.async_call(
                domain,
                service,
                service_data,
                blocking=blocking,
            )
            response = None
    except HomeAssistantError as exc:
        # Surface HA's structured error info (translation_placeholders,
        # translation_key) back to the caller — that's the whole point
        # of the passthrough for Matter debugging.
        placeholders = getattr(exc, "translation_placeholders", None)
        translation_key = getattr(exc, "translation_key", None)
        translation_domain = getattr(exc, "translation_domain", None)
        LOGGER.warning(
            "ha_service_call failed domain=%s service=%s "
            "translation_key=%s status=%s exc=%s",
            domain,
            service,
            translation_key,
            placeholders.get("status") if isinstance(placeholders, dict) else None,
            exc,
        )
        return {
            "domain": domain,
            "service": service,
            "service_data": service_data,
            "response": None,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "translation_domain": translation_domain,
                "translation_key": translation_key,
                "translation_placeholders": placeholders
                if isinstance(placeholders, dict)
                else None,
            },
        }

    LOGGER.info(
        "ha_service_call ok domain=%s service=%s response_keys=%s",
        domain,
        service,
        sorted(response.keys()) if isinstance(response, dict) else "<no-response>",
    )

    return {
        "domain": domain,
        "service": service,
        "service_data": service_data,
        "response": response,
    }
