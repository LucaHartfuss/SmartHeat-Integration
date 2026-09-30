"""Entry v2, unvollstaendige Eintraege, Reparaturhinweis und Entfernen (Spec TP7 2.1, 2.7)."""
from unittest.mock import AsyncMock

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.smartheat.const import DOMAIN

from .addon_fakes import make_entry, status_event

CO = "custom_components.smartheat.coordinator"


async def test_entry_missing_a_current_plant_field_is_incomplete(hass, monkeypatch):
    """AU-037: ein Eintrag aus 0.7.x ohne entity_shift_current/entity_min_flow."""
    monkeypatch.setattr(f"{CO}.async_find_addon_managers", AsyncMock(return_value={}))
    entry = make_entry(hass, tenant_id="client1")
    entities = {k: v for k, v in entry.data["entities"].items() if k not in ("entity_shift_current", "entity_min_flow")}
    hass.config_entries.async_update_entry(entry, data={**entry.data, "entities": entities})

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"complete_setup_{entry.entry_id}")
    assert (issue.is_fixable, issue.translation_key) == (False, "complete_setup")
    assert hass.states.get("sensor.smartheat_client1_status") is not None


async def test_orphaned_entities_are_removed(hass, monkeypatch):
    """B-TP11-4: sensor.smartheat_client1_offset aus 0.7.x."""
    monkeypatch.setattr(f"{CO}.async_find_addon_managers", AsyncMock(return_value={}))
    entry = make_entry(hass, tenant_id="client1")
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", DOMAIN, "client1_offset", config_entry=entry, suggested_object_id="smartheat_client1_offset")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get("sensor.smartheat_client1_offset") is None
    assert registry.async_get("sensor.smartheat_client1_status") is not None
    assert registry.async_get("binary_sensor.smartheat_client1_notbetrieb") is not None


async def test_complete_entry_has_no_repair_issue(hass, monkeypatch):
    monkeypatch.setattr(f"{CO}.async_find_addon_managers", AsyncMock(return_value={}))
    entry = make_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"complete_setup_{entry.entry_id}") is None


async def test_removing_the_entry_signs_off_and_deletes_the_issue(hass, monkeypatch):
    monkeypatch.setattr(f"{CO}.async_find_addon_managers", AsyncMock(return_value={}))
    sign_off = AsyncMock()
    monkeypatch.setattr("custom_components.smartheat.addon_control.async_sign_off", sign_off)
    entry = make_entry(hass, data={"tenant_id": "wohnung1", "profile_id": "p"})
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
