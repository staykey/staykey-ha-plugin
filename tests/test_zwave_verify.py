"""Tests for the Z-Wave provider's set-and-verify read-back.

After setting a code the provider reads the slot back with a User Code CC
Get.  Only the lock's Report says what the slot holds: when the lock sends
no Report, zwave-js returns nothing and the driver's ValueDB still holds
whatever it cached earlier, so a silent lock must not be reported as a lock
holding a different code.
"""

from __future__ import annotations

import asyncio
import enum
import logging
from types import SimpleNamespace

import pytest

# HA module stubs and sys.path setup live in conftest.py.

ENTITY_ID = "lock.front_door"
SLOT = 5
PIN = "482916"


class _CommandClass(enum.IntEnum):
    """Stand-in for ``zwave_js_server.const.CommandClass``."""

    USER_CODE = 99


class _FakeLockNode:
    """Z-Wave ``Node`` stand-in whose lock answers User Code CC Gets.

    ``reports`` maps a slot to the Report the lock sends back
    (``{"userIdStatus": ..., "userCode": ...}``); a slot that isn't there
    gets no Report.  Like ``Node.async_invoke_cc_api``, the CC API's return
    value only comes back when the caller waits for the result, and ``None``
    stands for no Report.
    """

    def __init__(self, reports: dict[int, dict]):
        self.reports = reports

    async def async_invoke_cc_api(
        self, command_class, method_name, *args, wait_for_result=None
    ):
        if command_class != _CommandClass.USER_CODE or method_name != "get":
            return None
        if not wait_for_result:
            return None
        (slot,) = args
        return self.reports.get(slot)


class _RecordingServices:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    async def async_call(self, domain, service, data, *, blocking=True):
        self.calls.append((domain, service, dict(data)))


@pytest.fixture
def set_code(monkeypatch, caplog):
    """Run ``ZwaveLockProvider.set_code`` for ``PIN`` against a fake lock."""
    from services.providers import zwave

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(zwave, "HAS_ZWAVE_LIB", True)
    monkeypatch.setattr(zwave, "CommandClass", _CommandClass, raising=False)
    caplog.set_level(logging.DEBUG)

    def _set_code(node: _FakeLockNode):
        monkeypatch.setattr(
            zwave, "_get_zwave_node_for_entity", lambda _hass, _entity_id: node
        )
        hass = SimpleNamespace(services=_RecordingServices())
        result = asyncio.run(
            zwave.ZwaveLockProvider().set_code(hass, ENTITY_ID, SLOT, PIN)
        )
        return hass, result

    return _set_code


def test_set_code_verifies_when_lock_reports_the_code(set_code, caplog):
    node = _FakeLockNode({SLOT: {"userIdStatus": 1, "userCode": PIN}})

    hass, result = set_code(node)

    assert hass.services.calls == [
        (
            "zwave_js",
            "set_lock_usercode",
            {"entity_id": ENTITY_ID, "code_slot": SLOT, "usercode": PIN},
        )
    ]
    assert result.verified is True
    assert result.attempts == 1
    assert result.error is None
    assert PIN not in caplog.text


@pytest.mark.parametrize(
    ("report", "expected_error"),
    [
        pytest.param(
            {"userIdStatus": 1, "userCode": "1234"},
            "Slot 5 readback mismatch (expected_length=6, actual_length=4)",
            id="other-code",
        ),
        pytest.param(
            {"userIdStatus": 0, "userCode": ""},
            "Slot 5 readback mismatch (expected_length=6, actual_length=0)",
            id="empty-slot",
        ),
        pytest.param(
            {"userIdStatus": 2, "userCode": PIN},
            "Slot 5 readback mismatch (expected_length=6, actual_length=6)",
            id="code-disabled",
        ),
    ],
)
def test_set_code_reports_mismatch_when_lock_reports_other_contents(
    set_code, caplog, report, expected_error
):
    node = _FakeLockNode({SLOT: report})

    _hass, result = set_code(node)

    assert result.verified is False
    assert result.error == expected_error
    assert PIN not in caplog.text


def test_set_code_reports_no_response_when_lock_does_not_answer_readback(
    set_code, caplog
):
    node = _FakeLockNode({})

    _hass, result = set_code(node)

    assert result.verified is False
    assert result.error == "Slot 5: lock did not respond to read-back"
    assert PIN not in caplog.text
