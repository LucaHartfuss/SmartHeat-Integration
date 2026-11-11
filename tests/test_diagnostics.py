"""Diagnose-Download: Versionen, Waechter, letzter Status mit Kreisen und die Verbindung zum Server -- und nichts, was
einen Menschen, ein Haus oder einen Zeitplan verraet. Die Diagnose nennt nur, was eine Positivliste erlaubt; alles
andere (Anlagen-ID, Namen, Entity-IDs der Zuordnung, Notify-Dienste, Uebernahme-Code, Plan, Abwesenheit, Leerstand,
Rueckkehrzeit, freier Text des Geraets) bleibt draussen, auch wenn das Ereignis es enthaelt. Die Zeit kommt nie von der
Wanduhr."""
import json

import pytest
from homeassistant.const import __version__ as HA_VERSION

from custom_components.smartheat.const import STATUS_EVENT, V5_SCHEMAS
from custom_components.smartheat.diagnostics import async_get_config_entry_diagnostics

from .addon_fakes import GERAET_ID, make_device_entry, status_event

CO = "custom_components.smartheat.coordinator"
TENANT = "wohnung1"

# Jede dieser Zeichenfolgen steht im Ereignis oder im Eintrag und darf nirgends in der Diagnose vorkommen.
GEHEIM = {
    "anlage_id": TENANT,
    "anlagenname": "Villa Sonnenschein",
    "kreisname_1": "Wohnung Erika",
    "kreisname_2": "Dachboden Muster",
    "entity_batterie": "sensor.wz_batterie_privat",
    "entity_fuehler": "sensor.kinderzimmer_fuehler",
    "entity_zuordnung": "climate.wohnzimmer_privat",
    "notify": "notify.mobile_app_pixel_von_erika",
    "claim": "ABCD-EFGH",
    "hinweistext": "Frau Mustermann ist verreist",
    "rueckkehr": "2026-12-24T18:30:00+01:00",
    "wochenplan": "Mo 06:00 22",
    "leerstand_von": "2026-12-20",
    "geraet_rest": "x7k2m4q9r5t3w6y8",
}
GERAET = "shg-" + GEHEIM["geraet_rest"]


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(f"{CO}._now", lambda: now["t"])
    return now


async def _laufend(hass, ereignis=None):
    entry = make_device_entry(hass, geraet_id=GERAET)
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, "claim_code": GEHEIM["claim"], "tenant_id": TENANT},
        options={"notify_services": [GEHEIM["notify"]], "room_sensors": [GEHEIM["entity_fuehler"]],
                 "battery_entities": [GEHEIM["entity_batterie"]], "entity_room_target": GEHEIM["entity_zuordnung"]},
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.bus.async_fire(STATUS_EVENT, ereignis if ereignis is not None else _ereignis())
    await hass.async_block_till_done()
    return entry


def _ereignis(**overrides):
    kreis1 = {
        "kreis": 1, "name": GEHEIM["kreisname_1"], "raum_soll": 21.0, "raum_ist": 20.5, "einlernphase_tag": 3,
        "testphase_bis": None, "befunde": [{"text": GEHEIM["hinweistext"]}], "boost": "keiner",
        "hebelsatz": "vaillant_vrc720", "hebel": {"curve": 1.2, "heat_limit": None}, "gelernt": {"curve": 1.1},
        "abo": "aktiv", "abo_frist_ende": None, "zustand": "heizt", "kaelteschutz": "aus",
        # was der Vertrag (noch) nicht kennt, darf nie in die Diagnose gelangen
        "abwesend": {"bis": GEHEIM["rueckkehr"]}, "wochenplan": GEHEIM["wochenplan"],
        "leerstand": {"von": GEHEIM["leerstand_von"]}, "rueckkehr": GEHEIM["rueckkehr"],
    }
    kreis2 = {**kreis1, "kreis": 2, "name": GEHEIM["kreisname_2"], "zustand": "abwesend", "abwesend": None,
              "wochenplan": None, "leerstand": None}
    return {
        **status_event(TENANT, "regelt", kreise=[kreis1, kreis2]),
        "geraet_id": GERAET, "anlage_name": GEHEIM["anlagenname"], "addon_version": "0.20.0",
        "letzte_serverantwort": "2026-11-11T08:00:00+00:00",
        "hinweise": {"batterie_niedrig": [GEHEIM["entity_batterie"]],
                     "raumfuehler_ausgefallen": [GEHEIM["entity_fuehler"], "sensor.zweiter"],
                     "manueller_eingriff": {"entity": GEHEIM["entity_zuordnung"]}},
        "geraet_hinweise": [{"key": "wunsch_begrenzt", "stufe": "warnung", "text": GEHEIM["hinweistext"]}],
        "mindest_integration": "1.4.0",
        **overrides,
    }


def _text(diagnose) -> str:
    return json.dumps(diagnose, ensure_ascii=False, sort_keys=True)


async def test_die_diagnose_zeigt_den_erlaubten_inhalt_und_sonst_nichts(hass, clock):
    entry = await _laufend(hass)
    clock["t"] = 1090.0

    diagnose = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnose == {
        "versionen": {
            "integration": str(entry.runtime_data.version), "addon": "0.20.0", "home_assistant": HA_VERSION,
            "integration_schema": 1, "v5_schemas": list(V5_SCHEMAS), "status_schema": 4,
        },
        "geraet": "shg-x7k2m4",
        "waechter": {
            "befund": None, "versionsbefund": None, "nicht_laufend_in_folge": 0, "neustarts_letzte_stunde": 0,
            "alter_letztes_ereignis_s": 90,
        },
        "status": {
            "schema": 4, "status": "regelt", "grund": None, "geraet_zustand": "regelt", "server_verbunden": True,
            "notbetrieb": False, "datenfehler": None, "letzte_serverantwort": "2026-11-11T08:00:00+00:00",
            "hinweise": {"raumfuehler_ausgefallen": 2, "batterie_niedrig": 1, "manueller_eingriff": True},
            "geraet_hinweise": [{"key": "wunsch_begrenzt", "stufe": "warnung"}],
            "kreise": [
                {"kreis": 1, "name": "Kreis 1", "raum_soll": 21.0, "raum_ist": 20.5, "einlernphase_tag": 3,
                 "testphase_bis": None, "befunde": 1, "boost": "keiner", "hebelsatz": "vaillant_vrc720",
                 "hebel": {"curve": 1.2, "heat_limit": None}, "gelernt": {"curve": 1.1}, "abo": "aktiv",
                 "abo_frist_ende": None, "zustand": "heizt", "kaelteschutz": "aus"},
                {"kreis": 2, "name": "Kreis 2", "raum_soll": 21.0, "raum_ist": 20.5, "einlernphase_tag": 3,
                 "testphase_bis": None, "befunde": 1, "boost": "keiner", "hebelsatz": "vaillant_vrc720",
                 "hebel": {"curve": 1.2, "heat_limit": None}, "gelernt": {"curve": 1.1}, "abo": "aktiv",
                 "abo_frist_ende": None, "zustand": "abwesend", "kaelteschutz": "aus"},
            ],
        },
    }


async def test_kein_verbotenes_feld_gelangt_in_die_diagnose(hass, clock):
    entry = await _laufend(hass)

    text = _text(await async_get_config_entry_diagnostics(hass, entry))

    for name, wert in GEHEIM.items():
        assert wert not in text, name
    assert GERAET not in text
    assert "claim_code" not in text and "tenant_id" not in text and "anlage_name" not in text


async def test_abwesenheit_plan_und_leerstand_erscheinen_nur_als_ja_nein(hass, clock):
    """Meldet ein kuenftiges Add-on diese Felder je Kreis, zeigt die Diagnose nur, ob sie gesetzt sind."""
    entry = await _laufend(hass)
    coordinator = entry.runtime_data
    coordinator.kreise[1].update(abwesend={"bis": GEHEIM["rueckkehr"]}, wochenplan=GEHEIM["wochenplan"],
                                 leerstand={"von": GEHEIM["leerstand_von"]})
    coordinator.kreise[2].update(abwesend=None, wochenplan=None, leerstand=None)

    diagnose = await async_get_config_entry_diagnostics(hass, entry)

    k1, k2 = diagnose["status"]["kreise"]
    assert (k1["abwesend"], k1["wochenplan"], k1["leerstand"]) == (True, True, True)
    assert (k2["abwesend"], k2["wochenplan"], k2["leerstand"]) == (False, False, False)
    text = _text(diagnose)
    for name in ("rueckkehr", "wochenplan", "leerstand_von"):
        assert GEHEIM[name] not in text
    assert "rueckkehr" not in {schluessel for kreis in (k1, k2) for schluessel in kreis}


@pytest.mark.parametrize("feld", ["status", "grund", "geraet_zustand", "datenfehler"])
async def test_freier_text_in_einem_codefeld_wird_nicht_uebernommen(hass, clock, feld):
    """Ein Feld, das einen Code tragen soll, gibt nie freien Text weiter (veraendertes Add-on, Fremdereignis)."""
    entry = await _laufend(hass, _ereignis(**{feld: GEHEIM["hinweistext"]}))

    diagnose = await async_get_config_entry_diagnostics(hass, entry)

    assert GEHEIM["hinweistext"] not in _text(diagnose)
    assert diagnose["status"][feld] is None


async def test_freier_text_in_kreisfeldern_wird_nicht_uebernommen(hass, clock):
    ereignis = _ereignis()
    ereignis["kreise"][0].update(zustand=GEHEIM["hinweistext"], boost=GEHEIM["wochenplan"],
                                 hebelsatz=GEHEIM["anlagenname"], raum_soll=GEHEIM["hinweistext"],
                                 hebel={GEHEIM["kreisname_1"]: 1.0, "curve": GEHEIM["hinweistext"]},
                                 testphase_bis=GEHEIM["hinweistext"])
    entry = await _laufend(hass, ereignis)

    diagnose = await async_get_config_entry_diagnostics(hass, entry)

    for name in ("hinweistext", "wochenplan", "anlagenname", "kreisname_1"):
        assert GEHEIM[name] not in _text(diagnose), name
    k1 = diagnose["status"]["kreise"][0]
    assert (k1["zustand"], k1["boost"], k1["hebelsatz"], k1["raum_soll"], k1["testphase_bis"]) == (None,) * 5
    assert k1["hebel"] == {}


async def test_die_diagnose_nennt_den_waechterbefund_und_seine_zaehler(hass, clock):
    entry = await _laufend(hass)
    coordinator = entry.runtime_data
    coordinator._watchdog.restarts.extend([clock["t"] - 4000.0, clock["t"] - 100.0, clock["t"] - 10.0])
    coordinator._watchdog.not_running = 2
    coordinator._set_watchdog_status("addon_gestoppt")
    clock["t"] = 1000.0 + 400.0

    waechter = (await async_get_config_entry_diagnostics(hass, entry))["waechter"]

    assert waechter == {"befund": "addon_gestoppt", "versionsbefund": None, "nicht_laufend_in_folge": 2,
                        "neustarts_letzte_stunde": 2, "alter_letztes_ereignis_s": 400}


async def test_ein_versionsbefund_steht_in_der_diagnose(hass, clock):
    entry = await _laufend(hass, {**_ereignis(), "schema": max(V5_SCHEMAS) + 1})

    diagnose = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnose["waechter"]["versionsbefund"] == "integration_veraltet"


async def test_vor_dem_ersten_ereignis_ist_die_diagnose_leer_aber_gueltig(hass, clock):
    entry = make_device_entry(hass, geraet_id=GERAET)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    diagnose = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnose["status"] is None
    assert diagnose["waechter"]["alter_letztes_ereignis_s"] is None
    assert diagnose["versionen"]["addon"] is None
    assert diagnose["geraet"] == "shg-x7k2m4"
    assert GEHEIM["geraet_rest"] not in _text(diagnose)


@pytest.mark.parametrize("kennung", [None, 5, "abc", "shg-", "shg-ABC", "shg-a b c d e f g"])
async def test_eine_unbrauchbare_geraete_id_ergibt_keine_kennung(hass, clock, kennung):
    entry = await _laufend(hass)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "geraet_id": kennung})

    assert (await async_get_config_entry_diagnostics(hass, entry))["geraet"] is None


async def test_die_geraete_id_wird_auf_shg_und_sechs_zeichen_gekuerzt(hass, clock):
    entry = await _laufend(hass)

    assert (await async_get_config_entry_diagnostics(hass, entry))["geraet"] == "shg-x7k2m4"
    assert GERAET_ID not in GERAET
