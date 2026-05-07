"""Tests for ``services.providers.select_provider`` registry resolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _hass_with_device(identifiers: list[tuple[str, str]]):
    """Build hass + registry mocks so ``entity_id`` resolves to *identifiers*."""
    hass = MagicMock(name="hass")
    entity_entry = MagicMock()
    entity_entry.device_id = "device-uuid-1"

    device = MagicMock()
    device.identifiers = identifiers

    entity_reg = MagicMock()
    entity_reg.async_get = MagicMock(return_value=entity_entry)
    dev_reg = MagicMock()
    dev_reg.async_get = MagicMock(return_value=device)

    def er_factory(_h):
        return entity_reg

    def dr_factory(_h):
        return dev_reg

    return hass, er_factory, dr_factory


def test_select_provider_matter_lock():
    from services.providers import select_provider

    hass, er_factory, dr_factory = _hass_with_device([("matter", "abc")])

    with patch("services.providers.er.async_get", er_factory), patch(
        "services.providers.dr.async_get", dr_factory
    ):
        provider = select_provider(hass, "lock.front_door")
    assert provider.name == "matter"


def test_select_provider_zwave_lock():
    from services.providers import select_provider

    hass, er_factory, dr_factory = _hass_with_device([("zwave_js", "999-8")])

    with patch("services.providers.er.async_get", er_factory), patch(
        "services.providers.dr.async_get", dr_factory
    ):
        provider = select_provider(hass, "lock.front_door")
    assert provider.name == "zwave"


def test_select_provider_first_matching_domain_wins():
    """Iteration order over identifiers picks the first supported integration."""
    from services.providers import select_provider

    hass, er_factory, dr_factory = _hass_with_device(
        [("matter", "m1"), ("zwave_js", "z1")]
    )

    with patch("services.providers.er.async_get", er_factory), patch(
        "services.providers.dr.async_get", dr_factory
    ):
        assert select_provider(hass, "lock.a").name == "matter"

    hass2, er2, dr2 = _hass_with_device([("zwave_js", "z1"), ("matter", "m1")])

    with patch("services.providers.er.async_get", er2), patch(
        "services.providers.dr.async_get", dr2
    ):
        assert select_provider(hass2, "lock.b").name == "zwave"
