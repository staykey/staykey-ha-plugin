"""Lock-code and capability actions find their lock through the device map.

A request names a Staykey ``device_id``. The entity it reaches is the one this
entry's device map holds for that id. A request's ``external_id`` is ignored,
so a request can only reach a device Staykey gave this entry.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))
from staykey.device_map import DeviceMap  # noqa: E402
from staykey.handlers import capability, lock  # noqa: E402
from staykey.services.lock_provider import (  # noqa: E402
    CapabilityInfo,
    ProviderResult,
)

MAPPED_DEVICE = "sk-front-door"
MAPPED_ENTITY = "lock.front_door"
OTHER_ENTITY = "lock.other_property_door"


def _run(coro):
    return asyncio.run(coro)


def _device_map() -> DeviceMap:
    device_map = DeviceMap()
    device_map.load_sync([{"device_id": MAPPED_DEVICE, "external_id": MAPPED_ENTITY}])
    return device_map


def _hass():
    return SimpleNamespace(states=SimpleNamespace(get=lambda _entity_id: None))


class _RecordingProvider:
    """Stands in for a LockProvider and records the entity each call names."""

    name = "zwave"

    def __init__(self):
        self.entities: list[str] = []

    async def set_code(self, _hass, entity_id, slot, _code):
        self.entities.append(entity_id)
        return ProviderResult(slot=slot, method="test", verified=True)

    async def clear_code(self, _hass, entity_id, slot):
        self.entities.append(entity_id)
        return ProviderResult(slot=slot, method="test", verified=True)

    async def read_codes(self, _hass, entity_id, max_slots=30):
        self.entities.append(entity_id)
        return []

    async def get_capabilities(self, _hass, entity_id):
        self.entities.append(entity_id)
        return CapabilityInfo(supports_access_codes=True, max_slots=30)


@pytest.fixture
def provider(monkeypatch):
    """Route every provider lookup to one recorder.

    ``lock`` and ``capability`` share one ``services.providers`` module, so a
    single patch covers both.
    """
    recording = _RecordingProvider()
    monkeypatch.setattr(
        lock.providers, "select_provider", lambda _hass, _entity_id: recording
    )
    return recording


CODE_ACTIONS = [
    pytest.param(
        lock.handle_set_access_code,
        {"code_slot": 5, "access_code": "1234"},
        id="set_lock_access_code",
    ),
    pytest.param(
        lock.handle_clear_access_code, {"code_slot": 5}, id="clear_lock_access_code"
    ),
    pytest.param(lock.handle_read_codes, {}, id="get_lock_access_codes"),
]


@pytest.mark.parametrize(("handler", "extra"), CODE_ACTIONS)
def test_code_action_reaches_the_mapped_lock_not_the_requested_entity(
    provider, handler, extra
):
    params = {"device_id": MAPPED_DEVICE, "external_id": OTHER_ENTITY, **extra}

    _run(handler(_hass(), _device_map(), params))

    assert provider.entities == [MAPPED_ENTITY]


@pytest.mark.parametrize(("handler", "extra"), CODE_ACTIONS)
def test_code_action_for_an_unmapped_device_is_an_error(provider, handler, extra):
    params = {"device_id": "sk-not-ours", "external_id": OTHER_ENTITY, **extra}

    with pytest.raises(ValueError, match="Unknown device_id: sk-not-ours"):
        _run(handler(_hass(), _device_map(), params))

    assert provider.entities == []


@pytest.mark.parametrize(("handler", "extra"), CODE_ACTIONS)
def test_code_action_naming_only_an_entity_is_an_error(provider, handler, extra):
    params = {"external_id": OTHER_ENTITY, **extra}

    with pytest.raises(ValueError, match="Unknown device_id"):
        _run(handler(_hass(), _device_map(), params))

    assert provider.entities == []


def test_capabilities_describe_the_mapped_lock_not_the_requested_entity(provider):
    params = {"device_id": MAPPED_DEVICE, "external_id": OTHER_ENTITY}

    result = _run(capability.handle_get_capabilities(_hass(), _device_map(), params))

    assert result["entity_id"] == MAPPED_ENTITY
    assert result["device_id"] == MAPPED_DEVICE
    assert provider.entities == [MAPPED_ENTITY]


@pytest.mark.parametrize(
    "params",
    [
        pytest.param(
            {"device_id": "sk-not-ours", "external_id": OTHER_ENTITY}, id="unmapped"
        ),
        pytest.param({"external_id": OTHER_ENTITY}, id="entity-only"),
    ],
)
def test_capabilities_for_a_device_outside_the_map_are_an_error(provider, params):
    with pytest.raises(ValueError, match="Unknown device_id"):
        _run(capability.handle_get_capabilities(_hass(), _device_map(), params))

    assert provider.entities == []
