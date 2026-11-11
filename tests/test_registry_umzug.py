"""Umzug der Registry-Eintraege einer laufenden Anlage (client1) auf die Kennungen je Kreis: derselbe Eintrag behaelt
seine Registry-Kennung, Einstellungen des Nutzers und damit den Verlauf; vor jeder Aenderung liegt eine Sicherung der
eigenen Eintraege in der Ablage der Integration, ohne sie wird nichts veraendert."""
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from custom_components.smartheat.const import DOMAIN

from .addon_fakes import make_device_entry

SICHERUNG = "smartheat.registry_sicherung"

# Entities von client1 vor dem Update: unique_id aus Anlage und Schluessel, Entity-ID mit Anlagennamen.
ALT_ANLAGE = ("status", "datenfehler", "letzte_serverantwort", "addon_version", "notbetrieb")
ALT_KREIS = ("boost", "abo", "zustand", "kaelteschutz", "heizkurve", "parallelverschiebung", "heizgrenze",
             "mindestvorlauf", "gelernte_steigung", "gelernte_heizgrenze")
BINAER = {"notbetrieb"}


def _alte_entities(hass, entry, tenant_id="client1"):
    """Die Registry einer Anlage vor dem Update; liefert {alte unique_id: Registry-Kennung}."""
    registry = er.async_get(hass)
    geraet = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, tenant_id)})
    kennungen = {}
    for key in (*ALT_ANLAGE, *ALT_KREIS, "wunschtemperatur"):
        plattform = "number" if key == "wunschtemperatur" else "binary_sensor" if key in BINAER else "sensor"
        eintrag = registry.async_get_or_create(
            plattform, DOMAIN, f"{tenant_id}_{key}", config_entry=entry, device_id=geraet.id,
            suggested_object_id=f"smartheat_{tenant_id}_{key}", original_name=key)
        kennungen[eintrag.unique_id] = eintrag.id
    return kennungen


async def _laden(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _eigene(hass, entry):
    return {e.unique_id: e for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)}


async def test_die_eintraege_ziehen_mit_kennung_und_nutzereinstellung_um(hass):
    entry = make_device_entry(hass, tenant_id="client1")
    kennungen = _alte_entities(hass, entry)
    registry = er.async_get(hass)
    registry.async_update_entity("sensor.smartheat_client1_zustand", name="Mein Zustand",
                                 area_id=None, hidden_by=er.RegistryEntryHider.USER)

    await _laden(hass, entry)

    eigene = _eigene(hass, entry)
    for key in ALT_ANLAGE:
        alt = f"client1_{key}"
        assert eigene[alt].id == kennungen[alt]
    for key in (*ALT_KREIS, "wunschtemperatur"):
        neu = eigene[f"client1_k1_{key}"]
        assert neu.id == kennungen[f"client1_{key}"], key
        assert neu.entity_id.split(".")[1] == f"smartheat_k1_{key}"
    zustand = eigene["client1_k1_zustand"]
    assert zustand.entity_id == "sensor.smartheat_k1_zustand"
    assert (zustand.name, zustand.hidden_by) == ("Mein Zustand", er.RegistryEntryHider.USER)
    assert eigene["client1_status"].entity_id == "sensor.smartheat_status"
    assert eigene["client1_notbetrieb"].entity_id == "binary_sensor.smartheat_notbetrieb"
    assert eigene["client1_k1_wunschtemperatur"].entity_id == "number.smartheat_k1_wunschtemperatur"
    assert not [e for e in eigene.values() if "smartheat_client1" in e.entity_id]
    assert not {f"client1_{key}" for key in (*ALT_KREIS, "wunschtemperatur")} & set(eigene)


async def test_eine_vom_nutzer_umbenannte_entity_id_bleibt(hass):
    entry = make_device_entry(hass, tenant_id="client1")
    _alte_entities(hass, entry)
    er.async_get(hass).async_update_entity("sensor.smartheat_client1_boost", new_entity_id="sensor.heizung_boost")

    await _laden(hass, entry)

    boost = _eigene(hass, entry)["client1_k1_boost"]
    assert boost.entity_id == "sensor.heizung_boost"


async def test_die_umgezogenen_entities_gehen_sofort_an_das_geraet_des_ersten_kreises(hass):
    entry = make_device_entry(hass, tenant_id="client1")
    _alte_entities(hass, entry)

    await _laden(hass, entry)

    kreis = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "client1_k1"), entry.entry_id)
    assert kreis is not None
    eigene = _eigene(hass, entry)
    assert eigene["client1_k1_zustand"].device_id == kreis.id
    assert eigene["client1_status"].device_id != kreis.id
    assert hass.states.get("sensor.smartheat_client1_zustand") is None
    assert hass.states.get("sensor.smartheat_k1_zustand") is not None


async def test_vor_der_aenderung_liegt_die_sicherung_der_eigenen_eintraege_in_der_ablage(hass, hass_storage):
    entry = make_device_entry(hass, tenant_id="client1")
    kennungen = _alte_entities(hass, entry)
    fremd = er.async_get(hass).async_get_or_create("sensor", "mypyllant", "fremd", suggested_object_id="fremd")
    registry = er.async_get(hass)
    gesehen = []
    echt = registry.async_update_entity

    def update(*args, **kwargs):
        gesehen.append(SICHERUNG in hass_storage)
        return echt(*args, **kwargs)

    with patch.object(registry, "async_update_entity", side_effect=update), \
            patch.object(registry, "async_remove", wraps=registry.async_remove) as entfernen:
        await _laden(hass, entry)

    assert gesehen and all(gesehen)
    assert entfernen.call_count == 0
    sicherung = hass_storage[SICHERUNG]["data"]["sicherungen"][-1]
    assert {e["unique_id"]: e["id"] for e in sicherung["entities"]} == kennungen
    assert {e["entity_id"] for e in sicherung["entities"]} >= {"sensor.smartheat_client1_zustand"}
    assert fremd.unique_id not in {e["unique_id"] for e in sicherung["entities"]}
    assert sicherung["config_entry"]["entry_id"] == entry.entry_id
    assert sicherung["config_entry"]["data"] == dict(entry.data)
    assert [g["identifiers"] for g in sicherung["geraete"]] == [[[DOMAIN, "client1"]]]
    assert sicherung["gesichert_am"]


async def test_scheitert_die_sicherung_aendert_sich_nichts_und_der_eintrag_laedt_nicht(hass, hass_storage):
    entry = make_device_entry(hass, tenant_id="client1")
    kennungen = _alte_entities(hass, entry)
    vorher = {e.unique_id: e.entity_id for e in _eigene(hass, entry).values()}

    with patch.object(Store, "async_save", side_effect=OSError("Platte voll")):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert {e.unique_id: e.entity_id for e in _eigene(hass, entry).values()} == vorher
    assert {u: e.id for u, e in _eigene(hass, entry).items()} == kennungen
    assert SICHERUNG not in hass_storage


async def test_ohne_alte_eintraege_wird_nichts_gesichert(hass, hass_storage):
    entry = make_device_entry(hass, tenant_id="client1")

    await _laden(hass, entry)

    assert SICHERUNG not in hass_storage


async def test_ein_zweites_laden_aendert_nichts_und_sichert_nicht_erneut(hass, hass_storage):
    entry = make_device_entry(hass, tenant_id="client1")
    _alte_entities(hass, entry)
    await _laden(hass, entry)
    erste = hass_storage[SICHERUNG]["data"]
    nachher = {u: (e.id, e.entity_id) for u, e in _eigene(hass, entry).items()}

    assert await hass.config_entries.async_unload(entry.entry_id)
    registry = er.async_get(hass)
    with patch.object(registry, "async_update_entity") as update, patch.object(registry, "async_remove") as entfernen:
        await _laden(hass, entry)

    assert (update.call_count, entfernen.call_count) == (0, 0)
    assert hass_storage[SICHERUNG]["data"] == erste
    assert {u: (e.id, e.entity_id) for u, e in _eigene(hass, entry).items()} == nachher


async def test_die_sicherung_haelt_hoechstens_drei_staende(hass, hass_storage):
    entry = make_device_entry(hass, tenant_id="client1")
    registry = er.async_get(hass)
    for lauf in range(5):
        registry.async_get_or_create("sensor", DOMAIN, f"client1_alt{lauf}", config_entry=entry,
                                     suggested_object_id=f"smartheat_client1_alt{lauf}")
        await _laden(hass, entry)
        assert await hass.config_entries.async_unload(entry.entry_id)

    sicherungen = hass_storage[SICHERUNG]["data"]["sicherungen"]
    assert len(sicherungen) == 3
    letzte = {e["unique_id"] for e in sicherungen[-1]["entities"]}
    assert "client1_alt4" in letzte and "client1_alt3" not in letzte


async def test_eine_belegte_neue_kennung_laesst_den_alten_eintrag_entfallen(hass):
    entry = make_device_entry(hass, tenant_id="client1")
    registry = er.async_get(hass)
    alt = registry.async_get_or_create("sensor", DOMAIN, "client1_boost", config_entry=entry,
                                       suggested_object_id="smartheat_client1_boost")
    neu = registry.async_get_or_create("sensor", DOMAIN, "client1_k1_boost", config_entry=entry,
                                       suggested_object_id="smartheat_k1_boost")

    await _laden(hass, entry)

    eigene = _eigene(hass, entry)
    assert eigene["client1_k1_boost"].id == neu.id
    assert "client1_boost" not in eigene
    assert registry.async_get(alt.entity_id) is None


async def test_eine_belegte_entity_id_bleibt_unberuehrt_die_kennung_zieht_trotzdem_um(hass):
    entry = make_device_entry(hass, tenant_id="client1")
    _alte_entities(hass, entry)
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", "mypyllant", "fremd", suggested_object_id="smartheat_k1_zustand")

    await _laden(hass, entry)

    zustand = _eigene(hass, entry)["client1_k1_zustand"]
    assert zustand.entity_id == "sensor.smartheat_client1_zustand"
    assert registry.async_get("sensor.smartheat_k1_zustand").platform == "mypyllant"


async def test_nicht_umziehbare_eintraege_werden_nach_der_sicherung_entfernt(hass, hass_storage):
    entry = make_device_entry(hass, tenant_id="client1")
    registry = er.async_get(hass)
    for key in ("offset", "niveau"):  # Hebel anderer Hebelsaetze und ein Sensor aus 0.7.x
        registry.async_get_or_create("sensor", DOMAIN, f"client1_{key}", config_entry=entry,
                                     suggested_object_id=f"smartheat_client1_{key}")
    registry.async_get_or_create("sensor", DOMAIN, "client1_k9_boost", config_entry=entry,
                                 suggested_object_id="smartheat_k9_boost")

    await _laden(hass, entry)

    assert {"client1_offset", "client1_niveau", "client1_k9_boost"}.isdisjoint(_eigene(hass, entry))
    gesichert = {e["unique_id"] for e in hass_storage[SICHERUNG]["data"]["sicherungen"][-1]["entities"]}
    assert {"client1_offset", "client1_niveau", "client1_k9_boost"} <= gesichert


async def test_das_rueckspielen_der_sicherung_stellt_den_alten_stand_her(hass, hass_storage):
    """Rueckweg im Test: die Zeilen der Sicherung (gleiches Format wie `core.entity_registry`) genuegen, um die Registry
    der eigenen Eintraege auf den Stand vor dem Umzug zurueckzusetzen."""
    entry = make_device_entry(hass, tenant_id="client1")
    _alte_entities(hass, entry)
    registry = er.async_get(hass)
    registry.async_update_entity("sensor.smartheat_client1_zustand", name="Mein Zustand")
    vorher = {e.unique_id: (e.id, e.entity_id, e.name, e.original_name, e.device_id, e.platform)
              for e in _eigene(hass, entry).values()}

    await _laden(hass, entry)
    assert {u: v[0] for u, v in vorher.items()} != {u: e.id for u, e in _eigene(hass, entry).items()}
    assert await hass.config_entries.async_unload(entry.entry_id)

    sicherung = hass_storage[SICHERUNG]["data"]["sicherungen"][0]
    for eintrag in _eigene(hass, entry).values():
        registry.async_remove(eintrag.entity_id)
    for zeile in sicherung["entities"]:
        wieder = registry.async_get_or_create(
            zeile["entity_id"].split(".")[0], zeile["platform"], zeile["unique_id"], config_entry=entry,
            device_id=zeile["device_id"], original_name=zeile["original_name"],
            suggested_object_id=zeile["entity_id"].split(".")[1])
        # Die Registry merkt sich gerade entfernte Eintraege und gibt ihnen ihre letzte ID zurueck: die ID der Zeile setzen.
        registry.async_update_entity(wieder.entity_id, name=zeile["name"], new_entity_id=zeile["entity_id"])

    zurueck = {e.unique_id: (e.entity_id, e.name, e.original_name, e.device_id, e.platform)
               for e in _eigene(hass, entry).values()}
    assert zurueck == {u: v[1:] for u, v in vorher.items()}
    assert sicherung["config_entry"]["data"] == dict(entry.data)
    assert sicherung["config_entry"]["version"] == entry.version


@pytest.mark.parametrize("tenant_id", ["client1", "Wohnung-1"])
async def test_der_umzug_findet_auch_anlagen_mit_ungewoehnlichem_namen(hass, tenant_id):
    entry = make_device_entry(hass, tenant_id=tenant_id)
    registry = er.async_get(hass)
    slug = "wohnung_1" if tenant_id == "Wohnung-1" else tenant_id
    registry.async_get_or_create("sensor", DOMAIN, f"{tenant_id}_zustand", config_entry=entry,
                                 suggested_object_id=f"smartheat_{slug}_zustand")
    registry.async_get_or_create("sensor", DOMAIN, f"{tenant_id}_status", config_entry=entry,
                                 suggested_object_id=f"smartheat_{slug}_status")

    await _laden(hass, entry)

    assert registry.async_get("sensor.smartheat_k1_zustand").unique_id == f"{tenant_id}_k1_zustand"
    assert registry.async_get("sensor.smartheat_status").unique_id == f"{tenant_id}_status"
