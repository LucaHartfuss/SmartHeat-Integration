"""Entities je Kreis und Anlage: IDs und unique_ids, Zuordnung zum Geraet, Werte nur aus dem eigenen Kreis,
dynamisches Anlegen und Abraeumen mit den Kreisen, Wiederherstellen nach dem Neustart, Diagnose-Kategorie und
Standard aus fuer Hebel- und Lernwerte."""
import copy
import json
from pathlib import Path

import pytest
from homeassistant.const import EntityCategory
from homeassistant.core import Context, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import mock_restore_cache

from custom_components.smartheat.const import (
    DOMAIN,
    EINRICHTUNG_MAX_KREISE,
    KAELTESCHUTZ_VALUES,
    KREIS_ZUSTAND_VALUES,
    STATUS_EVENT,
    WUNSCH_EVENT,
    entity_id,
    unique_id,
)

from .addon_fakes import make_device_entry, status_event

TENANT = "wohnung1"
PAKET = Path(__file__).parent.parent / "custom_components" / "smartheat"

ANLAGE_SENSOREN = ("status", "letzte_serverantwort", "datenfehler", "addon_version")
ANLAGE_BINAER = ("notbetrieb", "server_verbunden")
KREIS_SENSOREN = ("zustand", "kaelteschutz", "abo", "boost", "raumtemperatur", "befunde")
HEBEL_SCHLUESSEL = ("heizkurve", "parallelverschiebung", "heizgrenze", "mindestvorlauf", "gelernte_steigung",
                    "gelernte_heizgrenze")


def _kreis(nummer, **felder):
    kreis = copy.deepcopy(status_event(TENANT, "regelt")["kreise"][0])
    kreis.update({"kreis": nummer, "name": f"Kreis-{nummer}", **felder})
    return kreis


def _ereignis(nummern=(1, 2), kreise=None, konfig_version=7, **overrides):
    if kreise is None:
        kreise = [_kreis(n) for n in nummern]
    ereignis = status_event(TENANT, "regelt", kreise=kreise, **overrides)
    ereignis["konfig_version"] = konfig_version
    return ereignis


async def _setup(hass):
    entry = make_device_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _fire(hass, event):
    hass.bus.async_fire(STATUS_EVENT, event)
    await hass.async_block_till_done()


def _eintraege(hass, entry):
    return {e.unique_id: e for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)}


def _geraet(hass, entry, kennung):
    return dr.async_get(hass).async_get_device_by_identifier(kennung, entry.entry_id)


# --- Kennungen ---

def test_entity_id_und_unique_id_der_anlage_und_des_kreises():
    assert entity_id("sensor", "status") == "sensor.smartheat_status"
    assert entity_id("binary_sensor", "notbetrieb") == "binary_sensor.smartheat_notbetrieb"
    assert entity_id("sensor", "zustand", kreis=2) == "sensor.smartheat_k2_zustand"
    assert entity_id("number", "wunschtemperatur", kreis=1) == "number.smartheat_k1_wunschtemperatur"
    assert unique_id("wohnung1", "status") == "wohnung1_status"
    assert unique_id("wohnung1", "zustand", kreis=2) == "wohnung1_k2_zustand"


def test_zustandsmenge_enthaelt_die_werte_der_betriebsarten_und_heizt_auf():
    assert {"kuehl", "heizt_auf", "uebersteuert", "leerstand", "abwesend", "notbetrieb", "einlernphase", "pause",
            "heizt", "boost", "nicht_geregelt"} <= set(KREIS_ZUSTAND_VALUES)
    assert {"regelt", "abo_inaktiv", "abo_beendet", "testphase", "ausser_betrieb"} <= set(KREIS_ZUSTAND_VALUES)
    assert len(set(KREIS_ZUSTAND_VALUES)) == len(KREIS_ZUSTAND_VALUES)


# --- Zuordnung ---

async def test_die_entities_der_anlage_haengen_an_der_anlage_ohne_tenant_im_namen(hass):
    entry = await _setup(hass)
    await _fire(hass, _ereignis())

    eintraege = _eintraege(hass, entry)
    anlage = _geraet(hass, entry, (DOMAIN, TENANT))
    for key in ANLAGE_SENSOREN:
        e = eintraege[f"{TENANT}_{key}"]
        assert (e.entity_id, e.device_id) == (f"sensor.smartheat_{key}", anlage.id), key
    for key in ANLAGE_BINAER:
        e = eintraege[f"{TENANT}_{key}"]
        assert (e.entity_id, e.device_id) == (f"binary_sensor.smartheat_{key}", anlage.id), key


async def test_die_entities_eines_kreises_haengen_an_seinem_geraet(hass):
    entry = await _setup(hass)
    await _fire(hass, _ereignis())

    eintraege = _eintraege(hass, entry)
    for n in (1, 2):
        geraet = _geraet(hass, entry, (DOMAIN, f"{TENANT}_k{n}"))
        for key in KREIS_SENSOREN:
            e = eintraege[f"{TENANT}_k{n}_{key}"]
            assert (e.entity_id, e.device_id) == (f"sensor.smartheat_k{n}_{key}", geraet.id), (n, key)
        e = eintraege[f"{TENANT}_k{n}_wunschtemperatur"]
        assert (e.entity_id, e.device_id) == (f"number.smartheat_k{n}_wunschtemperatur", geraet.id)


async def test_es_gibt_genau_diese_entities_und_keine_alten_oder_alltags_entities(hass):
    entry = await _setup(hass)
    await _fire(hass, _ereignis())

    erwartet = {f"{TENANT}_{key}" for key in (*ANLAGE_SENSOREN, *ANLAGE_BINAER)}
    for n in (1, 2):
        erwartet |= {f"{TENANT}_k{n}_{key}" for key in (*KREIS_SENSOREN, "wunschtemperatur", *HEBEL_SCHLUESSEL)}
    assert set(_eintraege(hass, entry)) == erwartet
    for alt in ("zustand", "kaelteschutz", "boost", "abo", "wunschtemperatur", "heizkurve"):
        assert f"{TENANT}_{alt}" not in _eintraege(hass, entry)


async def test_vor_dem_ersten_ereignis_gibt_es_nur_die_entities_der_anlage(hass):
    entry = await _setup(hass)

    assert {e.unique_id for e in _eintraege(hass, entry).values() if "_k" in e.unique_id} == set()
    assert hass.states.get("sensor.smartheat_status") is not None
    assert hass.states.get("sensor.smartheat_k1_zustand") is None


# --- Werte ---

async def test_jeder_kreis_zeigt_nur_seine_eigenen_werte(hass):
    await _setup(hass)
    eins = _kreis(1, zustand="regelt", kaelteschutz="aus", abo="aktiv", boost="keiner", raum_ist=20.5, raum_soll=21.0,
                  befunde=[], testphase_bis=None, abo_frist_ende="2026-12-01")
    zwei = _kreis(2, zustand="testphase", kaelteschutz="frost", abo="beendet", boost="komfort", raum_ist=17.25,
                  raum_soll=18.0, befunde=[{"a": 1}, {"a": 2}], testphase_bis="2026-12-24", abo_frist_ende=None)

    await _fire(hass, _ereignis(kreise=[eins, zwei]))

    def stand(n, key, plattform="sensor"):
        return hass.states.get(f"{plattform}.smartheat_k{n}_{key}")

    assert [stand(1, k).state for k in ("zustand", "kaelteschutz", "abo", "boost")] == ["regelt", "aus", "aktiv", "keiner"]
    assert [stand(2, k).state for k in ("zustand", "kaelteschutz", "abo", "boost")] == [
        "testphase", "frost", "beendet", "komfort"]
    assert (stand(1, "raumtemperatur").state, stand(2, "raumtemperatur").state) == ("20.5", "17.25")
    assert (stand(1, "befunde").state, stand(2, "befunde").state) == ("0", "2")
    assert stand(2, "zustand").attributes["testphase_bis"] == "2026-12-24"
    assert stand(1, "abo").attributes["frist_ende"] == "2026-12-01"
    assert float(stand(1, "wunschtemperatur", "number").state) == 21.0
    assert float(stand(2, "wunschtemperatur", "number").state) == 18.0


async def test_ein_kreis_ohne_felder_im_ereignis_zeigt_unbekannt_statt_der_werte_eines_anderen(hass):
    await _setup(hass)
    leer = {"kreis": 2, "name": "Leer"}

    await _fire(hass, _ereignis(kreise=[_kreis(1, zustand="regelt", raum_ist=20.0), leer]))

    assert hass.states.get("sensor.smartheat_k1_zustand").state == "regelt"
    for key in ("zustand", "raumtemperatur", "abo", "befunde"):
        assert hass.states.get(f"sensor.smartheat_k2_{key}").state == "unknown", key
    assert hass.states.get("number.smartheat_k2_wunschtemperatur").state == "unknown"


@pytest.mark.parametrize("wert", [None, "kalt", float("nan"), float("inf"), True, 10**400])
async def test_raumtemperatur_ohne_endliche_zahl_ist_unbekannt(hass, wert):
    await _setup(hass)

    await _fire(hass, _ereignis(nummern=(1,), kreise=[_kreis(1, raum_ist=wert)]))

    assert hass.states.get("sensor.smartheat_k1_raumtemperatur").state == "unknown"


async def test_raumtemperatur_ist_ein_temperatursensor_in_grad(hass):
    await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1,), kreise=[_kreis(1, raum_ist=19.5)]))

    attribute = hass.states.get("sensor.smartheat_k1_raumtemperatur").attributes
    assert (attribute["device_class"], attribute["unit_of_measurement"], attribute["state_class"]) == (
        "temperature", "°C", "measurement")


@pytest.mark.parametrize("wert, erwartet", [([], "0"), ([{"a": 1}], "1"), (["x", "y", "z"], "3"),
                                            (None, "unknown"), ("zwei", "unknown"), ({"a": 1}, "unknown")])
async def test_befunde_zaehlt_die_liste_und_kennt_sonst_nichts(hass, wert, erwartet):
    await _setup(hass)

    await _fire(hass, _ereignis(nummern=(1,), kreise=[_kreis(1, befunde=wert)]))

    state = hass.states.get("sensor.smartheat_k1_befunde")
    assert state.state == erwartet
    assert set(state.attributes) - {"friendly_name", "icon", "state_class"} == set()


@pytest.mark.parametrize("wert, erwartet", [(True, "on"), (False, "off"), (None, "unknown"), ("true", "unknown"),
                                            (1, "unknown")])
async def test_server_verbunden_zeigt_nur_einen_echten_wahrheitswert(hass, wert, erwartet):
    await _setup(hass)

    await _fire(hass, _ereignis(server_verbunden=wert))

    state = hass.states.get("binary_sensor.smartheat_server_verbunden")
    assert state.state == erwartet
    assert state.attributes["device_class"] == "connectivity"


async def test_server_verbunden_wird_ohne_ereignis_wiederhergestellt(hass):
    mock_restore_cache(hass, [State("binary_sensor.smartheat_server_verbunden", "on")])

    await _setup(hass)

    assert hass.states.get("binary_sensor.smartheat_server_verbunden").state == "on"


# --- Diagnose und Standard aus ---

async def test_hebel_und_lernwerte_sind_diagnose_und_standardmaessig_aus(hass):
    entry = await _setup(hass)
    await _fire(hass, _ereignis())

    eintraege = _eintraege(hass, entry)
    for n in (1, 2):
        for key in HEBEL_SCHLUESSEL:
            e = eintraege[f"{TENANT}_k{n}_{key}"]
            assert e.disabled_by is er.RegistryEntryDisabler.INTEGRATION, (n, key)
            assert e.entity_category is EntityCategory.DIAGNOSTIC, (n, key)
            assert hass.states.get(e.entity_id) is None


async def test_die_uebrigen_entities_sind_aktiv_und_nur_die_vorgesehenen_diagnose(hass):
    entry = await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1,)))

    diagnose = {"letzte_serverantwort", "datenfehler", "addon_version", "server_verbunden", "k1_boost"}
    for unique, e in _eintraege(hass, entry).items():
        if any(unique.endswith(f"_{key}") for key in HEBEL_SCHLUESSEL):
            continue
        assert e.disabled_by is None, unique
        schluessel = unique.removeprefix(f"{TENANT}_")
        erwartet = EntityCategory.DIAGNOSTIC if schluessel in diagnose else None
        assert e.entity_category is erwartet, unique


async def test_ein_aktivierter_hebel_zeigt_den_wert_seines_kreises(hass):
    entry = await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1, 2)))
    registry = er.async_get(hass)
    for n in (1, 2):
        registry.async_update_entity(_eintraege(hass, entry)[f"{TENANT}_k{n}_heizkurve"].entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    await _fire(hass, _ereignis(kreise=[
        _kreis(1, hebel={"curve": 1.1, "room_setpoint": None, "heat_limit": None, "min_flow": None}),
        _kreis(2, hebel={"curve": 1.7, "room_setpoint": None, "heat_limit": None, "min_flow": None})]))

    assert hass.states.get("sensor.smartheat_k1_heizkurve").state == "1.1"
    assert hass.states.get("sensor.smartheat_k2_heizkurve").state == "1.7"


# --- Dynamik ---

async def test_ein_neuer_kreis_im_ereignis_bekommt_seine_entities(hass):
    entry = await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1,)))
    assert hass.states.get("sensor.smartheat_k2_zustand") is None

    await _fire(hass, _ereignis(kreise=[_kreis(1), _kreis(2, zustand="testphase")]))

    assert hass.states.get("sensor.smartheat_k2_zustand").state == "testphase"
    assert f"{TENANT}_k2_wunschtemperatur" in _eintraege(hass, entry)


async def test_mit_dem_kreis_verschwinden_seine_entities_und_ein_wiederkehrender_bekommt_sie_neu(hass):
    entry = await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1, 2)))
    assert hass.states.get("sensor.smartheat_k2_zustand") is not None

    await _fire(hass, _ereignis(nummern=(1,)))
    assert hass.states.get("sensor.smartheat_k2_zustand") is not None  # erst nach zwei Ereignissen
    await _fire(hass, _ereignis(nummern=(1,)))
    assert hass.states.get("sensor.smartheat_k2_zustand") is None
    assert not [u for u in _eintraege(hass, entry) if u.startswith(f"{TENANT}_k2_")]
    assert hass.states.get("sensor.smartheat_k1_zustand") is not None

    await _fire(hass, _ereignis(kreise=[_kreis(1), _kreis(2, zustand="leerstand")]))
    assert hass.states.get("sensor.smartheat_k2_zustand").state == "leerstand"


async def test_ein_kurz_fehlender_kreis_behaelt_seine_entities_und_legt_sie_nicht_doppelt_an(hass, caplog):
    entry = await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1, 2)))
    await _fire(hass, _ereignis(nummern=(1,)))

    await _fire(hass, _ereignis(nummern=(1, 2)))

    assert hass.states.get("sensor.smartheat_k2_zustand") is not None
    assert "unique ID" not in caplog.text and "already exists" not in caplog.text
    assert len([u for u in _eintraege(hass, entry) if u.startswith(f"{TENANT}_k2_")]) == len(
        [u for u in _eintraege(hass, entry) if u.startswith(f"{TENANT}_k1_")])


async def test_nach_dem_neustart_stellt_ein_bekannter_kreis_seinen_stand_ohne_ereignis_wieder_her(hass):
    entry = await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1, 2)))
    assert await hass.config_entries.async_unload(entry.entry_id)
    mock_restore_cache(hass, [State("sensor.smartheat_k2_zustand", "testphase"),
                              State("number.smartheat_k2_wunschtemperatur", "19.5"),
                              State("sensor.smartheat_k2_befunde", "3")])

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.smartheat_k2_zustand").state == "testphase"
    assert float(hass.states.get("number.smartheat_k2_wunschtemperatur").state) == 19.5
    assert hass.states.get("sensor.smartheat_k2_befunde").state == "3"


async def test_der_wiederhergestellte_zustand_muss_zur_wertemenge_gehoeren(hass):
    entry = await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1,)))
    assert await hass.config_entries.async_unload(entry.entry_id)
    mock_restore_cache(hass, [State("sensor.smartheat_k1_zustand", "gibt_es_nicht")])

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.smartheat_k1_zustand").state == "unknown"


# --- Aufraeumen ---

async def test_die_alten_unique_ids_ohne_kreis_werden_beim_laden_entfernt(hass):
    entry = make_device_entry(hass)
    registry = er.async_get(hass)
    for platform, key in (("sensor", "zustand"), ("sensor", "boost"), ("sensor", "heizkurve"), ("number", "wunschtemperatur"),
                          ("sensor", "abo"), ("sensor", "kaelteschutz")):
        registry.async_get_or_create(platform, DOMAIN, f"{TENANT}_{key}", config_entry=entry)
    gemerkt = registry.async_get_or_create("sensor", DOMAIN, f"{TENANT}_status", config_entry=entry)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    unique_ids = set(_eintraege(hass, entry))
    assert not unique_ids & {f"{TENANT}_{k}" for k in ("zustand", "boost", "heizkurve", "wunschtemperatur", "abo",
                                                         "kaelteschutz")}
    assert gemerkt.unique_id in unique_ids


async def test_ein_kreis_ueber_der_obergrenze_wird_aufgeraeumt_ein_zulaessiger_bleibt(hass):
    entry = make_device_entry(hass)
    registry = er.async_get(hass)
    zu_viel = EINRICHTUNG_MAX_KREISE + 1
    registry.async_get_or_create("sensor", DOMAIN, f"{TENANT}_k{zu_viel}_zustand", config_entry=entry)
    registry.async_get_or_create("sensor", DOMAIN, f"{TENANT}_k{EINRICHTUNG_MAX_KREISE}_zustand", config_entry=entry)
    registry.async_get_or_create("sensor", DOMAIN, f"{TENANT}_k1_unbekannt", config_entry=entry)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    unique_ids = set(_eintraege(hass, entry))
    assert f"{TENANT}_k{EINRICHTUNG_MAX_KREISE}_zustand" in unique_ids
    assert f"{TENANT}_k{zu_viel}_zustand" not in unique_ids and f"{TENANT}_k1_unbekannt" not in unique_ids


# --- Wunschtemperatur je Kreis ---

async def test_die_wunschtemperatur_eines_kreises_meldet_dessen_nummer(hass):
    await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1, 2)))
    gesehen = []
    hass.bus.async_listen(WUNSCH_EVENT, lambda event: gesehen.append(event.data))

    await hass.services.async_call("number", "set_value", {
        "entity_id": "number.smartheat_k2_wunschtemperatur", "value": 19.0}, blocking=True, context=Context())
    await hass.services.async_call("number", "set_value", {
        "entity_id": "number.smartheat_k1_wunschtemperatur", "value": 22.0}, blocking=True, context=Context())
    await hass.async_block_till_done()

    assert [(d["kreis"], d["raum_soll"]) for d in gesehen] == [(2, 19.0), (1, 22.0)]


# --- Texte ---

@pytest.mark.parametrize("datei", ["strings.json", "translations/en.json", "translations/de.json"])
def test_alle_entities_haben_einen_namen_und_alle_zustaende_einen_text(datei):
    texte = json.loads((PAKET / datei).read_text(encoding="utf-8"))["entity"]
    for key in (*ANLAGE_SENSOREN, *KREIS_SENSOREN, *HEBEL_SCHLUESSEL):
        assert texte["sensor"][key]["name"], key
    for key in ANLAGE_BINAER:
        assert texte["binary_sensor"][key]["name"], key
    assert texte["number"]["wunschtemperatur"]["name"]
    assert set(texte["sensor"]["zustand"]["state"]) == set(KREIS_ZUSTAND_VALUES)
    assert set(texte["sensor"]["kaelteschutz"]["state"]) == set(KAELTESCHUTZ_VALUES)
    assert all(texte["sensor"]["zustand"]["state"].values())


def test_icons_stehen_in_der_icons_datei_fuer_jede_entity():
    icons = json.loads((PAKET / "icons.json").read_text(encoding="utf-8"))["entity"]
    for key in (*ANLAGE_SENSOREN, *KREIS_SENSOREN):
        assert icons["sensor"][key], key
    for key in ANLAGE_BINAER:
        assert icons["binary_sensor"][key], key
    assert icons["number"]["wunschtemperatur"]
    for wert in KREIS_ZUSTAND_VALUES:
        assert icons["sensor"]["zustand"]["state"][wert], wert

