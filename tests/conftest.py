"""Test configuration: stub Home Assistant modules.

The Staykey integration is normally loaded inside a HA process where
``homeassistant.*`` is available.  Our unit tests don't need a running
HA instance and shouldn't pull the framework as a dev dependency, so we
register lightweight stubs at import time.  Only the symbols actually
referenced at module-import time need to exist; runtime calls into HA
(e.g. ``hass.services.async_call``) belong in integration tests, not
here.
"""

from __future__ import annotations

import copy
import enum
import sys
import types
from pathlib import Path

import voluptuous as vol

# Make ``custom_components/staykey`` importable as the package root for
# tests, without going through HA's component loader.
PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "staykey"
sys.path.insert(0, str(PLUGIN_ROOT))


def _ensure_module(dotted: str) -> types.ModuleType:
    if dotted in sys.modules:
        return sys.modules[dotted]

    parts = dotted.split(".")
    parent: types.ModuleType | None = None
    accumulated = ""
    for part in parts:
        accumulated = f"{accumulated}.{part}" if accumulated else part
        if accumulated not in sys.modules:
            mod = types.ModuleType(accumulated)
            sys.modules[accumulated] = mod
            if parent is not None:
                setattr(parent, part, mod)
        parent = sys.modules[accumulated]

    return sys.modules[dotted]


_ensure_module("homeassistant")
_ensure_module("homeassistant.core")
_ensure_module("homeassistant.exceptions")
_ensure_module("homeassistant.helpers")
_ensure_module("homeassistant.helpers.device_registry")
_ensure_module("homeassistant.helpers.entity_registry")


# Minimal placeholders for symbols our modules import at top level.
class _HomeAssistant:  # noqa: D401 - stub class
    """Stand-in for ``homeassistant.core.HomeAssistant``."""


sys.modules["homeassistant.core"].HomeAssistant = _HomeAssistant


class _HomeAssistantError(Exception):
    """Stand-in for ``homeassistant.exceptions.HomeAssistantError``.

    Mirrors the real class's keyword-only ``translation_*`` attributes so
    tests can construct exceptions the way HA's matter integration does.
    """

    def __init__(
        self,
        *args,
        translation_domain=None,
        translation_key=None,
        translation_placeholders=None,
        **kwargs,
    ):
        super().__init__(*args)
        self.translation_domain = translation_domain
        self.translation_key = translation_key
        self.translation_placeholders = translation_placeholders


sys.modules["homeassistant.exceptions"].HomeAssistantError = _HomeAssistantError


class _ConfigEntryError(_HomeAssistantError):
    """Stand-in for ``homeassistant.exceptions.ConfigEntryError``."""


sys.modules["homeassistant.exceptions"].ConfigEntryError = _ConfigEntryError


def _async_get(_):  # noqa: D401 - stub function
    """Stand-in for the HA registry async_get accessors."""
    raise RuntimeError("homeassistant.helpers.*_registry.async_get is stubbed in tests")


sys.modules["homeassistant.helpers.device_registry"].async_get = _async_get
sys.modules["homeassistant.helpers.entity_registry"].async_get = _async_get


# ``custom_components/staykey/__init__.py`` reaches for a few more HA
# modules at import time; tests that drive the config entry setup need
# those present too.
_ensure_module("homeassistant.config_entries")
_ensure_module("homeassistant.const")
_ensure_module("homeassistant.helpers.aiohttp_client")
_ensure_module("homeassistant.helpers.config_validation")
_ensure_module("homeassistant.helpers.instance_id")
_ensure_module("homeassistant.helpers.issue_registry")
_ensure_module("homeassistant.helpers.typing")
_ensure_module("homeassistant.loader")


class _ConfigEntry:  # noqa: D401 - stub class
    """Stand-in for ``homeassistant.config_entries.ConfigEntry``."""


class _Event:  # noqa: D401 - stub class
    """Stand-in for ``homeassistant.core.Event``."""


class _State:  # noqa: D401 - stub class
    """Stand-in for ``homeassistant.core.State``."""


sys.modules["homeassistant.config_entries"].ConfigEntry = _ConfigEntry
sys.modules["homeassistant.const"].__version__ = "0.0.0"
sys.modules["homeassistant.core"].CALLBACK_TYPE = object
sys.modules["homeassistant.core"].Event = _Event
sys.modules["homeassistant.core"].State = _State
sys.modules["homeassistant.helpers.typing"].ConfigType = dict


# ``config_flow.py`` subclasses Home Assistant's flow handlers and builds its
# forms with voluptuous (installed from requirements-test.txt). These
# stand-ins return plain dicts shaped like Home Assistant's flow results.
# A test gives a flow its ``hass`` (and a config flow its ``context``) the
# way Home Assistant's flow manager would.
_ensure_module("homeassistant.data_entry_flow")


class _AbortFlow(Exception):  # noqa: D401 - stub class
    """Stand-in for ``homeassistant.data_entry_flow.AbortFlow``.

    Home Assistant's flow manager turns it into an abort result; called
    directly, a step raises it.
    """

    def __init__(self, reason, description_placeholders=None):
        super().__init__(reason)
        self.reason = reason
        self.description_placeholders = description_placeholders


class _FlowHandler:  # noqa: D401 - stub class
    """Stand-in for the data-entry flow result helpers."""

    def async_show_form(self, *, step_id, data_schema=None, errors=None):
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors or {},
        }

    def async_create_entry(self, *, title, data):
        return {"type": "create_entry", "title": title, "data": data}

    def add_suggested_values_to_schema(self, data_schema, suggested_values):
        """Copy of Home Assistant's: each field with a value in
        ``suggested_values`` gets it as its suggested value."""
        schema = {}
        for key, value in data_schema.schema.items():
            new_key = key
            if (
                suggested_values
                and key in suggested_values
                and isinstance(key, vol.Marker)
            ):
                new_key = copy.copy(key)
                new_key.description = {"suggested_value": suggested_values[key.schema]}
            schema[new_key] = value
        return vol.Schema(schema)


class _OptionsFlow(_FlowHandler):  # noqa: D401 - stub class
    """Stand-in for ``homeassistant.config_entries.OptionsFlow``."""


# The fields ``ConfigFlow.async_update_reload_and_abort`` takes besides the
# entry and the abort reason (Home Assistant 2026.4).
_RELOAD_AND_ABORT_FIELDS = frozenset(
    {
        "unique_id",
        "title",
        "data",
        "data_updates",
        "options",
        "reload_even_if_entry_is_unchanged",
    }
)

# Home Assistant's ``UNDEFINED``: an argument that was left out, as opposed to
# one passed as None.
_UNDEFINED = object()


class _ConfigFlow(_FlowHandler):  # noqa: D401 - stub class
    """Stand-in for ``homeassistant.config_entries.ConfigFlow``."""

    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.handler = domain

    @property
    def unique_id(self):
        if not getattr(self, "context", None):
            return None
        return self.context.get("unique_id")

    async def async_set_unique_id(self, unique_id=None, *, raise_on_progress=True):
        """Like Home Assistant's: None clears the flow's unique ID and returns
        straight away; any other ID is kept and the entry that already has it,
        if any, is returned."""
        if unique_id is None:
            self.context["unique_id"] = None
            return None
        self.context["unique_id"] = unique_id
        return next(
            (
                entry
                for entry in self.hass.config_entries.async_entries(self.handler)
                if entry.unique_id == unique_id
            ),
            None,
        )

    def _abort_if_unique_id_configured(
        self, updates=None, reload_on_update=True, *, error="already_configured"
    ):
        for entry in self.hass.config_entries.async_entries(self.handler):
            if entry.unique_id == self.unique_id:
                raise _AbortFlow(error)

    def _get_reauth_entry(self):
        return self.hass.config_entries.async_get_known_entry(self.context["entry_id"])

    def async_update_reload_and_abort(self, entry, *, reason=_UNDEFINED, **changes):
        """Return the abort, with the entry and the changes asked for, so a
        test can check them; Home Assistant applies them and reloads.

        As in Home Assistant, only a reason that is left out defaults.
        """
        if unknown := set(changes) - _RELOAD_AND_ABORT_FIELDS:
            raise TypeError(f"unexpected arguments: {sorted(unknown)}")
        if reason is _UNDEFINED:
            reason = "reauth_successful"
            if self.context.get("source") == "reconfigure":
                reason = "reconfigure_successful"
        return {
            "type": "abort",
            "reason": reason,
            "entry": entry,
            "changes": changes,
        }


class _OptionsFlowWithReload(_OptionsFlow):  # noqa: D401 - stub class
    """Stand-in for ``homeassistant.config_entries.OptionsFlowWithReload``."""

    automatic_reload = True


_config_entries = sys.modules["homeassistant.config_entries"]
_config_entries.SOURCE_REAUTH = "reauth"
_config_entries.OptionsFlow = _OptionsFlow
_config_entries.ConfigFlow = _ConfigFlow
_config_entries.OptionsFlowWithReload = _OptionsFlowWithReload
sys.modules["homeassistant.data_entry_flow"].FlowResult = dict
sys.modules["homeassistant.data_entry_flow"].AbortFlow = _AbortFlow


def _async_get_clientsession(_hass):  # noqa: D401 - stub function
    """Stand-in for HA's shared aiohttp session accessor."""
    raise RuntimeError("aiohttp_client.async_get_clientsession is stubbed in tests")


_aiohttp_client = sys.modules["homeassistant.helpers.aiohttp_client"]
_aiohttp_client.async_get_clientsession = _async_get_clientsession


def _config_entry_only_config_schema(_domain):  # noqa: D401 - stub function
    """Stand-in for ``config_validation.config_entry_only_config_schema``."""
    return dict


_config_validation = sys.modules["homeassistant.helpers.config_validation"]
_config_validation.config_entry_only_config_schema = _config_entry_only_config_schema


def _noop_issue(*_args, **_kwargs):  # noqa: D401 - stub function
    """Stand-in for the Repairs issue registry helpers."""


_issue_registry = sys.modules["homeassistant.helpers.issue_registry"]
_issue_registry.async_create_issue = _noop_issue
_issue_registry.async_delete_issue = _noop_issue
_issue_registry.IssueSeverity = types.SimpleNamespace(WARNING="warning")


async def _async_get_integration(_hass, _domain):  # noqa: D401 - stub function
    """Stand-in for ``homeassistant.loader.async_get_integration``."""
    return types.SimpleNamespace(version="0.0.0")


sys.modules["homeassistant.loader"].async_get_integration = _async_get_integration


async def _async_get_instance_id(_hass):  # noqa: D401 - stub function
    """Stand-in for ``homeassistant.helpers.instance_id.async_get``.

    Home Assistant creates the ID once per installation with
    ``uuid.uuid4().hex``; this returns a fixed one of the same shape, so every
    test that runs ``async_setup_entry`` gets one.
    """
    return "0f1e2d3c4b5a69788796a5b4c3d2e1f0"


sys.modules["homeassistant.helpers.instance_id"].async_get = _async_get_instance_id


# The gateway client imports aiohttp, catches its ClientError and compares
# received messages with its WSMsgType. Leave a real aiohttp alone if one is
# installed.
try:
    import aiohttp  # noqa: E402, F401
except ImportError:

    class _ClientError(Exception):
        """Stand-in for ``aiohttp.ClientError``."""

    class _WSMsgType(enum.IntEnum):
        """Stand-in for ``aiohttp.WSMsgType``, with aiohttp's values."""

        CONTINUATION = 0x0
        TEXT = 0x1
        BINARY = 0x2
        PING = 0x9
        PONG = 0xA
        CLOSE = 0x8
        CLOSING = 0x100
        CLOSED = 0x101
        ERROR = 0x102

    _aiohttp = _ensure_module("aiohttp")
    _aiohttp.ClientError = _ClientError
    _aiohttp.WSMsgType = _WSMsgType
