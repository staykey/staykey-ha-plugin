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

import sys
import types
from pathlib import Path

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


def _async_get_clientsession(_hass):  # noqa: D401 - stub function
    """Stand-in for HA's shared aiohttp session accessor."""
    raise RuntimeError("aiohttp_client.async_get_clientsession is stubbed in tests")


_aiohttp_client = sys.modules["homeassistant.helpers.aiohttp_client"]
_aiohttp_client.async_get_clientsession = _async_get_clientsession


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


# Only the exception type the webhook path catches is needed; leave a real
# aiohttp alone if the environment happens to have one installed.
try:
    import aiohttp  # noqa: E402, F401
except ImportError:

    class _ClientError(Exception):
        """Stand-in for ``aiohttp.ClientError``."""

    _ensure_module("aiohttp").ClientError = _ClientError
