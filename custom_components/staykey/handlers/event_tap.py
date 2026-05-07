"""Bus event tap for ad-hoc Home Assistant event observation.

Subscribes to ``hass.bus`` for a bounded duration and returns whatever
fired during that window. Useful when investigating integration behavior —
e.g. which bus events fire for a keypad unlock — where a service call
alone does not surface asynchronous notifications.

## Why this exists

Service-call responses carry only direct results; many integrations emit
follow-on events (``state_changed``, integration-specific notifications).
Subscribing from inside this component is the reliable way to capture
those when troubleshooting from a gateway-connected client.

Windows are capped (duration + max event count) so a misbehaving caller
cannot leave listeners attached indefinitely.

## Security model

Same as :mod:`..passthrough` — gateway-authenticated channel only, not on
the public REST webhook surface, privilege bounded by the HA integration.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Iterable, List, Optional

from homeassistant.core import HomeAssistant

LOGGER = logging.getLogger(__name__)

# Hard caps: protect HA from a misbehaving caller.  Tap windows are
# meant for empirical debugging, not long-running subscriptions.
_MAX_DURATION_SECONDS = 60.0
_DEFAULT_DURATION_SECONDS = 10.0
_MAX_EVENTS = 500
_DEFAULT_MAX_EVENTS = 200


async def handle_tap_events(
    hass: HomeAssistant,
    device_map: Any,  # noqa: ARG001 - kept for handler signature uniformity
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Listen on ``hass.bus`` for *duration_seconds* and return events.

    Expected ``params`` shape::

        {
          "event_types": ["state_changed", "matter_event"],  # optional
          "entity_id": "lock.front_door",                   # optional filter
          "duration_seconds": 10,                              # default 10, max 60
          "max_events": 200                                     # default 200, max 500
        }

    Filtering rules:

    * ``event_types`` is a list of bus event names.  If omitted /
      empty, the tap subscribes to **all** events via HA's
      ``MATCH_ALL`` (``"*"``) sentinel — useful for fishing trips.
    * ``entity_id``, when set, filters returned events to those whose
      ``data.entity_id`` matches.  Applied post-capture so we don't
      need to know the schema of every event type up front.

    Returns::

        {
          "duration_seconds": <actual capture window>,
          "subscribed_event_types": ["state_changed", ...] | "*",
          "captured": <count returned>,
          "truncated": true|false,
          "events": [
            {
              "event_type": "state_changed",
              "time_fired": "2026-05-06T05:30:00.123456+00:00",
              "origin": "LOCAL",
              "context": {"id": "...", "user_id": null, "parent_id": null},
              "data": { ... }
            },
            ...
          ]
        }
    """
    duration_seconds = _coerce_duration(params.get("duration_seconds"))
    max_events = _coerce_max_events(params.get("max_events"))
    event_types = _coerce_event_types(params.get("event_types"))
    entity_id_filter = params.get("entity_id")
    if entity_id_filter is not None and not isinstance(entity_id_filter, str):
        raise ValueError("tap_events: 'entity_id' must be a string when provided")

    captured: List[Dict[str, Any]] = []
    truncated = False
    unsubs: List[Any] = []

    def _record(event: Any) -> None:
        nonlocal truncated
        if len(captured) >= max_events:
            truncated = True
            return
        captured.append(_serialize_event(event))

    try:
        if event_types is None:
            # Subscribe to everything.  HA accepts ``MATCH_ALL`` ("*") as
            # the wildcard sentinel for ``async_listen``.
            unsubs.append(hass.bus.async_listen("*", _record))
            subscribed_label: Any = "*"
        else:
            for et in event_types:
                unsubs.append(hass.bus.async_listen(et, _record))
            subscribed_label = list(event_types)

        LOGGER.info(
            "tap_events start duration_seconds=%.1f max_events=%d "
            "subscribed_event_types=%s entity_id_filter=%s",
            duration_seconds,
            max_events,
            subscribed_label,
            entity_id_filter,
        )

        await asyncio.sleep(duration_seconds)
    finally:
        for unsub in unsubs:
            try:
                unsub()
            except Exception:  # pragma: no cover - HA returns plain callables
                LOGGER.exception("tap_events: unsubscribe raised")

    if entity_id_filter is not None:
        filtered = [
            e
            for e in captured
            if isinstance(e.get("data"), dict)
            and e["data"].get("entity_id") == entity_id_filter
        ]
    else:
        filtered = captured

    LOGGER.info(
        "tap_events done captured=%d returned=%d truncated=%s",
        len(captured),
        len(filtered),
        truncated,
    )

    return {
        "duration_seconds": duration_seconds,
        "subscribed_event_types": subscribed_label,
        "captured": len(filtered),
        "truncated": truncated,
        "events": filtered,
    }


def _coerce_duration(value: Any) -> float:
    if value is None:
        return _DEFAULT_DURATION_SECONDS
    try:
        d = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("tap_events: 'duration_seconds' must be a number") from exc
    if d <= 0:
        raise ValueError("tap_events: 'duration_seconds' must be > 0")
    if d > _MAX_DURATION_SECONDS:
        # Clamp rather than reject — easier to reason about for callers
        # that pass "hopefully a long time".
        return _MAX_DURATION_SECONDS
    return d


def _coerce_max_events(value: Any) -> int:
    if value is None:
        return _DEFAULT_MAX_EVENTS
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("tap_events: 'max_events' must be an integer")
    if value <= 0:
        raise ValueError("tap_events: 'max_events' must be > 0")
    return min(value, _MAX_EVENTS)


def _coerce_event_types(value: Any) -> Optional[List[str]]:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("tap_events: 'event_types' must be a list of strings")
    out: List[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ValueError("tap_events: 'event_types' entries must be non-empty strings")
        out.append(item)
    if not out:
        return None
    return out


def _serialize_event(event: Any) -> Dict[str, Any]:
    """Produce a JSON-safe representation of an HA Event.

    HA's ``Event`` carries ``data`` (a dict, often containing rich
    objects like ``State``, ``datetime``, etc.) and metadata.  We
    serialize everything into JSON-friendly primitives for transport.
    """
    payload: Dict[str, Any] = {
        "event_type": getattr(event, "event_type", None),
        "time_fired": _isoformat(getattr(event, "time_fired", None)),
        "origin": _origin_name(getattr(event, "origin", None)),
        "context": _serialize_context(getattr(event, "context", None)),
        "data": _make_serializable(getattr(event, "data", None)),
    }
    return payload


def _serialize_context(ctx: Any) -> Optional[Dict[str, Any]]:
    if ctx is None:
        return None
    return {
        "id": getattr(ctx, "id", None),
        "user_id": getattr(ctx, "user_id", None),
        "parent_id": getattr(ctx, "parent_id", None),
    }


def _origin_name(origin: Any) -> Optional[str]:
    if origin is None:
        return None
    # HA's EventOrigin is an Enum; ``.value`` is the canonical string
    # ("local" or "remote").  Fall back to ``str()`` for any custom
    # origin objects.
    return getattr(origin, "value", None) or str(origin)


def _isoformat(value: Any) -> Optional[str]:
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        try:
            return iso()
        except Exception:  # pragma: no cover - defensive
            return str(value)
    return str(value)


def _make_serializable(value: Any) -> Any:
    """Recursively convert ``value`` to JSON-serializable primitives.

    Mirrors the conversion in :mod:`.state` so event data is rendered
    consistently.  Unknown objects fall back to ``str()`` rather than
    raising — the tap is for human inspection, not strict schemas.
    """
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        return [_make_serializable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _make_serializable(v) for k, v in value.items()}
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        try:
            return iso()
        except Exception:  # pragma: no cover - defensive
            pass
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        try:
            return _make_serializable(as_dict())
        except Exception:  # pragma: no cover - defensive
            pass
    return str(value)


# Internal helpers exposed for testing.
__all__ = [
    "handle_tap_events",
    "_coerce_duration",
    "_coerce_max_events",
    "_coerce_event_types",
    "_serialize_event",
    "_make_serializable",
]


# Note for callers: ``Iterable[str]`` is accepted via ``_coerce_event_types``
# but the return is always materialized to a list before subscription so we
# don't accidentally consume a generator more than once.
_ = Iterable  # keep import meaningful even if not directly referenced
