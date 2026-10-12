"""Einrichten und Entfernen des Eintrags ab 1.0.0 (Spec 5c 2.2, 4, 8): stabile Entities fuer client1, verwaiste
Entities weg, Entfernen ohne Server und ohne das Add-on anzuhalten."""
from unittest.mock import AsyncMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.smartheat.const import DOMAIN, STATUS_EVENT

from .addon_fakes import FakeAddon, make_device_entry, make_entry, status_event

CO = "custom_components.smartheat.coordinator"

# Was 0.16.1 fuer client1 (vaillant_vrc720, shift_lever room_setpoint) erzeugt: 5 feste Felder, Status, 4 Hebel,
# 2 Lernwerte, Notbetrieb -- dazu neu die Wunschtemperatur.
CLIENT1_ENTITIES = {
    "sensor.smartheat_client1_status": "client1_status",
    "sensor.smartheat_client1_datenfehler": "client1_datenfehler",
    "sensor.smartheat_client1_boost": "client1_boost",
    "sensor.smartheat_client1_letzte_serverantwort": "client1_letzte_serverantwort",
    "sensor.smartheat_client1_abo": "client1_abo",
    "sensor.smartheat_client1_addon_version": "client1_addon_version",
    "sensor.smartheat_client1_heizkurve": "client1_heizkurve",
    "sensor.smartheat_client1_parallelverschiebung": "client1_parallelverschiebung",
    "sensor.smartheat_client1_heizgrenze": "client1_heizgrenze",
    "sensor.smartheat_client1_mindestvorlauf": "client1_mindestvorlauf",
    "sensor.smartheat_client1_gelernte_steigung": "client1_gelernte_steigung",
    "sensor.smartheat_client1_gelernte_heizgrenze": "client1_gelernte_heizgrenze",
    "binary_sensor.smartheat_client1_notbetrieb": "client1_notbetrieb",
    "number.smartheat_client1_wunschtemperatur": "client1_wunschtemperatur",
}


async def _setup(hass, monkeypatch, addons=None, **entry_kwargs):
    monkeypatch.setattr(f"{CO}.async_find_addon_managers", AsyncMock(return_value=addons or {}))
    entry = make_device_entry(hass, **entry_kwargs)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_client1_keeps_its_entity_ids_unique_ids_and_device(hass, monkeypatch):
    """Spec 5c 8: Namen und unique_id bleiben (Dashboards und Automationen von client1), Geraet (smartheat, tenant)."""
    entry = await _setup(hass, monkeypatch, tenant_id="client1")

    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert {e.entity_id: e.unique_id for e in entities} == CLIENT1_ENTITIES
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "client1"), entry.entry_id)
    assert device is not None and {e.device_id for e in entities} == {device.id}
    for entity_id in CLIENT1_ENTITIES:
        assert hass.states.get(entity_id) is not None, entity_id


async def test_viessmann_entry_has_its_own_levers(hass, monkeypatch):
    entry = await _setup(hass, monkeypatch, tenant_id="t1", profile_id="viessmann_vicare",
                         lever_set="viessmann_vicare", hebel=["curve", "level", "room_setpoint"], shift_lever="level")

    keys = {e.unique_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)}
    assert {"t1_heizkurve", "t1_niveau", "t1_raum_soll", "t1_wunschtemperatur"} <= keys
    assert not {"t1_heizgrenze", "t1_mindestvorlauf", "t1_parallelverschiebung"} & keys


async def test_orphaned_entities_are_removed(hass, monkeypatch):
    """B-TP11-4: z. B. sensor.smartheat_client1_offset aus 0.7.x oder der Hebel eines anderen Hebelsatzes."""
    monkeypatch.setattr(f"{CO}.async_find_addon_managers", AsyncMock(return_value={}))
    entry = make_device_entry(hass, tenant_id="client1")
    registry = er.async_get(hass)
    for key in ("offset", "niveau"):
        registry.async_get_or_create("sensor", DOMAIN, f"client1_{key}", config_entry=entry,
                                     suggested_object_id=f"smartheat_client1_{key}")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get("sensor.smartheat_client1_offset") is None
    assert registry.async_get("sensor.smartheat_client1_niveau") is None
    assert registry.async_get("sensor.smartheat_client1_heizkurve") is not None
    assert registry.async_get("number.smartheat_client1_wunschtemperatur") is not None


async def test_setup_creates_no_repair_issue(hass, monkeypatch):
    """Der Hinweis complete_setup (unvollstaendiger Eintrag bis 0.16) entfaellt."""
    await _setup(hass, monkeypatch)

    assert [issue_id for (domain, issue_id) in ir.async_get(hass).issues if domain == DOMAIN] == []


async def test_removing_the_entry_calls_no_server(hass, monkeypatch, aioclient_mock):
    """Spec 5c 4: das Geraet regelt weiter. Kein HTTP-Aufruf, das Add-on wird weder gestoppt noch abgemeldet oder
    deinstalliert; nur die eigenen Reparatur-Hinweise verschwinden."""
    log = []
    bridge = FakeAddon("a_heizungsbruecke", log)
    sign_off = AsyncMock()
    monkeypatch.setattr("custom_components.smartheat.addon_control.async_sign_off", sign_off)
    uninstall, stop = AsyncMock(), AsyncMock()
    monkeypatch.setattr("homeassistant.components.hassio.AddonManager.async_uninstall_addon", uninstall)
    monkeypatch.setattr("homeassistant.components.hassio.AddonManager.async_stop_addon", stop)
    entry = await _setup(hass, monkeypatch, {"heizungsbruecke": bridge})
    hass.bus.async_fire(STATUS_EVENT, status_event("wohnung1", "regelt", geraet_hinweise=[
        {"key": "geraet_doppelt", "stufe": "kritisch", "text": "Zweite Instanz"}]))
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "hinweis_geraet_doppelt") is not None

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert aioclient_mock.call_count == 0
    assert log == []
    sign_off.assert_not_awaited()
    uninstall.assert_not_awaited()
    stop.assert_not_awaited()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "hinweis_geraet_doppelt") is None


async def test_unloaded_entry_no_longer_follows_events(hass, monkeypatch):
    entry = await _setup(hass, monkeypatch)

    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.bus.async_fire(STATUS_EVENT, status_event("wohnung1", "regelt"))
    await hass.async_block_till_done()

    assert hass.states.get("sensor.smartheat_wohnung1_status").state == "unavailable"
    assert hass.states.get("number.smartheat_wohnung1_wunschtemperatur").state == "unavailable"


async def test_an_entry_from_before_1_0_fails_with_a_text_and_a_repair_issue(hass, monkeypatch, caplog):
    """Ein Eintrag aus 0.16 (ohne geraet_id, VERSION 2 wie client1 heute): Fehler mit uebersetztem Grund und ein
    Reparatur-Hinweis statt eines KeyError; Entfernen raeumt den Hinweis weg."""
    monkeypatch.setattr(f"{CO}.async_find_addon_managers", AsyncMock(return_value={}))
    entry = make_entry(hass, tenant_id="client1")

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert (entry.error_reason_translation_domain, entry.error_reason_translation_key) == (DOMAIN, "alter_eintrag")
    assert "KeyError" not in caplog.text
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "alter_eintrag")
    assert (issue.is_fixable, issue.severity, issue.translation_key) == (False, ir.IssueSeverity.ERROR, "alter_eintrag")
    assert hass.states.get("sensor.smartheat_client1_status") is None

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "alter_eintrag") is None


async def test_unloading_clears_the_hint_issues(hass, monkeypatch):
    """Ein deaktivierter Eintrag hinterlaesst keine veralteten Hinweise."""
    entry = await _setup(hass, monkeypatch)
    hass.bus.async_fire(STATUS_EVENT, status_event("wohnung1", "regelt", geraet_hinweise=[
        {"key": "wunsch_begrenzt", "stufe": "warnung", "text": "Auf 25 °C begrenzt"}]))
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "hinweis_wunsch_begrenzt") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)

    assert ir.async_get(hass).async_get_issue(DOMAIN, "hinweis_wunsch_begrenzt") is None
