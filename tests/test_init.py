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


async def test_orphan_cleanup_keeps_old_lever_entities_of_incomplete_entries(hass, monkeypatch):
    """client1 nach dem Update, vor "Neu konfigurieren": Eintrag ohne lever_set behaelt seine Hebel-Entities."""
    monkeypatch.setattr(f"{CO}.async_find_addon_managers", AsyncMock(return_value={}))
    entry = make_entry(hass, tenant_id="client1", data_without=("lever_set", "shift_lever"))
    registry = er.async_get(hass)
    for key in ("heizkurve", "parallelverschiebung", "mindestvorlauf", "heizgrenze"):
        registry.async_get_or_create(
            "sensor", DOMAIN, f"client1_{key}", config_entry=entry, suggested_object_id=f"smartheat_client1_{key}",
        )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    for key in ("heizkurve", "parallelverschiebung", "mindestvorlauf", "heizgrenze"):
        assert registry.async_get(f"sensor.smartheat_client1_{key}") is not None
    assert hass.states.get("sensor.smartheat_client1_status") is not None


async def test_orphan_cleanup_removes_keys_of_another_lever_set(hass, monkeypatch):
    """Vollstaendiger Viessmann-Eintrag: Hebel des Vaillant-Hebelsatzes (heizgrenze) fallen weg, seine eigenen bleiben."""
    monkeypatch.setattr(f"{CO}.async_find_addon_managers", AsyncMock(return_value={}))
    entry = make_entry(hass, tenant_id="t1", data={
        "tenant_id": "t1", "profile_id": "viessmann_vicare", "integration_domain": "vicare",
        "circuit": {"config_entry_id": "vicare-entry", "system_key": "SYSTEM", "circuit": "0"},
        "entities": {
            "entity_curve_current": "number.heating_curve_slope", "entity_level_current": "number.heating_curve_shift",
            "entity_shift_current": "number.comfort_temperature", "entity_mode_select": "climate.vicare",
            "entity_outdoor_temp": "sensor.aussen",
        },
        "lever_set": "viessmann_vicare", "shift_lever": "level",
    })
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", DOMAIN, "t1_heizgrenze", config_entry=entry, suggested_object_id="smartheat_t1_heizgrenze")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get("sensor.smartheat_t1_heizgrenze") is None
    for key in ("heizkurve", "niveau", "raum_soll", "gelernte_steigung", "status"):
        assert registry.async_get(f"sensor.smartheat_t1_{key}") is not None, key


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
