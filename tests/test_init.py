"""Einrichten und Entfernen des Eintrags ab 1.0.0 (Spec 5c 2.2, 4, 8): stabile Entities fuer client1, verwaiste
Entities weg, Entfernen ohne Server und ohne das Add-on anzuhalten; seit L4-06 meldet das Entfernen das Geraet ueber
das stdin der Heizungsbruecke ab."""
import logging

import pytest
from aiohasupervisor.exceptions import SupervisorBadRequestError
from aiohasupervisor.models import AddonState
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.smartheat.const import DOMAIN, STATUS_EVENT

from .addon_fakes import make_device_entry, make_entry, status_event
from .supervisor_fakes import BRIDGE_SLUG, installed_addon

# Was 0.16.1 fuer client1 (vaillant_vrc720, shift_lever room_setpoint) erzeugt: 5 feste Felder, Status, 4 Hebel,
# 2 Lernwerte, Notbetrieb -- dazu neu die Wunschtemperatur. Die 13 alten IDs sind aus sensor.entity_keys und
# binary_sensor.ENTITY_KEYS von v0.16.1 abgeleitet (Plan 5c I Task 7), nicht abgetippt.
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


async def _setup(hass, **entry_kwargs):
    entry = make_device_entry(hass, **entry_kwargs)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_client1_keeps_its_entity_ids_unique_ids_and_device(hass):
    """Spec 5c 8: Namen und unique_id bleiben (Dashboards und Automationen von client1), Geraet (smartheat, tenant)."""
    entry = await _setup(hass, tenant_id="client1")

    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert {e.entity_id: e.unique_id for e in entities} == CLIENT1_ENTITIES
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "client1"), entry.entry_id)
    assert device is not None and {e.device_id for e in entities} == {device.id}
    for entity_id in CLIENT1_ENTITIES:
        assert hass.states.get(entity_id) is not None, entity_id


async def test_viessmann_entry_has_its_own_levers(hass):
    entry = await _setup(hass, tenant_id="t1", profile_id="viessmann_vicare",
                         lever_set="viessmann_vicare", hebel=["curve", "level", "room_setpoint"], shift_lever="level")

    keys = {e.unique_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)}
    assert {"t1_heizkurve", "t1_niveau", "t1_raum_soll", "t1_wunschtemperatur"} <= keys
    assert not {"t1_heizgrenze", "t1_mindestvorlauf", "t1_parallelverschiebung"} & keys


async def test_orphaned_entities_are_removed(hass):
    """B-TP11-4: z. B. sensor.smartheat_client1_offset aus 0.7.x oder der Hebel eines anderen Hebelsatzes."""
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


async def test_setup_creates_no_repair_issue(hass):
    """Der Hinweis complete_setup (unvollstaendiger Eintrag bis 0.16) entfaellt."""
    await _setup(hass)

    assert [issue_id for (domain, issue_id) in ir.async_get(hass).issues if domain == DOMAIN] == []


GUELTIG = "shg-aaaaaaaaaaaaaaaa"
LOGGER_ADDON_CONTROL = "custom_components.smartheat.addon_control"


async def _hinweis_setzen(hass):
    """Das Status-Ereignis muss die Geraete-ID des Eintrags tragen (sonst ignoriert ihn der Koordinator)."""
    hass.bus.async_fire(STATUS_EVENT, status_event("wohnung1", "regelt", geraet_id=GUELTIG, geraet_hinweise=[
        {"key": "geraet_doppelt", "stufe": "kritisch", "text": "Zweite Instanz"}]))
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "hinweis_geraet_doppelt") is not None


async def test_removing_the_entry_signs_the_device_off_through_stdin(hass, supervisor, aioclient_mock, caplog):
    """L4-06 Variante C: Eintrag loeschen schreibt genau `entfernen <geraet_id>` mit Zeilenende an das stdin der einen
    Heizungsbruecke; kein Server-Aufruf, das Add-on wird weder gestoppt noch deinstalliert; die Hinweise verschwinden."""
    entry = await _setup(hass, geraet_id=GUELTIG)
    await _hinweis_setzen(hass)
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger=LOGGER_ADDON_CONTROL):
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

    assert supervisor.stdin == [(BRIDGE_SLUG, b"entfernen shg-aaaaaaaaaaaaaaaa\n")]
    assert aioclient_mock.call_count == 0
    assert supervisor.actions == []
    assert [(addon.slug, addon.state) for addon in supervisor.installed] == [(BRIDGE_SLUG, AddonState.STARTED)]
    assert ir.async_get(hass).async_get_issue(DOMAIN, "hinweis_geraet_doppelt") is None
    assert [r for r in caplog.records if r.name == LOGGER_ADDON_CONTROL] == []


def _bruecke_fehlt(supervisor):
    supervisor.installed = []


def _bruecke_mehrfach(supervisor):
    supervisor.installed = [installed_addon(BRIDGE_SLUG), installed_addon(BRIDGE_SLUG)]


def _supervisor_fehler(supervisor):
    supervisor.errors["addons.write_addon_stdin"] = SupervisorBadRequestError("Supervisor sagt nein")


def _addon_gestoppt(supervisor):
    supervisor.installed = [installed_addon(BRIDGE_SLUG, state=AddonState.STOPPED)]


def _kein_hassio(supervisor):
    supervisor.hassio = False


@pytest.mark.parametrize("zustand", [_bruecke_fehlt, _bruecke_mehrfach, _supervisor_fehler, _addon_gestoppt,
                                      _kein_hassio],
                         ids=["bruecke_fehlt", "bruecke_mehrfach", "supervisor_fehler", "addon_gestoppt",
                              "kein_hassio"])
async def test_removing_the_entry_warns_and_still_removes_it(hass, supervisor, aioclient_mock, caplog, zustand):
    """Spec 2.1: jeder Fehler auf dem Weg zum stdin ist eine WARNING; der Eintrag ist trotzdem weg."""
    entry = await _setup(hass, geraet_id=GUELTIG)
    await _hinweis_setzen(hass)
    zustand(supervisor)
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger=LOGGER_ADDON_CONTROL):
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

    warnings = [r for r in caplog.records if r.name == LOGGER_ADDON_CONTROL and r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "Portal abmelden" in warnings[0].getMessage()
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, "hinweis_geraet_doppelt") is None
    assert aioclient_mock.call_count == 0
    assert supervisor.stdin == []


def _ohne_feld_geraet_id(hass, entry):
    hass.config_entries.async_update_entry(entry, data={k: v for k, v in entry.data.items() if k != "geraet_id"})


def _id_ungueltig(hass, entry):
    hass.config_entries.async_update_entry(entry, data={**entry.data, "geraet_id": "shg-a"})


def _id_mit_zeilenumbruch(hass, entry):
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "geraet_id": "shg-aaaaaaaaaaaaaaaa\nentfernen shg-bbbbbbbbbbbbbbbb"})


def _id_mit_zeilenumbruch_am_ende(hass, entry):
    hass.config_entries.async_update_entry(entry, data={**entry.data, "geraet_id": "shg-aaaaaaaaaaaaaaaa\n"})


@pytest.mark.parametrize("aendern", [_ohne_feld_geraet_id, _id_ungueltig, _id_mit_zeilenumbruch,
                                      _id_mit_zeilenumbruch_am_ende],
                         ids=["ohne_feld", "ungueltig", "zeilenumbruch_injektion", "zeilenumbruch_am_ende"])
async def test_removing_an_entry_without_a_valid_device_id_writes_nothing(hass, supervisor, aioclient_mock, caplog,
                                                                         aendern):
    """L-3: Eintrag ohne (gueltige) Geraete-ID, auch mit Zeilenumbruch: eine WARNING ohne den Wert, nichts ans
    stdin (kein eingeschmuggelter zweiter Befehl), Eintrag trotzdem weg."""
    entry = await _setup(hass, geraet_id=GUELTIG)
    await _hinweis_setzen(hass)
    aendern(hass, entry)
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger=LOGGER_ADDON_CONTROL):
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

    warnings = [r for r in caplog.records if r.name == LOGGER_ADDON_CONTROL and r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "shg-" not in caplog.text and "bbbbbbbb" not in caplog.text and "entfernen shg" not in caplog.text
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, "hinweis_geraet_doppelt") is None
    assert aioclient_mock.call_count == 0
    assert supervisor.stdin == []


async def test_unloaded_entry_no_longer_follows_events(hass):
    entry = await _setup(hass)

    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.bus.async_fire(STATUS_EVENT, status_event("wohnung1", "regelt"))
    await hass.async_block_till_done()

    assert hass.states.get("sensor.smartheat_wohnung1_status").state == "unavailable"
    assert hass.states.get("number.smartheat_wohnung1_wunschtemperatur").state == "unavailable"


async def test_an_entry_from_before_1_0_is_not_migrated(hass):
    """Stufe 4, Rueckweg loeschen: ein Eintrag aus 0.16 (VERSION 2) bleibt ohne Migration stehen (HA-Zustand
    MIGRATION_ERROR); es gibt keinen Reparatur-Hinweis dafuer mehr."""
    entry = make_entry(hass, tenant_id="client1", version=2)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert ir.async_get(hass).async_get_issue(DOMAIN, "alter_eintrag") is None


async def test_unloading_clears_the_hint_issues(hass):
    """Ein deaktivierter Eintrag hinterlaesst keine veralteten Hinweise."""
    entry = await _setup(hass)
    hass.bus.async_fire(STATUS_EVENT, status_event("wohnung1", "regelt", geraet_hinweise=[
        {"key": "wunsch_begrenzt", "stufe": "warnung", "text": "Auf 25 °C begrenzt"}]))
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "hinweis_wunsch_begrenzt") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)

    assert ir.async_get(hass).async_get_issue(DOMAIN, "hinweis_wunsch_begrenzt") is None
