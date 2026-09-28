"""Entry v2, Migration, Reparaturhinweis und Entfernen (Spec TP7 2.1, 2.7)."""
from unittest.mock import AsyncMock

from homeassistant.helpers import issue_registry as ir

from custom_components.smartheat.const import DOMAIN

from .addon_fakes import make_entry, status_event

CO = "custom_components.smartheat.coordinator"


async def test_v1_entry_is_migrated_incomplete_with_repair_issue_and_entities(hass, monkeypatch):
    """Review Focus 1 (Integrationsseite): client1 nach dem Update."""
    monkeypatch.setattr(f"{CO}.async_find_addon_managers", AsyncMock(return_value={}))
    entry = make_entry(
        hass, tenant_id="client1", version=1, options={},
        data={"tenant_id": "client1", "profile_id": "vaillant_gastherme_heizkoerper"},
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    assert entry.data == {"tenant_id": "client1", "profile_id": "vaillant_gastherme_heizkoerper", "unvollstaendig": True}
    assert entry.options == {}
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"complete_setup_{entry.entry_id}")
    assert (issue.is_fixable, issue.translation_key) == (False, "complete_setup")

    hass.bus.async_fire("smartheat_status", status_event(
        "client1", "konfigurationsfehler", grund="Konfiguration veraltet – bitte SmartHeat-Einrichtung erneut durchführen",
    ))
    await hass.async_block_till_done()

    status = hass.states.get("sensor.smartheat_client1_status")
    assert status.state == "konfigurationsfehler"
    assert status.attributes["grund"].startswith("Konfiguration veraltet")


async def test_complete_entry_has_no_repair_issue(hass, monkeypatch):
    monkeypatch.setattr(f"{CO}.async_find_addon_managers", AsyncMock(return_value={}))
    entry = make_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"complete_setup_{entry.entry_id}") is None


async def test_removing_the_entry_signs_off_and_deletes_the_issue(hass, monkeypatch):
    monkeypatch.setattr(f"{CO}.async_find_addon_managers", AsyncMock(return_value={}))
    sign_off = AsyncMock()
    monkeypatch.setattr("custom_components.smartheat.addon_control.async_sign_off", sign_off)
    entry = make_entry(hass, version=1, options={}, data={"tenant_id": "wohnung1", "profile_id": "p"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    sign_off.assert_awaited_once_with(hass, "wohnung1")
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"complete_setup_{entry.entry_id}") is None


async def test_unloaded_entry_no_longer_follows_events(hass, monkeypatch):
    monkeypatch.setattr(f"{CO}.async_find_addon_managers", AsyncMock(return_value={}))
    entry = make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.bus.async_fire("smartheat_status", status_event("wohnung1", "regelt"))
    await hass.async_block_till_done()

    assert hass.states.get("sensor.smartheat_wohnung1_status").state == "unavailable"
