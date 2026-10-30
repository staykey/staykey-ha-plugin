"""Tests for the Z-Wave provider, which only uses public ``zwave_js`` services.

After setting a code the provider asks the lock to report the slot
(``zwave_js.invoke_cc_api``, User Code CC ``get``) and reads it back with
``zwave_js.get_lock_usercode``.  That service reads Z-Wave JS's cache, which
only changes when the lock reports a slot, so a slot that reads the same
before and after the write was not confirmed by the lock.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

# HA module stubs and sys.path setup live in conftest.py.

ENTITY_ID = "lock.front_door"
SLOT = 5
PIN = "482916"
OLD_PIN = "1234"


class _NotFoundError(Exception):
    """Stand-in for the HomeAssistantError get_lock_usercode raises."""


class _FakeZwaveJs:
    """The ``zwave_js`` services as seen from another integration.

    ``cache`` is Z-Wave JS's view of each slot (``{"usercode", "in_use"}``).
    Like Z-Wave JS, ``set_lock_usercode`` does not change it: only a Report
    from the lock does.  ``lock_reports`` maps a slot to what the lock
    reports when asked; a slot that isn't there gets no answer.
    """

    def __init__(self, cache=None, lock_reports=None, invoke_error=None):
        self.cache: dict[int, dict] = dict(cache or {})
        self.lock_reports: dict[int, dict] = dict(lock_reports or {})
        self.invoke_error = invoke_error
        self.calls: list[tuple[str, str, dict]] = []

    async def async_call(
        self, domain, service, data, *, blocking=True, return_response=False
    ):
        self.calls.append((domain, service, dict(data)))
        assert domain == "zwave_js"
        assert data["entity_id"] == ENTITY_ID

        if service == "set_lock_usercode":
            return None
        if service == "invoke_cc_api":
            if self.invoke_error is not None:
                raise self.invoke_error
            assert data["command_class"] == 99
            assert data["method_name"] == "get"
            (slot,) = data["parameters"]
            if slot in self.lock_reports:
                self.cache[slot] = dict(self.lock_reports[slot])
            return None
        if service == "get_lock_usercode":
            assert return_response is True
            if "code_slot" in data:
                slot = data["code_slot"]
                if slot not in self.cache:
                    raise _NotFoundError(f"slot {slot} not found")
                slots = {slot: self.cache[slot]}
            else:
                slots = self.cache
            return {ENTITY_ID: {str(k): dict(v) for k, v in slots.items()}}
        raise AssertionError(f"unexpected service {service}")


def _slot(code, in_use=True):
    return {"usercode": code, "in_use": in_use}


@pytest.fixture
def zwave(monkeypatch, caplog):
    from services.providers import zwave

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    caplog.set_level(logging.DEBUG)
    return zwave


@pytest.fixture
def set_code(zwave):
    """Run ``ZwaveLockProvider.set_code`` for ``PIN`` against a fake lock."""

    def _set_code(services: _FakeZwaveJs):
        hass = SimpleNamespace(services=services)
        return asyncio.run(
            zwave.ZwaveLockProvider().set_code(hass, ENTITY_ID, SLOT, PIN)
        )

    return _set_code


def test_set_code_verifies_when_lock_reports_the_code(set_code, caplog):
    services = _FakeZwaveJs(
        cache={SLOT: _slot(OLD_PIN)}, lock_reports={SLOT: _slot(PIN)}
    )

    result = set_code(services)

    assert [service for _d, service, _data in services.calls] == [
        "get_lock_usercode",
        "set_lock_usercode",
        "invoke_cc_api",
        "get_lock_usercode",
    ]
    assert services.calls[1][2] == {
        "entity_id": ENTITY_ID,
        "code_slot": SLOT,
        "usercode": PIN,
    }
    assert services.calls[2][2] == {
        "entity_id": ENTITY_ID,
        "command_class": 99,
        "method_name": "get",
        "parameters": [SLOT],
    }
    assert result.verified is True
    assert result.attempts == 1
    assert result.error is None
    assert PIN not in caplog.text


def test_set_code_verifies_a_slot_read_for_the_first_time(set_code):
    services = _FakeZwaveJs(lock_reports={SLOT: _slot(PIN)})

    result = set_code(services)

    assert result.verified is True


@pytest.mark.parametrize(
    ("report", "expected_error"),
    [
        pytest.param(
            _slot("9999"),
            "Slot 5 readback mismatch (expected_length=6, actual_length=4)",
            id="other-code",
        ),
        pytest.param(
            _slot("", in_use=False),
            "Slot 5 readback mismatch (expected_length=6, actual_length=0)",
            id="empty-slot",
        ),
        pytest.param(
            _slot(PIN, in_use=False),
            "Slot 5 readback mismatch (expected_length=6, actual_length=6)",
            id="code-disabled",
        ),
    ],
)
def test_set_code_reports_mismatch_when_lock_reports_other_contents(
    set_code, caplog, report, expected_error
):
    services = _FakeZwaveJs(cache={SLOT: _slot(OLD_PIN)}, lock_reports={SLOT: report})

    result = set_code(services)

    assert result.verified is False
    assert result.error == expected_error
    assert result.attempts == 3
    assert PIN not in caplog.text


def test_set_code_reports_unconfirmed_when_lock_does_not_answer(set_code, caplog):
    services = _FakeZwaveJs(cache={SLOT: _slot(OLD_PIN)})

    result = set_code(services)

    assert result.verified is False
    assert result.error == (
        "Slot 5: lock did not confirm the code (no response, or slot unchanged)"
    )
    assert PIN not in caplog.text


def test_set_code_reports_unreadable_when_slot_never_reported(set_code):
    services = _FakeZwaveJs()

    result = set_code(services)

    assert result.verified is False
    assert result.error == "Slot 5 could not be read from lock"


def test_set_code_reports_unreadable_when_report_request_fails(set_code, caplog):
    services = _FakeZwaveJs(
        cache={SLOT: _slot(OLD_PIN)},
        lock_reports={SLOT: _slot(PIN)},
        invoke_error=RuntimeError("node is dead"),
    )

    result = set_code(services)

    assert result.verified is False
    assert result.error == "Slot 5 could not be read from lock"
    assert PIN not in caplog.text


def test_read_codes_lists_slots_and_hides_codes_of_unused_slots(zwave):
    services = _FakeZwaveJs(
        cache={
            1: _slot(PIN),
            2: _slot(OLD_PIN, in_use=False),
            3: _slot(None, in_use=None),
            31: _slot("5555"),
        }
    )
    hass = SimpleNamespace(services=services)

    slots = asyncio.run(zwave.ZwaveLockProvider().read_codes(hass, ENTITY_ID))

    assert [(s.slot, s.occupied, s.code) for s in slots] == [
        (1, True, PIN),
        (2, False, None),
        (3, False, None),
    ]


def test_read_codes_returns_empty_when_service_fails(zwave):
    class _Failing:
        async def async_call(self, *_args, **_kwargs):
            raise RuntimeError("not a zwave_js lock")

    hass = SimpleNamespace(services=_Failing())

    assert asyncio.run(zwave.ZwaveLockProvider().read_codes(hass, ENTITY_ID)) == []


def test_get_capabilities_counts_slots_from_get_lock_usercode(zwave, monkeypatch):
    async def _no_node_info(_hass, _entity_id):
        return {"node_id": 7}

    monkeypatch.setattr(zwave, "get_node_info", _no_node_info)
    services = _FakeZwaveJs(cache={n: _slot(None, in_use=False) for n in range(1, 21)})
    hass = SimpleNamespace(services=services)

    caps = asyncio.run(zwave.ZwaveLockProvider().get_capabilities(hass, ENTITY_ID))

    assert caps.supports_access_codes is True
    assert caps.max_slots == 20
    assert caps.extra == {
        "node_id": 7,
        "supports_user_codes": True,
        "max_code_slots": 20,
    }


def test_get_capabilities_without_user_codes(zwave, monkeypatch):
    async def _no_node_info(_hass, _entity_id):
        return None

    monkeypatch.setattr(zwave, "get_node_info", _no_node_info)
    hass = SimpleNamespace(services=_FakeZwaveJs())

    caps = asyncio.run(zwave.ZwaveLockProvider().get_capabilities(hass, ENTITY_ID))

    assert caps.supports_access_codes is False
    assert caps.max_slots is None


def test_get_node_info_reads_registry_and_zwave_js_sensors(zwave, monkeypatch):
    lock_entry = SimpleNamespace(device_id="dev1")
    device = SimpleNamespace(
        id="dev1",
        identifiers={("zwave_js", "3967032004-7"), ("zwave_js", "3967032004-7-99")},
        manufacturer="Yale",
        model="YRD256",
        sw_version="1.2",
    )
    sensors = [
        SimpleNamespace(
            platform="zwave_js",
            unique_id="3967032004.7.node_status",
            entity_id="sensor.front_door_node_status",
        ),
        SimpleNamespace(
            platform="zwave_js",
            unique_id="3967032004.7.statistics_commands_tx",
            entity_id="sensor.front_door_commands_tx",
        ),
        SimpleNamespace(
            platform="zwave_js",
            unique_id="3967032004.7.statistics_commands_rx",
            entity_id="sensor.front_door_commands_rx",
        ),
        SimpleNamespace(
            platform="other",
            unique_id="x.node_status",
            entity_id="sensor.other",
        ),
    ]
    states = {
        "sensor.front_door_node_status": "alive",
        "sensor.front_door_commands_tx": "42",
        "sensor.front_door_commands_rx": "unavailable",
        "sensor.other": "dead",
    }

    entity_reg = SimpleNamespace(
        async_get=lambda entity_id: lock_entry if entity_id == ENTITY_ID else None
    )
    device_reg = SimpleNamespace(async_get=lambda device_id: device)
    monkeypatch.setattr(zwave.er, "async_get", lambda _hass: entity_reg)
    monkeypatch.setattr(zwave.dr, "async_get", lambda _hass: device_reg)
    monkeypatch.setattr(
        zwave.er,
        "async_entries_for_device",
        lambda _reg, device_id: sensors if device_id == "dev1" else [],
        raising=False,
    )
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda eid: (
                SimpleNamespace(state=states[eid]) if eid in states else None
            )
        )
    )

    info = asyncio.run(zwave.get_node_info(hass, ENTITY_ID))

    assert info == {
        "node_id": 7,
        "manufacturer": "Yale",
        "model": "YRD256",
        "firmware": "1.2",
        "status": "alive",
        "statistics": {"commands_tx": 42},
    }


def test_get_node_info_is_none_off_zwave(zwave, monkeypatch):
    lock_entry = SimpleNamespace(device_id="dev1")
    device = SimpleNamespace(id="dev1", identifiers={("matter", "abc")})
    monkeypatch.setattr(
        zwave.er,
        "async_get",
        lambda _hass: SimpleNamespace(async_get=lambda _e: lock_entry),
    )
    monkeypatch.setattr(
        zwave.dr,
        "async_get",
        lambda _hass: SimpleNamespace(async_get=lambda _d: device),
    )

    assert asyncio.run(zwave.get_node_info(SimpleNamespace(), ENTITY_ID)) is None
