"""Einrichten und Entfernen des Eintrags: stabile Entities fuer client1, verwaiste
Entities weg, Entfernen ohne Server und ohne das Add-on anzuhalten; das Entfernen meldet das Geraet ueber
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

# Die Entities der Anlage von client1 (Schluessel unveraendert, unique_id aus Anlage und Schluessel); dazu kommt je
# gemeldetem Kreis ein Satz mit Kreis-Nummer in unique_id und Entity-ID.
ANLAGE_ENTITIES = {
    "sensor.smartheat_status": "client1_status",
    "sensor.smartheat_datenfehler": "client1_datenfehler",
    "sensor.smartheat_letzte_serverantwort": "client1_letzte_serverantwort",
    "sensor.smartheat_addon_version": "client1_addon_version",
    "binary_sensor.smartheat_notbetrieb": "client1_notbetrieb",
    "binary_sensor.smartheat_server_verbunden": "client1_server_verbunden",
}
KREIS_1_ENTITIES = {
    "sensor.smartheat_k1_zustand": "client1_k1_zustand",
    "sensor.smartheat_k1_kaelteschutz": "client1_k1_kaelteschutz",
    "sensor.smartheat_k1_boost": "client1_k1_boost",
    "sensor.smartheat_k1_abo": "client1_k1_abo",
    "sensor.smartheat_k1_raumtemperatur": "client1_k1_raumtemperatur",
    "sensor.smartheat_k1_befunde": "client1_k1_befunde",
    "number.smartheat_k1_wunschtemperatur": "client1_k1_wunschtemperatur",
    "sensor.smartheat_k1_heizkurve": "client1_k1_heizkurve",
    "sensor.smartheat_k1_parallelverschiebung": "client1_k1_parallelverschiebung",
    "sensor.smartheat_k1_heizgrenze": "client1_k1_heizgrenze",
    "sensor.smartheat_k1_mindestvorlauf": "client1_k1_mindestvorlauf",
    "sensor.smartheat_k1_gelernte_steigung": "client1_k1_gelernte_steigung",
    "sensor.smartheat_k1_gelernte_heizgrenze": "client1_k1_gelernte_heizgrenze",
}


async def _setup(hass, **entry_kwargs):
    entry = make_device_entry(hass, **entry_kwargs)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_vor_dem_ersten_ereignis_hat_client1_nur_die_entities_der_anlage(hass):
    entry = await _setup(hass, tenant_id="client1")

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert {e.entity_id: e.unique_id for e in entities} == ANLAGE_ENTITIES
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "client1"), entry.entry_id)
    assert device is not None and {e.device_id for e in entities} == {device.id}


async def test_nach_dem_ersten_ereignis_hat_der_kreis_seine_entities_an_seinem_geraet(hass):
    entry = await _setup(hass, tenant_id="client1")
    ereignis = status_event("client1", "regelt")
    ereignis["kreise"] = ereignis["kreise"][:1]

    hass.bus.async_fire(STATUS_EVENT, ereignis)
    await hass.async_block_till_done()

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert {e.entity_id: e.unique_id for e in entities} == {**ANLAGE_ENTITIES, **KREIS_1_ENTITIES}
    kreis_geraet = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "client1_k1"), entry.entry_id)
    assert {e.unique_id for e in entities if e.device_id == kreis_geraet.id} == set(KREIS_1_ENTITIES.values())
    for entity_id in (*ANLAGE_ENTITIES, "sensor.smartheat_k1_zustand", "number.smartheat_k1_wunschtemperatur"):
        assert hass.states.get(entity_id) is not None, entity_id


async def test_eine_bestehende_entity_der_anlage_behaelt_ihre_id_in_der_registry(hass):
    """Die Registry gewinnt gegen die vorgeschlagene ID: das Umbenennen der Entities einer laufenden Anlage ist Sache der
    Migration, nicht des Ladens."""
    entry = make_device_entry(hass, tenant_id="client1")
    er.async_get(hass).async_get_or_create("sensor", DOMAIN, "client1_status", config_entry=entry,
                                           suggested_object_id="smartheat_client1_status")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "client1_status") == "sensor.smartheat_client1_status"
    assert hass.states.get("sensor.smartheat_client1_status") is not None


async def test_viessmann_entry_has_its_own_levers(hass):
    entry = await _setup(hass, tenant_id="t1", profile_id="viessmann_vicare",
                         lever_set="viessmann_vicare", hebel=["curve", "level", "room_setpoint"], shift_lever="level")
    ereignis = status_event("t1", "regelt")
    ereignis["kreise"] = ereignis["kreise"][:1]
    hass.bus.async_fire(STATUS_EVENT, ereignis)
    await hass.async_block_till_done()

    keys = {e.unique_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)}
    assert {"t1_k1_heizkurve", "t1_k1_niveau", "t1_k1_raum_soll", "t1_k1_wunschtemperatur"} <= keys
    assert not {"t1_k1_heizgrenze", "t1_k1_mindestvorlauf", "t1_k1_parallelverschiebung"} & keys


async def test_orphaned_entities_are_removed(hass):
    """Verwaiste Entities: ein Sensor aus 0.7.x, der Hebel eines anderen Hebelsatzes und die Entities ohne Kreis aus der
    Zeit vor den Kreis-Entities."""
    entry = make_device_entry(hass, tenant_id="client1")
    registry = er.async_get(hass)
    for key in ("offset", "niveau", "zustand", "boost", "heizkurve"):
        registry.async_get_or_create("sensor", DOMAIN, f"client1_{key}", config_entry=entry,
                                     suggested_object_id=f"smartheat_client1_{key}")
    registry.async_get_or_create("number", DOMAIN, "client1_wunschtemperatur", config_entry=entry,
                                 suggested_object_id="smartheat_client1_wunschtemperatur")
    registry.async_get_or_create("sensor", DOMAIN, "client1_k1_niveau", config_entry=entry,
                                 suggested_object_id="smartheat_k1_niveau")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    for entity_id in ("sensor.smartheat_client1_offset", "sensor.smartheat_client1_niveau",
                      "sensor.smartheat_client1_zustand", "sensor.smartheat_client1_boost",
                      "sensor.smartheat_client1_heizkurve", "number.smartheat_client1_wunschtemperatur",
                      "sensor.smartheat_k1_niveau"):
        assert registry.async_get(entity_id) is None, entity_id
    assert registry.async_get("sensor.smartheat_status") is not None


async def test_setup_creates_no_repair_issue(hass):
    """Die Einrichtung legt keinen Reparatur-Hinweis an (auch kein complete_setup fuer unvollstaendige Eintraege)."""
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
    """Eintrag loeschen schreibt genau `entfernen <geraet_id>` mit Zeilenende an das stdin der einen
    Heizungsbruecke; kein Server-Aufruf, das Add-on wird weder gestoppt noch deinstalliert; die Hinweise verschwinden."""
    entry = await _setup(hass, geraet_id=GUELTIG)
    await _hinweis_setzen(hass)
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger=LOGGER_ADDON_CONTROL):
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

    # Zwei Zeilenumbrueche: einer aus STDIN_ZEILE_FORMAT, einer haengt der Supervisor selbst an (die Attrappe
    # bildet das nach).
    assert supervisor.stdin == [(BRIDGE_SLUG, b"entfernen shg-aaaaaaaaaaaaaaaa\n\n")]
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
    """Jeder Fehler auf dem Weg zum stdin ist eine WARNING; der Eintrag ist trotzdem weg."""
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
    hass.bus.async_fire(STATUS_EVENT, status_event("wohnung1", "regelt"))
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.bus.async_fire(STATUS_EVENT, status_event("wohnung1", "notbetrieb", kreise=[]))
    await hass.async_block_till_done()

    assert hass.states.get("sensor.smartheat_status").state == "unavailable"
    assert hass.states.get("number.smartheat_k1_wunschtemperatur").state == "unavailable"


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
