"""Assistent ueber der Einrichtungs-API (Discovery, manuell, Ersetzen, eingerichtetes Geraet,
Neu konfigurieren, Fehler). Attrappen nur an der Grenze: die Einrichtungs-API ist
`einrichtung_fakes.FakeServer` (autospec des `EinrichtungClient`), der Supervisor `supervisor_fakes.FakeSupervisor`
(Fixture `supervisor`: Store, Add-ons, Optionen mit `bootstrap_url`, Discovery, Mindestversion ueber die Version des
installierten Add-ons). Eigene Funktionen von `addon_control` und `supervisor_client` laufen echt."""
import asyncio
import copy
import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import voluptuous as vol
from aiohasupervisor.exceptions import SupervisorError
from aiohasupervisor.models import AddonBoot, AddonState
from homeassistant.config_entries import SOURCE_HASSIO, SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartheat import config_flow
from custom_components.smartheat.const import ADDON_REPOSITORY_URL, DEFAULT_BASE_URL, DOMAIN, NACHWEIS_EVENT
from custom_components.smartheat.einrichtung_client import ApiError, InvalidResponse, NotFound, ServerError, TooMany

from . import einrichtung_fakes
from .einrichtung_fakes import (
    ANMELDECODE,
    CLAIM_CODE,
    CODE_ANTWORT,
    GERAET_ID,
    GERAETECODE,
    KONFIGURATION,
    NACHWEIS_SIGNATUR,
    NACHWEIS_ZEITSTEMPEL,
    ROLLEN,
    TENANT,
    TOKEN,
    ZUORDNUNG,
    CannotConnect,
    Conflict,
    ErneutAnmelden,
    FakeServer,
    InvalidAuth,
    PortalBestaetigung,
    Rejected,
    TokenUngueltig,
)
from .supervisor_fakes import BRIDGE_SLUG, discovery_message, installed_addon, repository

DISCOVERY = HassioServiceInfo(config={"geraet_id": GERAET_ID, "zustand": "nicht_uebernommen",
                                      "claim_code": CLAIM_CODE},
                              name="Heizungsbruecke", slug=BRIDGE_SLUG, uuid="u1")
LABOR_URL = "https://labor.example.test"
_COMPONENT = Path(__file__).parents[1] / "custom_components" / "smartheat"


def _melden(supervisor, config) -> None:
    """Discovery-Liste des Supervisors: eine Meldung der Heizungsbruecke mit `config`, bei None keine."""
    supervisor.discovery = [] if config is None else [discovery_message(config)]


@pytest.fixture
def server(monkeypatch, supervisor, hass):
    """Einrichtungs-API an der Grenze `EinrichtungClient(session, base_url)`; der Supervisor meldet die installierte
    Heizungsbruecke 1.0.0 mit `bootstrap_url` = LABOR_URL und ihre Discovery mit Uebernahme-Code. Das Add-on antwortet
    auf `nachweis <geraet_id>` mit dem Ereignis `smartheat_nachweis`; `fake.pausen` sammelt die Wartezeiten des Pollings
    (sie laufen nicht wirklich)."""
    fake = FakeServer()
    monkeypatch.setattr(config_flow, "EinrichtungClient", lambda session, base_url: fake.client(base_url))
    supervisor.addon_options[BRIDGE_SLUG] = {"bootstrap_url": LABOR_URL}
    _melden(supervisor, {"geraet_id": GERAET_ID, "zustand": "nicht_uebernommen", "claim_code": CLAIM_CODE})
    monkeypatch.setattr(config_flow, "POLL_SECONDS", 0)
    fake.pausen = []
    vorgerueckt = {"s": 0.0}

    async def pause(sekunden):
        fake.pausen.append(sekunden)
        vorgerueckt["s"] += sekunden
        await asyncio.sleep(0)

    # Die Uhr des Assistenten laeuft um die gewarteten Sekunden vor: eine Schleife, die auf die Frist des Codes wartet,
    # endet in Millisekunden (und nicht erst nach zehn Minuten Wanduhr, wenn ein Test sie falsch beendet).
    monkeypatch.setattr(config_flow, "time", SimpleNamespace(monotonic=lambda: time.monotonic() + vorgerueckt["s"]))
    monkeypatch.setattr(config_flow, "_pause", pause)

    def add_on(slug, zeile):
        if zeile.startswith(b"nachweis "):
            hass.bus.async_fire(NACHWEIS_EVENT, {"schema": 1, "geraet_id": zeile.split()[1].decode(),
                                                 "zeitstempel": NACHWEIS_ZEITSTEMPEL, "signatur": NACHWEIS_SIGNATUR})

    supervisor.stdin_hook = add_on
    return fake


async def _progress(hass, result):
    """Fortschritts-Schritte bis zum naechsten Formular oder Ende durchlaufen."""
    while result["type"] in (FlowResultType.SHOW_PROGRESS, FlowResultType.SHOW_PROGRESS_DONE):
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    return result


async def _configure(hass, result, user_input=None):
    return await _progress(hass, await hass.config_entries.flow.async_configure(result["flow_id"], user_input))


async def _bis_anlage(hass, discovery=DISCOVERY):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=discovery)
    assert result["step_id"] == "bestaetigen"
    return await _configure(hass, result, {})


async def _bis_raeume(hass):
    result = await _bis_anlage(hass)
    assert result["step_id"] == "anlage"
    result = await _configure(hass, result, {"anlage": TENANT})
    assert result["step_id"] == "system"
    result = await _configure(hass, result, {"kreis": "mypyllant|e1|SYSTEM|0",
                                             "profil": "vaillant_gastherme_heizkoerper"})
    assert result["step_id"] == "raeume"
    return result


async def _bis_benachrichtigung(hass):
    result = await _bis_raeume(hass)
    result = await _configure(hass, result, {"raumfuehler": ["sensor.wohnzimmer_temperatur"],
                                             "room_target": "climate.wohnzimmer_thermostat"})
    assert result["step_id"] == "anlagenwerte"
    result = await _configure(hass, result, {
        "curve": "number.zuhause_circuit_0_heating_curve", "room_setpoint": "climate.zuhause_zone_1_circuit_0_climate",
        "outdoor_temp": "sensor.zuhause_outdoor_temperature"})
    assert result["step_id"] == "benachrichtigung"
    return result


async def _bis_zusammenfassung(hass):
    result = await _bis_benachrichtigung(hass)
    result = await _configure(hass, result, {
        "benachrichtigung": ["notify.mobile_app_handy"], "hinweise_aus": [], "batterien": ["sensor.wohnzimmer_batterie"],
        "raum_soll": 21.0})
    assert result["step_id"] == "zusammenfassung"
    return result


async def _eingerichtet(hass, server):
    result = await _configure(hass, await _bis_zusammenfassung(hass), {})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    server.calls.clear()
    return hass.config_entries.async_entries(DOMAIN)[0]


async def _reconfigure(hass, entry):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id})
    return await _progress(hass, result)


# --- Einrichten ---


async def test_discovery_flow_creates_the_entry(hass, server):
    result = await _configure(hass, await _bis_zusammenfassung(hass), {})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == TENANT
    assert result["result"].unique_id == GERAET_ID and result["result"].version == config_flow.SmartHeatConfigFlow.VERSION
    assert result["data"] == {"geraet_id": GERAET_ID, "tenant_id": TENANT, "profile_id": KONFIGURATION["profil"],
                              "lever_set": "vaillant_vrc720", "hebel": KONFIGURATION["hebel"],
                              "shift_lever": "room_setpoint"}
    assert server.base_url == LABOR_URL
    assert ("uebernehmen", GERAET_ID, CLAIM_CODE) in server.calls
    assert ("zuordnung", "mypyllant|e1|SYSTEM|0", "vaillant_gastherme_heizkoerper", "vaillant_vrc720", None) \
        in server.calls
    anfrage = next(call[1] for call in server.calls if call[0] == "einrichten")
    assert anfrage == {
        "tenant_id": TENANT, "kreis": "mypyllant|e1|SYSTEM|0", "profil": "vaillant_gastherme_heizkoerper",
        "hebelsatz": "vaillant_vrc720",
        "rollen": {"curve": "number.zuhause_circuit_0_heating_curve",
                   "room_setpoint": "climate.zuhause_zone_1_circuit_0_climate",
                   "outdoor_temp": "sensor.zuhause_outdoor_temperature", "room_target": "climate.wohnzimmer_thermostat"},
        "raumfuehler": ["sensor.wohnzimmer_temperatur"], "batterien": ["sensor.wohnzimmer_batterie"],
        "benachrichtigung": ["notify.mobile_app_handy"], "hinweise_aus": [], "raum_soll": 21.0}
    assert server.calls[-1] == ("abmelden",)


def _pin(name):
    """Die Literale, die der Assistent aus der Antwort liest (config_flow._VERTRAG_GEPINNT)."""
    return config_flow._VERTRAG_GEPINNT[name][1]


def _nur(objekt, felder):
    return {k: v for k, v in objekt.items() if k in felder}


async def test_der_assistent_kommt_mit_genau_den_gepinnten_feldern_durch(hass, server):
    """F-ha-host-39 Zeile 1: Antworten des Servers, auf genau die gepinnten Literale gekuerzt (je Objekt nur sie),
    reichen dem Assistenten bis `create_entry`: er liest kein Feld ausserhalb der Pins."""
    sicht = _nur(server.geraet_sicht, _pin("ANTWORT_FELDER[geraet]"))
    sicht["anlagen"] = [_nur(a, _pin("GERAET_ANLAGE_FELDER")) for a in sicht["anlagen"]]
    sicht["inventur"] = _nur({"zustand": "keine", "fehler": None}, _pin("GERAET_INVENTUR_FELDER"))
    server.geraet_sicht = sicht
    server.inventur_ergebnis = _nur({"zustand": "fertig", "fehler": None}, _pin("GERAET_INVENTUR_FELDER"))
    vorschlag = {"integrationen": [{
        **_nur(i, _pin("VORSCHLAG_INTEGRATION_FELDER")),
        "kreise": [_nur(k, _pin("VORSCHLAG_KREIS_FELDER")) for k in i["kreise"]],
        "profile": [{**_nur(p, _pin("VORSCHLAG_PROFIL_FELDER")),
                     "hebelsaetze": [_nur(h, _pin("VORSCHLAG_HEBELSATZ_FELDER")) for h in p["hebelsaetze"]]}
                    for p in i["profile"]],
        "vorschlag": _nur(i["vorschlag"], _pin("VORSCHLAG_AUSWAHL_FELDER"))} for i in einrichtung_fakes.VORSCHLAG[
            "integrationen"]]}
    zuordnung = {**_nur(ZUORDNUNG, _pin("ANTWORT_FELDER[zuordnung]")),
                 "rollen": [_nur(r, _pin("ZUORDNUNG_ROLLE_FELDER")) for r in ZUORDNUNG["rollen"]],
                 "raumfuehler": _nur(ZUORDNUNG["raumfuehler"], _pin("RAUMFUEHLER_FELDER")),
                 "room_target": _nur(ZUORDNUNG["room_target"], _pin("RAUMFUEHLER_FELDER"))}
    server.vorschlag_antwort = vorschlag
    server.zuordnung_antwort = zuordnung
    server.pruef_ergebnis = _nur(server.pruef_ergebnis, _pin("ANTWORT_FELDER[pruefen]"))
    server.auftrag_ergebnis = _nur(server.auftrag_ergebnis, _pin("ANTWORT_FELDER[auftrag]"))

    result = await _configure(hass, await _bis_zusammenfassung(hass), {})

    assert result["type"] == FlowResultType.CREATE_ENTRY


async def test_no_token_proof_or_code_is_stored_or_logged(hass, server, caplog):
    caplog.set_level(logging.DEBUG)

    await _configure(hass, await _bis_zusammenfassung(hass), {})

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    stored = str(entry.data) + str(entry.options)
    for secret in (TOKEN, CLAIM_CODE, NACHWEIS_SIGNATUR, GERAETECODE, ANMELDECODE):
        assert secret not in stored and secret not in caplog.text, secret


async def test_battery_of_the_chosen_thermostat_is_preselected(hass, server):
    result = await _bis_benachrichtigung(hass)

    schema = result["data_schema"].schema
    default = next(key for key in schema if key == "batterien").default()
    assert default == ["sensor.wohnzimmer_batterie"]


def _beenden(hass, server, result):
    """Einen im Fortschrittsschritt stehenden Ablauf schliessen (das gehaltene Abholen wird freigegeben)."""
    hass.config_entries.flow.async_abort(result["flow_id"])
    server.freigabe.set()


async def _bis_warten(hass, server, discovery=DISCOVERY):
    """Das Abholen haelt an (`server.halten`): der Ablauf steht im Fortschrittsschritt `warten` mit dem Code. Gibt dessen
    Ergebnis zurueck; der Aufrufer gibt das Abholen frei (`server.freigabe.set()`)."""
    server.halten = True
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=discovery)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    for _ in range(200):
        if result["type"] == FlowResultType.SHOW_PROGRESS and result["step_id"] == "warten":
            for _ in range(200):
                if any(call[0] == "geraetecode_abholen" for call in server.calls):
                    return result
                await asyncio.sleep(0)
            raise AssertionError("das Abholen hat nie begonnen")
        await asyncio.sleep(0)
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    raise AssertionError(result)


async def test_der_ablauf_fordert_den_nachweis_beim_add_on_an_und_holt_dann_das_token(hass, server, supervisor):
    result = await _bis_anlage(hass)

    assert result["step_id"] == "anlage"
    assert supervisor.stdin[0] == (BRIDGE_SLUG, f"nachweis {GERAET_ID}\n\n".encode())
    ablauf = [call[0] for call in server.calls]
    assert ablauf[:3] == ["geraetecode_anfordern", "geraetecode_abholen", "uebernehmen"]
    assert server.calls[0] == ("geraetecode_anfordern", {"geraet_id": GERAET_ID, "zeitstempel": NACHWEIS_ZEITSTEMPEL,
                                                         "signatur": NACHWEIS_SIGNATUR}, "en")
    assert server.calls[1] == ("geraetecode_abholen", GERAETECODE)
    assert server.calls[2] == ("uebernehmen", GERAET_ID, CLAIM_CODE)


async def test_die_sprache_von_home_assistant_geht_an_den_server(hass, server):
    hass.config.language = "de"

    await _bis_anlage(hass)

    assert server.calls[0][2] == "de"


async def test_code_und_link_werden_im_wartenden_schritt_angezeigt_ohne_eingabefelder(hass, server):
    result = await _bis_warten(hass, server)

    assert result["progress_action"] == "warten_eigener_server"
    assert result["description_placeholders"] == {
        "anmeldecode": ANMELDECODE, "link": CODE_ANTWORT["link"], "link_komplett": CODE_ANTWORT["link_komplett"],
        "server": LABOR_URL}
    assert "data_schema" not in result
    assert TOKEN not in str(result) and GERAETECODE not in str(result)
    _beenden(hass, server, result)


async def test_ohne_abweichende_bootstrap_url_nennt_der_schritt_keinen_server(hass, server, supervisor):
    supervisor.addon_options[BRIDGE_SLUG] = {}

    result = await _bis_warten(hass, server)

    assert server.base_url == DEFAULT_BASE_URL
    assert result["progress_action"] == "warten"
    assert "server" not in result["description_placeholders"]
    _beenden(hass, server, result)


async def test_polling_im_takt_der_antwort_und_langsamer_verlaengert_ihn(hass, server):
    server.abholen_folge = [{"zustand": "wartet", "abfrage_s": 5}, {"zustand": "wartet", "abfrage_s": 5},
                            {"zustand": "langsamer", "abfrage_s": 10}, {"zustand": "wartet", "abfrage_s": 10},
                            {"zustand": "bestaetigt"}]

    result = await _bis_anlage(hass)

    assert result["step_id"] == "anlage"
    assert server.pausen == [5, 5, 5, 10, 10]
    assert [c[0] for c in server.calls].count("geraetecode_abholen") == 5


async def test_ein_neuer_takt_in_der_wartet_antwort_gilt_ab_dem_naechsten_mal(hass, server):
    server.abholen_folge = [{"zustand": "wartet", "abfrage_s": 7}, {"zustand": "wartet"}, {"zustand": "bestaetigt"}]

    await _bis_anlage(hass)

    assert server.pausen == [5, 7, 7]


async def test_langsamer_wird_auch_ohne_groesseren_wert_verlaengert(hass, server):
    """Meldet der Server `langsamer` ohne groesseren Takt, wartet der Ablauf trotzdem laenger (nie im selben Takt weiter)."""
    server.abholen_folge = [{"zustand": "langsamer", "abfrage_s": 5}, {"zustand": "langsamer", "abfrage_s": 5},
                            {"zustand": "bestaetigt"}]

    await _bis_anlage(hass)

    assert server.pausen == [5, 10, 15]


async def test_ein_groesserer_takt_aus_langsamer_gilt_ganz(hass, server):
    server.abholen_folge = [{"zustand": "langsamer", "abfrage_s": 20}, {"zustand": "bestaetigt"}]

    await _bis_anlage(hass)

    assert server.pausen == [5, 20]


async def test_der_takt_des_servers_ist_nach_oben_begrenzt(hass, server):
    server.code_antwort = {**CODE_ANTWORT, "abfrage_s": 3000}
    server.abholen_folge = [{"zustand": "wartet", "abfrage_s": 3000}, {"zustand": "bestaetigt"}]

    await _bis_anlage(hass)

    assert server.pausen == [60, 60]


async def test_nach_der_bestaetigung_gehoert_das_token_nur_dem_client(hass, server, caplog):
    caplog.set_level(logging.DEBUG)

    result = await _bis_anlage(hass)

    assert result["step_id"] == "anlage"
    entry_daten = str(hass.config_entries.async_entries(DOMAIN))
    assert TOKEN not in str(result) + entry_daten + caplog.text
    assert server._client.token == TOKEN


async def test_ein_abgelehnter_code_bricht_ab_ohne_zu_uebernehmen(hass, server):
    server.abholen_folge = [{"zustand": "wartet", "abfrage_s": 5}, {"zustand": "abgelehnt"}]

    result = await _bis_anlage(hass)

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "code_abgelehnt")
    assert not any(call[0] == "uebernehmen" for call in server.calls)
    assert server.calls[-1] == ("abmelden",)


async def test_ein_abgelaufener_code_fuehrt_zu_einem_neuen_ablauf_ab_dem_ersten_schritt(hass, server, supervisor):
    server.abholen_folge = [{"zustand": "abgelaufen"}]

    result = await _bis_anlage(hass)

    assert result["step_id"] == "anmelden" and result["errors"] == {"base": "code_abgelaufen"}
    assert result["data_schema"] is None and result["type"] == FlowResultType.FORM
    # der abgelaufene Code wird nicht weiter abgefragt
    assert [c[0] for c in server.calls].count("geraetecode_abholen") == 1 and server.pausen == [5]
    server.abholen_folge = [{"zustand": "bestaetigt"}]
    result = await _configure(hass, result, {})
    assert result["step_id"] == "anlage"
    # neuer Ablauf ab Schritt 1: erneut Nachweis, erneut Code
    assert [call[0] for call in server.calls].count("geraetecode_anfordern") == 2
    assert [line for slug, line in supervisor.stdin if line.startswith(b"nachweis")] == [
        f"nachweis {GERAET_ID}\n\n".encode()] * 2


async def test_laeuft_die_frist_des_codes_ab_ohne_antwort_des_servers_gilt_er_als_abgelaufen(hass, server, monkeypatch):
    zeit = {"jetzt": 1000.0}
    monkeypatch.setattr(config_flow, "time", SimpleNamespace(monotonic=lambda: zeit["jetzt"]))

    async def pause(sekunden):
        zeit["jetzt"] += sekunden
        server.pausen.append(sekunden)

    monkeypatch.setattr(config_flow, "_pause", pause)
    server.abholen_folge = [{"zustand": "wartet", "abfrage_s": 5}]

    result = await _bis_anlage(hass)

    assert result["step_id"] == "anmelden" and result["errors"] == {"base": "code_abgelaufen"}
    assert sum(server.pausen) == 600 and len(server.pausen) == 120


async def test_die_frist_des_codes_ist_nach_oben_begrenzt(hass, server, monkeypatch):
    zeit = {"jetzt": 1000.0}
    monkeypatch.setattr(config_flow, "time", SimpleNamespace(monotonic=lambda: zeit["jetzt"]))

    async def pause(sekunden):
        zeit["jetzt"] += sekunden
        server.pausen.append(sekunden)

    monkeypatch.setattr(config_flow, "_pause", pause)
    server.code_antwort = {**CODE_ANTWORT, "gueltig_s": 3600, "abfrage_s": 60}
    server.abholen_folge = [{"zustand": "wartet", "abfrage_s": 60}]

    result = await _bis_anlage(hass)

    assert result["errors"] == {"base": "code_abgelaufen"} and sum(server.pausen) == 900


async def test_kurze_netzfehler_beim_abholen_werden_ueberbrueckt(hass, server):
    """Zwei Fehler hintereinander brechen den zehnminuetigen Ablauf nicht ab; ein Erfolg setzt den Zaehler zurueck."""
    server.abholen_fehler_folge = [CannotConnect(), ServerError("", 503), None, CannotConnect(), CannotConnect(), None]
    server.abholen_folge = [{"zustand": "wartet", "abfrage_s": 5}, {"zustand": "bestaetigt"}]

    result = await _bis_anlage(hass)

    assert result["step_id"] == "anlage"
    assert [c[0] for c in server.calls].count("geraetecode_abholen") == 6


async def test_drei_netzfehler_hintereinander_beim_abholen_brechen_ab(hass, server):
    server.fehler["geraetecode_abholen"] = CannotConnect("Der SmartHeat-Dienst ist nicht erreichbar.")

    result = await _bis_anlage(hass)

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "cannot_connect")
    assert [c[0] for c in server.calls].count("geraetecode_abholen") == 3


async def test_fehlt_der_nachweis_in_der_wartefrist_bricht_der_ablauf_mit_eigenem_text_ab(hass, server, supervisor,
                                                                                         monkeypatch):
    supervisor.stdin_hook = None
    monkeypatch.setattr(config_flow, "NACHWEIS_WAIT_SECONDS", 0.05)

    result = await _bis_anlage(hass)

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "nachweis_fehlt")
    assert not any(call[0] in ("geraetecode_anfordern", "geraetecode_abholen") for call in server.calls)


async def test_die_wartefrist_fuer_den_nachweis_ist_kurz_genug_fuer_den_nutzer():
    assert 5 <= config_flow.NACHWEIS_WAIT_SECONDS <= 30


@pytest.mark.parametrize(("fehler", "grund", "platzhalter"), [
    (InvalidAuth("Nicht angemeldet.", 401), "nachweis_abgelehnt", None),
    (TooMany("Zu viele Anfragen, bitte später erneut.", 429), "abgelehnt", "Zu viele Anfragen, bitte später erneut."),
    (CannotConnect(), "cannot_connect", None),
    (ServerError("", 503), "cannot_connect", None),
    (InvalidResponse("geraetecode: kein JSON"), "unknown", None),
    (NotFound("", 404), "server_veraltet", None),
    (ApiError("", 405), "server_veraltet", None),
    (ApiError("", 418), "abgelehnt", "-"),
])
async def test_fehler_beim_anfordern_des_codes_enden_mit_eigenem_text(hass, server, fehler, grund, platzhalter):
    server.fehler["geraetecode_anfordern"] = fehler

    result = await _bis_anlage(hass)

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, grund)
    if platzhalter is not None:
        assert result["description_placeholders"]["grund"] == platzhalter
    assert not any(call[0] in ("geraetecode_abholen", "uebernehmen") for call in server.calls)


async def test_ein_alter_server_ohne_geraetecode_beim_abholen_bricht_mit_server_veraltet_ab(hass, server):
    server.fehler["geraetecode_abholen"] = NotFound("", 404)

    result = await _bis_anlage(hass)

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "server_veraltet")
    assert [c[0] for c in server.calls].count("geraetecode_abholen") == 1


@pytest.mark.parametrize(("route", "fehler", "key", "grund"), [
    ("uebernehmen", Rejected("Code oder Gerät unbekannt."), "uebernahme_fehlgeschlagen", "Code oder Gerät unbekannt."),
    ("uebernehmen", Conflict("Diese Anmeldung gehört schon zu einem anderen Gerät.", 409), "anmeldung_gebunden",
     "Diese Anmeldung gehört schon zu einem anderen Gerät."),
    ("uebernehmen", TooMany("Zu viele Fehlversuche, bitte 15 Minuten warten.", 429), "uebernahme_gesperrt",
     "Zu viele Fehlversuche, bitte 15 Minuten warten."),
    ("uebernehmen", CannotConnect(), "cannot_connect", "-"),
    ("uebernehmen", ServerError("", 503), "cannot_connect", "-"),
    ("uebernehmen", InvalidResponse("x"), "unknown", "-"),
    ("uebernehmen", TokenUngueltig("", 401), "sitzung_abgelaufen", "-"),
    ("uebernehmen", ErneutAnmelden("Bitte erneut anmelden.", 403), "erneut_anmelden", "-"),
])
async def test_fehler_beim_uebernehmen_stehen_im_formular_und_beenden_das_token(hass, server, route, fehler, key, grund):
    server.fehler[route] = fehler

    result = await _bis_anlage(hass)

    assert result["step_id"] == "anmelden" and result["errors"] == {"base": key}
    assert result["description_placeholders"]["grund"] == grund
    assert server.calls[-1] == ("abmelden",)


async def test_nach_erneut_anmelden_beginnt_ein_neuer_ablauf_mit_neuem_code(hass, server):
    server.fehler["uebernehmen"] = ErneutAnmelden("Bitte erneut anmelden.", 403)
    result = await _bis_anlage(hass)
    assert result["errors"] == {"base": "erneut_anmelden"}
    del server.fehler["uebernehmen"]

    result = await _configure(hass, result, {})

    assert result["step_id"] == "anlage"
    assert [c[0] for c in server.calls].count("geraetecode_anfordern") == 2
    assert [c[0] for c in server.calls].count("geraetecode_abholen") == 2


async def test_portal_bestaetigung_hat_einen_eigenen_abbruch(hass, server):
    server.fehler["zuordnung"] = PortalBestaetigung("Der Eigentümer muss im Portal bestätigen.", 403)

    result = await _configure(hass, await _bis_raeume_vorher(hass), {
        "kreis": "mypyllant|e1|SYSTEM|0", "profil": "vaillant_gastherme_heizkoerper"})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "portal_bestaetigung")
    assert server.calls[-1] == ("abmelden",)


async def test_portal_bestaetigung_beim_entfernen_hat_den_eigenen_abbruch(hass, server):
    entry = await _eingerichtet(hass, server)
    result = await _reconfigure(hass, entry)
    server.fehler["entfernen"] = PortalBestaetigung("Der Eigentümer muss im Portal bestätigen.", 403)

    result = await _configure(hass, await _configure(hass, result, {"next_step_id": "geraet_entfernen"}), {})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "portal_bestaetigung")
    assert hass.config_entries.async_entries(DOMAIN) == [entry]


async def test_ein_schliessen_im_wartenden_schritt_beendet_das_polling(hass, server):
    result = await _bis_warten(hass, server)
    angefragt = [c[0] for c in server.calls].count("geraetecode_abholen")
    assert angefragt == 1

    hass.config_entries.flow.async_abort(result["flow_id"])
    server.freigabe.set()
    await hass.async_block_till_done()

    assert [c[0] for c in server.calls].count("geraetecode_abholen") == 1
    assert not any(call[0] == "uebernehmen" for call in server.calls)


async def test_es_gibt_kein_passwort_oder_email_feld_mehr(hass, server):
    server.fehler["uebernehmen"] = Rejected("x")

    result = await _bis_anlage(hass)

    assert result["step_id"] == "anmelden" and result["data_schema"] is None
    for path in ("strings.json", "translations/en.json", "translations/de.json"):
        data = json.loads((_COMPONENT / path).read_text(encoding="utf-8"))
        assert "data" not in data["config"]["step"]["anmelden"], path
        assert "invalid_auth" not in data["config"]["error"] and "too_many" not in data["config"]["error"], path


async def test_portal_bestaetigung_beim_uebernehmen_bricht_mit_eigenem_text_ab(hass, server):
    server.fehler["uebernehmen"] = PortalBestaetigung("Der Eigentümer muss im Portal bestätigen.", 403)

    result = await _bis_anlage(hass)

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "portal_bestaetigung")


async def test_server_check_errors_return_to_the_step(hass, server):
    server.pruef_ergebnis = {"fehler": {"raumfuehler": "out_of_range"}, "warnungen": {}}
    result = await _bis_benachrichtigung(hass)

    result = await _configure(hass, result, {"benachrichtigung": [], "hinweise_aus": [], "batterien": [],
                                             "raum_soll": 21.0})

    assert result["step_id"] == "raeume" and result["errors"] == {"raumfuehler": "out_of_range"}


async def test_field_errors_on_setup_return_to_the_mapping(hass, server):
    server.fehler["einrichten"] = Rejected("Die Zuordnung ist nicht vollständig.", {"curve": "entity_unavailable"})

    result = await _configure(hass, await _bis_zusammenfassung(hass), {})

    assert result["step_id"] == "anlagenwerte" and result["errors"] == {"curve": "entity_unavailable"}


@pytest.mark.parametrize(("fehler", "text", "grund"), [
    ({"kreis": "required"}, "Bitte eine Anlage wählen.", "Bitte eine Anlage wählen."),
    ({"tenant_id": "required"}, "", "tenant_id: required"),
])
async def test_field_errors_without_a_form_field_abort_with_the_server_text(hass, server, fehler, text, grund):
    server.fehler["einrichten"] = Rejected(text, fehler)

    result = await _configure(hass, await _bis_zusammenfassung(hass), {})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "abgelehnt")
    assert result["description_placeholders"]["grund"] == grund


async def test_unusable_server_answer_is_an_unexpected_error(hass, server):
    server.fehler["zuordnung"] = InvalidResponse("zuordnung: Antwort ist kein Objekt")

    result = await _configure(hass, await _bis_raeume_vorher(hass), {
        "kreis": "mypyllant|e1|SYSTEM|0", "profil": "vaillant_gastherme_heizkoerper"})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "unknown")
    assert "description_placeholders" not in result or not result["description_placeholders"]


async def test_warnings_are_shown_as_text(hass, server):
    hass.config.language = "de"
    server.pruef_ergebnis = {"fehler": {}, "warnungen": {"stale": ["sensor.wohnzimmer_temperatur"],
                                                         "kuenftig": ["sensor.x"]}}

    result = await _bis_zusammenfassung(hass)

    warnungen = result["description_placeholders"]["warnungen"]
    assert "Seit über 6 Stunden kein neuer Wert: sensor.wohnzimmer_temperatur" in warnungen
    assert "kuenftig: sensor.x" in warnungen


async def test_device_rejection_shows_the_reason(hass, server):
    server.auftrag_ergebnis = {"zustand": "fehlgeschlagen", "fehler": "Die Entity number.x gibt es nicht."}

    result = await _configure(hass, await _bis_zusammenfassung(hass), {})

    assert result["type"] == FlowResultType.ABORT and result["reason"] == "einrichten_fehler"
    assert "number.x" in result["description_placeholders"]["grund"]
    assert not hass.config_entries.async_entries(DOMAIN)
    assert server.calls[-1] == ("abmelden",)


async def test_setup_job_timeout_has_its_own_text(hass, server, monkeypatch):
    server.auftrag_ergebnis = {"zustand": "wartet_bestaetigung", "fehler": None}
    monkeypatch.setattr(config_flow, "AUFTRAG_WAIT_SECONDS", 0)

    result = await _configure(hass, await _bis_zusammenfassung(hass), {})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "einrichten_zeit")


async def test_already_set_up_device_only_creates_the_entry(hass, server, supervisor):
    server.geraet_sicht.update(eingerichtet=True, tenant_id=TENANT, konfiguration=KONFIGURATION)
    _melden(supervisor, {"geraet_id": GERAET_ID, "zustand": "regelt"})

    result = await _bis_anlage(hass, HassioServiceInfo(config={"geraet_id": GERAET_ID, "zustand": "regelt"}, name="x",
                                                       slug=BRIDGE_SLUG, uuid="u2"))
    assert result["step_id"] == "schon_eingerichtet"
    result = await _configure(hass, result, {})

    assert result["type"] == FlowResultType.CREATE_ENTRY and result["data"]["tenant_id"] == TENANT
    assert not any(call[0] in ("einrichten", "inventur") for call in server.calls)
    assert ("uebernehmen", GERAET_ID, None) in server.calls


async def test_discovery_of_a_configured_device_aborts(hass, server):
    await _eingerichtet(hass, server)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=DISCOVERY)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] in ("already_configured", "single_instance_allowed")


ANDERE_ID = "shg-bbbbbbbbbbbbbbbb"


async def test_neue_discovery_beendet_den_offenen_flow_eines_anderen_geraets(hass, server):
    """Meldet die Heizungsbruecke ein anderes Geraet (nach Neuinstallation), bleibt nicht der Flow des alten
    stehen: er wird beendet, nur der neue Flow ist offen. Gegenprobe: dieselbe Geraete-ID bleibt `already_in_progress`
    und laesst den offenen Flow stehen."""
    alt = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=DISCOVERY)
    assert alt["step_id"] == "bestaetigen"

    gleich = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=DISCOVERY)
    assert (gleich["type"], gleich["reason"]) == (FlowResultType.ABORT, "already_in_progress")
    assert [f["flow_id"] for f in hass.config_entries.flow.async_progress_by_handler(DOMAIN)] == [alt["flow_id"]]

    neu = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_HASSIO}, data=HassioServiceInfo(
            config={**DISCOVERY.config, "geraet_id": ANDERE_ID}, name="Heizungsbruecke", slug=BRIDGE_SLUG, uuid="u2"))

    assert neu["step_id"] == "bestaetigen"
    offen = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [(f["flow_id"], f["context"]["unique_id"]) for f in offen] == [(neu["flow_id"], ANDERE_ID)]


async def test_eine_ungueltige_discovery_beendet_keinen_offenen_flow(hass, server):
    """Gegenprobe: die Herkunftspruefung kommt zuerst; eine verworfene Discovery (fremdes Add-on) laesst den
    offenen Flow des Geraets unberuehrt."""
    alt = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=DISCOVERY)

    fremd = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_HASSIO}, data=HassioServiceInfo(
            config={**DISCOVERY.config, "geraet_id": ANDERE_ID}, name="x", slug="xyz_heizungsbruecke", uuid="u7"))

    assert (fremd["type"], fremd["reason"]) == (FlowResultType.ABORT, "discovery_ungueltig")
    assert [f["flow_id"] for f in hass.config_entries.flow.async_progress_by_handler(DOMAIN)] == [alt["flow_id"]]


@pytest.mark.parametrize("config", [{}, {"geraet_id": 7}, {"geraet_id": ""}, {"geraet_id": "shg-aaaaaaaaaaaaaaa1"},
                                    {"geraet_id": "SHG-AAAAAAAAAAAAAAAA"}, {"geraet_id": f"{GERAET_ID} [x](y)"}])
async def test_invalid_discovery_aborts(hass, server, config):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=HassioServiceInfo(
        config=config, name="x", slug=BRIDGE_SLUG, uuid="u3"))

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "discovery_ungueltig")


@pytest.mark.parametrize("addons", [
    [installed_addon(BRIDGE_SLUG)],  # die Discovery stammt von einem anderen Add-on (slug passt nicht)
    [installed_addon("xyz_heizungsbruecke", url="https://github.com/fremd/addons", version="9.9.9")],
    # fremdes Repository, das sich mit unserer url und einem *_heizungsbruecke als Heizungsbruecke ausgibt
    [installed_addon("xyz_heizungsbruecke", url=ADDON_REPOSITORY_URL)],
    [],
])
async def test_discovery_of_a_foreign_addon_aborts(hass, server, supervisor, addons):
    supervisor.installed = addons
    foreign = HassioServiceInfo(config=DISCOVERY.config, name="x", slug="xyz_heizungsbruecke", uuid="u5")

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=foreign)

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "discovery_ungueltig")


async def test_local_addon_imitating_the_bridge_is_rejected(hass, server, supervisor):
    supervisor.installed = [installed_addon("local_heizungsbruecke", url=ADDON_REPOSITORY_URL)]
    local = HassioServiceInfo(config=DISCOVERY.config, name="x", slug="local_heizungsbruecke", uuid="u6")

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=local)

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "discovery_ungueltig")


@pytest.mark.parametrize("discovery", [None, {"geraet_id": "shg-bbbbbbbbbbbbbbbb", "zustand": "nicht_uebernommen",
                                               "claim_code": "ANDERERCODE1"}])
async def test_claim_without_a_trusted_discovery_of_this_device_aborts(hass, server, supervisor, discovery):
    """Nie auf Geraete-ID und Code der ersten Meldung zurueckfallen."""
    _melden(supervisor, discovery)

    result = await _bis_anlage(hass)

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "geraet_nicht_gemeldet")
    assert not any(call[0] == "uebernehmen" for call in server.calls)
    assert server.calls[-1] == ("abmelden",)


@pytest.mark.parametrize("erste", [{"geraet_id": GERAET_ID, "zustand": "startet"},
                                   {"geraet_id": GERAET_ID, "zustand": "nicht_uebernommen", "claim_code": "ALTERCODE123"}],
                         ids=["ohne_code", "mit_altem_code"])
async def test_claim_uses_the_current_discovery_code(hass, server, supervisor, erste):
    """Die erste Discovery kann `startet` ohne Code sein oder einen alten Code tragen; vor dem Uebernehmen liest der
    Assistent sie neu und nimmt nur den frischen Code."""
    _melden(supervisor, {"geraet_id": GERAET_ID, "zustand": "nicht_uebernommen", "claim_code": "NEUERCODE123"})

    await _bis_anlage(hass, HassioServiceInfo(config=erste, name="x", slug=BRIDGE_SLUG, uuid="u4"))

    assert [call for call in server.calls if call[0] == "uebernehmen"] == [("uebernehmen", GERAET_ID, "NEUERCODE123")]


@pytest.mark.parametrize("version", ["0.36.0", "0.99.9"])
async def test_outdated_addon_aborts_before_login(hass, server, supervisor, version):
    supervisor.installed = [installed_addon(BRIDGE_SLUG, version=version)]
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=DISCOVERY)

    result = await _configure(hass, result, {})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "addon_veraltet")
    assert server.calls == [] and server.base_url is None


async def test_the_minimum_addon_version_1_0_0_is_enough(hass, server, supervisor):
    """Mindestversion genau 1.0.0 (MIN_ADDON_VERSIONS): sie reicht bis zur Anmeldung; 0.99.9 bricht ab (Test oben)."""
    supervisor.installed = [installed_addon(BRIDGE_SLUG, version="1.0.0")]
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=DISCOVERY)

    result = await _configure(hass, result, {})

    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "anlage")
    assert server.base_url == LABOR_URL


@pytest.mark.parametrize("start", ["discovery", "manuell"])
async def test_a_version_that_cannot_be_compared_aborts_with_addon_fehler(hass, server, supervisor, caplog, start):
    """Eine nicht vergleichbare Version der Heizungsbruecke ist ein Fehlerpfad (`addon_fehler`) auf beiden Startwegen,
    keine Ausnahme aus dem Schritt; im Log steht nur die Fehlerart, nie die Version."""
    caplog.set_level(logging.DEBUG)
    supervisor.installed = [installed_addon(BRIDGE_SLUG, version="GEHEIM-kein-semver")]

    if start == "discovery":
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=DISCOVERY)
        result = await _configure(hass, result, {})
    else:
        result = await _progress(hass, await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}))

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "addon_fehler")
    assert "Add-on-Version nicht pruefbar (AddonError)" in caplog.text
    assert "GEHEIM" not in caplog.text and "GEHEIM" not in str(result)
    assert server.base_url is None and server.calls == []


@pytest.mark.parametrize("value", ["http://labor.example.test", "", "ftp://labor.example.test", "https://", 42])
async def test_invalid_bootstrap_url_aborts(hass, server, supervisor, value):
    supervisor.addon_options[BRIDGE_SLUG] = {"bootstrap_url": value}
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=DISCOVERY)

    result = await _configure(hass, result, {})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "bootstrap_url_ungueltig")


@pytest.mark.parametrize(("start", "ungueltig"), [
    ("discovery", "http://labor.example.test"), ("discovery", "https://"), ("discovery", "nicht lesbar"),
    ("manuell", "http://labor.example.test"), ("manuell", "https://"),
    # manuell und Optionen nicht lesbar: schon das Starten scheitert (addon_fehler), siehe Add-on-Tests
])
async def test_an_invalid_bootstrap_url_sends_nothing_to_any_server(hass, server, supervisor, start, ungueltig):
    """F-ha-host-35 (Angreifer im LAN): ohne gueltige Adresse entsteht kein Client, es geht keine Anfrage hinaus und
    kein Passwort an eine andere Adresse (auch nicht an die Produktion); auf beiden Wegen des Starts."""
    if ungueltig == "nicht lesbar":
        supervisor.errors[("addons.addon_info", BRIDGE_SLUG)] = SupervisorError("Optionen: geheim")
    else:
        supervisor.addon_options[BRIDGE_SLUG] = {"bootstrap_url": ungueltig}
    if start == "discovery":
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=DISCOVERY)
        result = await _configure(hass, result, {})
    else:
        result = await _progress(hass, await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}))

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "bootstrap_url_ungueltig")
    assert server.base_url is None and server.calls == []


def _doppeltes_repository(supervisor) -> None:
    supervisor.repositories = [repository("abc"), repository("def", source=ADDON_REPOSITORY_URL + "#develop")]


def _fremde_bruecke(supervisor) -> None:
    supervisor.installed = [installed_addon(BRIDGE_SLUG), installed_addon("0badc0de_heizungsbruecke")]
    supervisor.repositories = [repository("abc"), repository("0badc0de", source="https://github.com/fremd/addons")]


async def test_a_foreign_bridge_next_to_ours_aborts_the_login(hass, server, supervisor):
    """Im Ablauf mit vorhandener Bruecke (Discovery der eigenen Bruecke) kein Rueckfall auf die Produktion und
    kein Client. (Ein doppelter Repository-Eintrag verwirft schon die Discovery: `_von_der_heizungsbruecke`.)"""
    _fremde_bruecke(supervisor)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=DISCOVERY)

    result = await _configure(hass, result, {})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "fremde_heizungsbruecke")
    assert server.base_url is None and server.calls == []


@pytest.mark.parametrize(("aufbau", "grund"), [
    (_doppeltes_repository, "repository_mehrdeutig"), (_fremde_bruecke, "fremde_heizungsbruecke")])
async def test_the_manual_start_checks_before_the_installation(hass, server, supervisor, aufbau, grund):
    """`_addon_starten` prueft vor `async_install_and_start`: nichts wird installiert, gestartet, gesetzt."""
    aufbau(supervisor)
    supervisor.installed = [addon for addon in supervisor.installed if addon.slug != BRIDGE_SLUG]  # noch nicht installiert
    supervisor.store_addons = {BRIDGE_SLUG: "1.0.0"}

    result = await _progress(hass, await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}))

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, grund)
    assert supervisor.client.store.install_addon.await_count == 0
    assert supervisor.client.store.add_repository.await_count == 0
    assert supervisor.client.addons.start_addon.await_count == 0
    assert supervisor.supervision == {} and supervisor.actions == []
    assert server.base_url is None and server.calls == []


async def test_the_manual_start_with_a_foreign_bridge_next_to_ours_aborts_before_any_start(hass, server, supervisor):
    _fremde_bruecke(supervisor)
    supervisor.installed[0].state = AddonState.STOPPED

    result = await _progress(hass, await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}))

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "fremde_heizungsbruecke")
    assert supervisor.actions == [] and supervisor.supervision == {}


async def test_replace_needs_confirmation_and_uses_the_old_mapping(hass, server):
    server.geraet_sicht["anlagen"] = [{"tenant_id": TENANT, "aktiv": True, "belegt": True, "ersetzbar": True}]
    result = await _bis_anlage(hass)
    result = await _configure(hass, result, {"anlage": TENANT})
    assert result["step_id"] == "ersetzen"

    result = await _configure(hass, result, {})
    assert result["step_id"] == "system"
    await _configure(hass, result, {"kreis": "mypyllant|e1|SYSTEM|0", "profil": "vaillant_gastherme_heizkoerper"})

    assert ("zuordnung", "mypyllant|e1|SYSTEM|0", "vaillant_gastherme_heizkoerper", "vaillant_vrc720", TENANT) \
        in server.calls


async def test_replace_sends_the_replace_flag(hass, server):
    server.geraet_sicht["anlagen"] = [{"tenant_id": TENANT, "aktiv": True, "belegt": True, "ersetzbar": True}]
    result = await _bis_anlage(hass)
    result = await _configure(hass, await _configure(hass, result, {"anlage": TENANT}), {})
    result = await _configure(hass, result, {"kreis": "mypyllant|e1|SYSTEM|0",
                                             "profil": "vaillant_gastherme_heizkoerper"})
    result = await _configure(hass, result, {"raumfuehler": ["sensor.wohnzimmer_temperatur"],
                                             "room_target": "climate.wohnzimmer_thermostat"})
    result = await _configure(hass, result, {"curve": "number.zuhause_circuit_0_heating_curve",
                                             "room_setpoint": "climate.zuhause_zone_1_circuit_0_climate",
                                             "outdoor_temp": "sensor.zuhause_outdoor_temperature"})
    result = await _configure(hass, result, {"raum_soll": 20.0})
    await _configure(hass, result, {})

    anfrage = next(call[1] for call in server.calls if call[0] == "einrichten")
    assert anfrage["ersetzen"] is True and anfrage["tenant_id"] == TENANT


@pytest.mark.parametrize("anlage", [
    {"tenant_id": TENANT, "aktiv": False, "belegt": False, "ersetzbar": False},
    {"tenant_id": TENANT, "aktiv": True, "belegt": True, "ersetzbar": False},
])
async def test_tenant_that_cannot_be_used_is_not_offered(hass, server, anlage):
    server.geraet_sicht["anlagen"] = [anlage]

    result = await _bis_anlage(hass)

    assert result["type"] == FlowResultType.ABORT and result["reason"] == "keine_anlage"
    assert server.calls[-1] == ("abmelden",)


async def test_a_free_tenant_without_altpfad_is_offered(hass, server):
    """Stufe 4 (V3 ohne altpfad): eine aktive, freie Anlage wird angeboten."""
    server.geraet_sicht["anlagen"] = [{"tenant_id": TENANT, "aktiv": True, "belegt": False, "ersetzbar": False}]

    result = await _bis_anlage(hass)

    assert result["type"] == FlowResultType.FORM and result["step_id"] == "anlage"


async def test_inventory_timeout_can_be_retried(hass, server, monkeypatch):
    server.inventur_ergebnis = {"zustand": "laeuft"}
    monkeypatch.setattr(config_flow, "INVENTUR_WAIT_SECONDS", 0)
    result = await _configure(hass, await _bis_anlage(hass), {"anlage": TENANT})
    assert result["step_id"] == "inventur_zeit"

    server.inventur_ergebnis = {"zustand": "fertig"}
    result = await _configure(hass, result, {})

    assert result["step_id"] == "system"
    assert server.calls.count(("inventur",)) == 2


async def test_failed_inventory_shows_the_device_text(hass, server):
    server.inventur_ergebnis = {"zustand": "fehlgeschlagen", "fehler": "Registry nicht lesbar"}

    result = await _configure(hass, await _bis_anlage(hass), {"anlage": TENANT})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "inventur_fehler")
    assert result["description_placeholders"]["grund"] == "Registry nicht lesbar"


async def test_no_supported_integration_shows_the_server_text(hass, server):
    server.fehler["vorschlag"] = Conflict("In Home Assistant ist keine unterstützte Heizungs-Integration "
                                          "eingerichtet (myVAILLANT).", 409)

    result = await _configure(hass, await _bis_anlage(hass), {"anlage": TENANT})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "keine_integration")
    assert "myVAILLANT" in result["description_placeholders"]["grund"]


@pytest.mark.parametrize("inventur", [{"zustand": "fehlgeschlagen", "fehler": None}, {"zustand": "fehlgeschlagen"},
                                      {"zustand": "fehlgeschlagen", "fehler": ""},
                                      {"zustand": "fehlgeschlagen", "fehler": 7}],
                         ids=["null", "fehlt", "leer", "zahl"])
async def test_abgelaufene_inventur_nennt_das_nicht_erreichbare_geraet(hass, server, inventur):
    """Ein abgelaufener Inventur-Befehl hat keinen Fehlertext; der Abbruch nennt dann, dass das Geraet nicht
    erreichbar war (Text der UI-Sprache), nicht „-“."""
    hass.config.language = "de"
    server.inventur_ergebnis = inventur

    result = await _configure(hass, await _bis_anlage(hass), {"anlage": TENANT})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "inventur_fehler")
    assert result["description_placeholders"] == {"grund": "Das Gerät ist nicht erreichbar."}
    text = json.loads((_COMPONENT / "translations/de.json").read_text(encoding="utf-8"))["config"]["abort"][
        "inventur_fehler"]
    assert text.format(**result["description_placeholders"]) == (
        "Die Anlage ließ sich nicht prüfen: Das Gerät ist nicht erreichbar.")


def _integration(plattform="mypyllant", label="myVAILLANT", kreise=None, hebelsaetze=None, profile=None):
    """Eine Integration des Vorschlags nach dem Muster von einrichtung_fakes.VORSCHLAG."""
    integration = copy.deepcopy(einrichtung_fakes.VORSCHLAG["integrationen"][0])
    integration.update(plattform=plattform, label=label)
    if kreise is not None:
        integration["kreise"] = kreise
    if hebelsaetze is not None:
        integration["profile"][0]["hebelsaetze"] = hebelsaetze
    if profile is not None:
        integration["profile"] = profile
    return integration


def _optionen(result, feld):
    selector = next(value for key, value in result["data_schema"].schema.items() if key == feld)
    return [option["value"] if isinstance(option, dict) else option for option in selector.config["options"]]


async def test_several_integrations_show_the_selection_step(hass, server):
    server.vorschlag_antwort = {"integrationen": [_integration(), _integration("vicare", "ViCare")]}

    result = await _configure(hass, await _bis_anlage(hass), {"anlage": TENANT})
    assert result["step_id"] == "integration"
    assert _optionen(result, "integration") == ["mypyllant", "vicare"]
    result = await _configure(hass, result, {"integration": "vicare"})

    assert result["step_id"] == "system" and result["description_placeholders"]["integration"] == "ViCare"


async def _anlage_waehlen_per_rest(hass, eingabe):
    """Wie die REST-API von HA: der Client schickt waehrend des Fortschritts der Inventur `eingabe` statt keiner
    Eingabe; HA reicht sie an den naechsten Schritt (integration) weiter, der kein Formular gezeigt hat (im
    Labor beobachtet)."""
    result = await hass.config_entries.flow.async_configure((await _bis_anlage(hass))["flow_id"], {"anlage": TENANT})
    assert result["type"] == FlowResultType.SHOW_PROGRESS
    while result["type"] in (FlowResultType.SHOW_PROGRESS, FlowResultType.SHOW_PROGRESS_DONE):
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"], eingabe)
    return result


async def test_an_empty_input_after_the_inventory_selects_the_only_integration(hass, server):
    result = await _anlage_waehlen_per_rest(hass, {})
    assert result["step_id"] == "system"


@pytest.mark.parametrize("eingabe", [{}, {"integration": "fremd"}, {"integration": None}],
                         ids=["leer", "unbekannt", "none"])
async def test_an_input_that_names_no_offered_integration_shows_the_selection(hass, server, eingabe):
    server.vorschlag_antwort = {"integrationen": [_integration(), _integration("vicare", "ViCare")]}
    result = await _anlage_waehlen_per_rest(hass, eingabe)
    assert result["step_id"] == "integration" and _optionen(result, "integration") == ["mypyllant", "vicare"]


async def test_one_integration_and_one_lever_set_skip_their_steps(hass, server):
    """Genau eine Integration und ein Hebelsatz: der Assistent geht ohne Auswahlschritt weiter (nach `system` direkt
    in `raeume`, die Zuordnung mit dem einzigen Hebelsatz)."""
    result = await _configure(hass, await _bis_anlage(hass), {"anlage": TENANT})
    assert result["step_id"] == "system"

    result = await _configure(hass, result, {"kreis": "mypyllant|e1|SYSTEM|0", "profil": "vaillant_gastherme_heizkoerper"})

    assert result["step_id"] == "raeume"
    assert ("zuordnung", "mypyllant|e1|SYSTEM|0", "vaillant_gastherme_heizkoerper", "vaillant_vrc720", None) \
        in server.calls


async def test_several_systems_and_lever_sets_are_offered(hass, server):
    kreise = [{"schluessel": "mypyllant|e1|SYSTEM|0", "label": "Zuhause"},
              {"schluessel": "mypyllant|e1|SYSTEM|1", "label": "Werkstatt"}]
    hebelsaetze = [{"id": "vaillant_vrc720", "hebel": ["curve"]}, {"id": "vaillant_vrc700", "hebel": ["curve"]}]
    server.vorschlag_antwort = {"integrationen": [_integration(kreise=kreise, hebelsaetze=hebelsaetze)]}

    result = await _configure(hass, await _bis_anlage(hass), {"anlage": TENANT})
    assert result["step_id"] == "system"
    assert _optionen(result, "kreis") == ["mypyllant|e1|SYSTEM|0", "mypyllant|e1|SYSTEM|1"]
    result = await _configure(hass, result, {"kreis": "mypyllant|e1|SYSTEM|1", "profil": "vaillant_gastherme_heizkoerper"})
    assert result["step_id"] == "hebelsatz"
    assert _optionen(result, "hebelsatz") == ["vaillant_vrc720", "vaillant_vrc700"]
    assert not any(call[0] == "zuordnung" for call in server.calls)
    result = await _configure(hass, result, {"hebelsatz": "vaillant_vrc700"})

    assert result["step_id"] == "raeume"
    assert ("zuordnung", "mypyllant|e1|SYSTEM|1", "vaillant_gastherme_heizkoerper", "vaillant_vrc700", None) \
        in server.calls


@pytest.mark.parametrize("integration", [
    _integration(kreise=[]),
    _integration(profile=[]),
    _integration(hebelsaetze=[]),
], ids=["ohne_kreise", "ohne_profile", "profil_ohne_hebelsatz"])
async def test_an_integration_without_circuit_or_lever_set_is_not_supported(hass, server, integration):
    server.vorschlag_antwort = {"integrationen": [integration]}

    result = await _configure(hass, await _bis_anlage(hass), {"anlage": TENANT})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "nicht_unterstuetzt")
    assert result["description_placeholders"] == {"integration": "myVAILLANT"}
    assert server.calls[-1] == ("abmelden",)


@pytest.mark.parametrize("route", ["zuordnung", "pruefen"])
@pytest.mark.parametrize("error", [Rejected("Diesen Heizkreis hat Home Assistant nicht gemeldet."),
                                   Conflict("Bitte zuerst die Anlage in Home Assistant prüfen lassen.", 409)])
async def test_server_rejection_aborts_with_its_text_and_logs_out(hass, server, route, error):
    server.fehler[route] = error

    if route == "zuordnung":
        result = await _configure(hass, await _bis_raeume_vorher(hass), {
            "kreis": "mypyllant|e1|SYSTEM|0", "profil": "vaillant_gastherme_heizkoerper"})
    else:
        result = await _configure(hass, await _bis_benachrichtigung(hass), {"raum_soll": 21.0})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "abgelehnt")
    assert result["description_placeholders"]["grund"] == error.text
    assert server.calls[-1] == ("abmelden",)


async def _bis_raeume_vorher(hass):
    """Bis zum Formular `system` (der naechste Schritt ruft `zuordnung`)."""
    result = await _configure(hass, await _bis_anlage(hass), {"anlage": TENANT})
    assert result["step_id"] == "system"
    return result


async def test_server_unreachable_aborts(hass, server):
    server.fehler["zuordnung"] = CannotConnect("Der SmartHeat-Dienst ist nicht erreichbar.")

    result = await _configure(hass, await _bis_raeume_vorher(hass), {
        "kreis": "mypyllant|e1|SYSTEM|0", "profil": "vaillant_gastherme_heizkoerper"})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "cannot_connect")


async def test_expired_token_goes_back_to_login(hass, server):
    server.fehler["pruefen"] = TokenUngueltig("", 401)
    result = await _bis_benachrichtigung(hass)

    result = await _configure(hass, result, {"raum_soll": 21.0})

    assert result["step_id"] == "anmelden" and result["errors"] == {"base": "sitzung_abgelaufen"}
    del server.fehler["pruefen"]
    result = await _configure(hass, result, {})
    assert result["step_id"] == "anlage"
    assert [c[0] for c in server.calls].count("geraetecode_anfordern") == 2


async def test_required_role_without_candidates_aborts_with_a_hint(hass, server):
    hass.config.language = "de"
    server.zuordnung_antwort = {**ZUORDNUNG, "rollen": [*ROLLEN[:2], {**ROLLEN[2], "vorschlag": None,
                                                                       "kandidaten": []}]}

    result = await _configure(hass, await _bis_raeume_vorher(hass), {
        "kreis": "mypyllant|e1|SYSTEM|0", "profil": "vaillant_gastherme_heizkoerper"})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "rolle_ohne_kandidaten")
    assert result["description_placeholders"]["rolle"] == "Außentemperatur"


@pytest.mark.parametrize(("feld", "bezeichnung"), [("raumfuehler", "Raumfühler"),
                                                   ("room_target", "Wunschtemperatur-Entity")])
async def test_ohne_raumfuehler_kandidaten_bricht_ab(hass, server, feld, bezeichnung):
    """Ohne Kandidaten fuer Raumfuehler oder Raum-Soll bricht der Assistent wie bei einer Pflichtrolle mit
    `rolle_ohne_kandidaten` und der Bezeichnung des Felds ab (statt ein Pflichtfeld mit leerem Selektor zu zeigen)."""
    hass.config.language = "de"
    server.zuordnung_antwort = {**ZUORDNUNG, feld: {"kandidaten": [], "vorschlag": None}}

    result = await _configure(hass, await _bis_raeume_vorher(hass), {
        "kreis": "mypyllant|e1|SYSTEM|0", "profil": "vaillant_gastherme_heizkoerper"})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "rolle_ohne_kandidaten")
    assert result["description_placeholders"] == {"rolle": bezeichnung}
    assert server.calls[-1] == ("abmelden",)


_VIELE = {"raumfuehler": [f"sensor.raum_{i}" for i in range(1, 10)],
          "batterien": [f"sensor.batterie_{i}" for i in range(1, 18)],
          "benachrichtigung": [f"notify.mobile_app_handy_{i}" for i in range(1, 12)]}


def _viele_kandidaten(server) -> None:
    raeume = [*_VIELE["raumfuehler"], "climate.wohnzimmer_thermostat"]
    server.zuordnung_antwort = {
        **ZUORDNUNG, "raumfuehler": {"kandidaten": raeume, "vorschlag": []},
        "room_target": {"kandidaten": raeume, "vorschlag": None},
        "batterie_je_entity": {f"sensor.raum_{i}": f"sensor.batterie_{i}" for i in range(1, 18)},
        "benachrichtigung": _VIELE["benachrichtigung"]}


_ANLAGENWERTE = {"curve": "number.zuhause_circuit_0_heating_curve",
                 "room_setpoint": "climate.zuhause_zone_1_circuit_0_climate",
                 "outdoor_temp": "sensor.zuhause_outdoor_temperature"}


@pytest.mark.parametrize("feld", ["raumfuehler", "batterien", "benachrichtigung"])
async def test_eine_auswahl_an_der_grenze_wird_angenommen(hass, server, feld):
    """Gegenprobe: genau 8 Raumfuehler, 16 Batterien, 10 Handys gehen zur Pruefung und zum Server."""
    grenze = {"raumfuehler": 8, "batterien": 16, "benachrichtigung": 10}[feld]
    _viele_kandidaten(server)
    auswahl = {key: werte[:1] for key, werte in _VIELE.items()} | {feld: _VIELE[feld][:grenze]}
    result = await _configure(hass, await _bis_raeume(hass), {"raumfuehler": auswahl["raumfuehler"],
                                                              "room_target": "climate.wohnzimmer_thermostat"})
    result = await _configure(hass, result, _ANLAGENWERTE)

    result = await _configure(hass, result, {"benachrichtigung": auswahl["benachrichtigung"], "hinweise_aus": [],
                                             "batterien": auswahl["batterien"], "raum_soll": 21.0})

    assert result["step_id"] == "zusammenfassung" and not result.get("errors")
    anfrage = next(call[1] for call in server.calls if call[0] == "pruefen")
    assert len(anfrage[feld]) == grenze


async def test_neun_raumfuehler_markieren_das_feld(hass, server):
    """9 Raumfuehler (Grenze 8) zeigen das Formular `raeume` wieder, Feld `raumfuehler` mit `zu_viele`; die
    Auswahl bleibt vorbelegt, es geht nichts an den Server."""
    _viele_kandidaten(server)
    result = await _bis_raeume(hass)

    result = await _configure(hass, result, {"raumfuehler": _VIELE["raumfuehler"],
                                             "room_target": "climate.wohnzimmer_thermostat"})

    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "raeume")
    assert result["errors"] == {"raumfuehler": "zu_viele"}
    vorbelegt = {str(key): key.default() for key in result["data_schema"].schema if key.default is not vol.UNDEFINED}
    assert vorbelegt["raumfuehler"] == _VIELE["raumfuehler"]
    assert not any(call[0] in ("pruefen", "einrichten") for call in server.calls)
    # korrigiert: weiter zu den Anlagenwerten, der Fehler ist weg
    result = await _configure(hass, result, {"raumfuehler": _VIELE["raumfuehler"][:8],
                                             "room_target": "climate.wohnzimmer_thermostat"})
    assert result["step_id"] == "anlagenwerte" and not result.get("errors")


@pytest.mark.parametrize(("feld", "zu_viele"), [("batterien", 17), ("benachrichtigung", 11)])
async def test_zu_viele_batterien_oder_handys_markieren_das_feld(hass, server, feld, zu_viele):
    """17 Batterien (Grenze 16) bzw. 11 Handys (Grenze 10) zeigen `benachrichtigung` wieder, am Feld mit
    `zu_viele`; keine Pruefung beim Server."""
    _viele_kandidaten(server)
    auswahl = {key: werte[:1] for key, werte in _VIELE.items()} | {feld: _VIELE[feld]}
    assert len(auswahl[feld]) == zu_viele
    result = await _configure(hass, await _bis_raeume(hass), {"raumfuehler": auswahl["raumfuehler"],
                                                              "room_target": "climate.wohnzimmer_thermostat"})
    result = await _configure(hass, result, _ANLAGENWERTE)
    assert result["step_id"] == "benachrichtigung"

    result = await _configure(hass, result, {"benachrichtigung": auswahl["benachrichtigung"], "hinweise_aus": [],
                                             "batterien": auswahl["batterien"], "raum_soll": 21.0})

    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "benachrichtigung")
    assert result["errors"] == {feld: "zu_viele"}
    assert not any(call[0] in ("pruefen", "einrichten") for call in server.calls)
    # korrigiert (auf die Grenze): weiter zur Zusammenfassung
    grenze = {"batterien": 16, "benachrichtigung": 10}[feld]
    result = await _configure(hass, result, {"benachrichtigung": auswahl["benachrichtigung"][:grenze],
                                             "hinweise_aus": [], "batterien": auswahl["batterien"][:grenze],
                                             "raum_soll": 21.0})
    assert result["step_id"] == "zusammenfassung" and not result.get("errors")


async def test_die_grenzen_gelten_auch_unmittelbar_vor_der_pruefung(hass, server):
    """Auch wenn eine zu grosse Auswahl bis zur Zusammenfassung gelangt waere, geht sie nicht zum Server:
    die Pruefung vor `pruefen` schickt in das Formular des Felds zurueck."""
    _viele_kandidaten(server)
    result = await _configure(hass, await _bis_raeume(hass), {"raumfuehler": _VIELE["raumfuehler"][:1],
                                                              "room_target": "climate.wohnzimmer_thermostat"})
    result = await _configure(hass, result, _ANLAGENWERTE)
    hass.config_entries.flow._progress[result["flow_id"]]._eingaben["raumfuehler"] = _VIELE["raumfuehler"]

    result = await _configure(hass, result, {"benachrichtigung": [], "hinweise_aus": [], "raum_soll": 21.0})

    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "raeume")
    assert result["errors"] == {"raumfuehler": "zu_viele"}
    assert not any(call[0] == "pruefen" for call in server.calls)


async def test_closing_the_dialog_logs_out(hass, server):
    result = await _bis_anlage(hass)
    assert result["step_id"] == "anlage"

    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done()

    assert server.calls[-1] == ("abmelden",)


# --- Fortschrittsschritte ---


@pytest.mark.parametrize("quelle", ["supervisor", "server"])
async def test_an_unexpected_error_in_a_progress_step_aborts_and_logs_only_its_type(hass, server, supervisor, caplog,
                                                                                   quelle):
    """F-ha-host-46: eine unerwartete Ausnahme im Task eines Fortschrittsschritts endet mit `unknown`; der Log nennt die
    Ausnahmeart, nie ihren Text (er kann Werte von Supervisor oder Server zitieren)."""
    caplog.set_level(logging.DEBUG)
    if quelle == "supervisor":
        # Keine SupervisorError (die faengt der Code): eine fremde Ausnahme des Clients mit Text (Schritt addon_starten).
        supervisor.errors["store.repositories_list"] = RuntimeError("GEHEIM-supervisor")
        result = await _progress(hass, await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}))
        art = "RuntimeError"
    else:
        server.fehler["inventur"] = RuntimeError("GEHEIM-server")  # Schritt inventur
        result = await _configure(hass, await _bis_anlage(hass), {"anlage": TENANT})
        art = "RuntimeError"
        assert server.calls[-1] == ("abmelden",)

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "unknown")
    assert f"({art}), Aufrufstelle:" in caplog.text
    assert 'config_flow.py", line' in caplog.text  # die Aufrufstelle nennt den Rahmen des Assistenten
    assert "GEHEIM" not in caplog.text


@pytest.mark.parametrize(("fehler", "key"), [(TokenUngueltig("", 401), "sitzung_abgelaufen"),
                                              (ErneutAnmelden("Bitte erneut anmelden.", 403), "erneut_anmelden")])
@pytest.mark.parametrize("route", ["inventur", "einrichten", "entfernen"])
async def test_an_expired_session_in_a_progress_step_goes_back_to_login(hass, server, route, fehler, key):
    if route == "entfernen":
        result = await _reconfigure(hass, await _eingerichtet(hass, server))
        result = await _configure(hass, result, {"next_step_id": "geraet_entfernen"})
        weiter, danach = {}, "menue"
    elif route == "inventur":
        result, weiter, danach = await _bis_anlage(hass), {"anlage": TENANT}, "anlage"
    else:
        result, weiter, danach = await _bis_zusammenfassung(hass), {}, "anlage"
    server.fehler[route] = fehler

    result = await _configure(hass, result, weiter)

    assert (result["step_id"], result["errors"]) == ("anmelden", {"base": key})
    assert server.calls[-2][0] == route and server.calls[-1] == ("abmelden",)
    del server.fehler[route]
    result = await _configure(hass, result, {})
    assert result["step_id"] == danach


# --- Manueller Start ---


def _nicht_installiert(supervisor, version: str = "1.0.0") -> None:
    """Repository im Store, die Heizungsbruecke installierbar, aber nicht installiert."""
    supervisor.installed = []
    supervisor.store_addons = {BRIDGE_SLUG: version}


async def test_manual_start_installs_the_addon_and_waits_for_discovery(hass, server, supervisor):
    _nicht_installiert(supervisor)
    mit_code = [discovery_message({"geraet_id": GERAET_ID, "zustand": "nicht_uebernommen", "claim_code": CLAIM_CODE})]
    folge = [[], [discovery_message({"geraet_id": GERAET_ID, "zustand": "startet"})]]
    supervisor.client.discovery.list.side_effect = lambda: folge.pop(0) if folge else mit_code
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await _progress(hass, result)
    assert result["step_id"] == "anlage"
    assert [(addon.slug, addon.state) for addon in supervisor.installed] == [(BRIDGE_SLUG, AddonState.STARTED)]
    assert supervisor.supervision == {BRIDGE_SLUG: {"watchdog": True, "boot": AddonBoot.AUTO}}
    assert supervisor.config_written == [] and folge == []
    assert ("uebernehmen", GERAET_ID, CLAIM_CODE) in server.calls
    assert server.base_url == LABOR_URL


async def test_manual_start_without_supervisor_aborts(hass, server, supervisor):
    supervisor.hassio = False

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "kein_supervisor")
    assert supervisor.client.store.repositories_list.await_count == 0


async def test_manual_start_with_an_outdated_addon_does_not_wait(hass, server, supervisor):
    """Ein zu altes Add-on meldet sich nie: nicht auf die Discovery warten (die Meldung liegt schon bereit und wuerde
    den Assistenten sonst weiterfuehren)."""
    _nicht_installiert(supervisor, version="0.36.0")

    result = await _progress(hass, await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}))

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "addon_veraltet")
    assert [(addon.slug, addon.version) for addon in supervisor.installed] == [(BRIDGE_SLUG, "0.36.0")]
    supervisor.client.discovery.list.assert_not_awaited()
    assert server.base_url is None


async def test_addon_error_shows_no_supervisor_text(hass, server, supervisor, caplog):
    _nicht_installiert(supervisor)
    supervisor.errors[("store.install_addon", BRIDGE_SLUG)] = SupervisorError("Supervisor sagt: geheimer-wert")

    result = await _progress(hass, await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}))

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "addon_fehler")
    assert supervisor.installed == []
    assert "AddonError" in caplog.text  # nur die Fehlerart
    assert "geheimer-wert" not in str(result) and "geheimer-wert" not in caplog.text


async def test_manual_start_without_discovery_times_out(hass, server, supervisor, monkeypatch):
    _melden(supervisor, None)
    monkeypatch.setattr(config_flow, "DISCOVERY_WAIT_SECONDS", 0)

    result = await _progress(hass, await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}))

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "keine_discovery")
    assert supervisor.client.discovery.list.await_count >= 1
    assert server.base_url is None


@pytest.mark.parametrize("start", ["discovery", "manuell"])
async def test_two_installed_bridges_never_start_the_assistant(hass, server, supervisor, start):
    """F-ha-host-40: zwei Add-ons passen auf die Heizungsbruecke (gleicher Repository-Slug und genauer Slug). Schon
    die Herkunftspruefung (`async_bridge_slug`) weist das als mehrdeutig zurueck: die Discovery wird verworfen, der
    manuelle Start bricht mit `addon_fehler` ab; geraten wird nicht."""
    supervisor.installed = [installed_addon(BRIDGE_SLUG), installed_addon(BRIDGE_SLUG, version="0.9.0")]

    if start == "discovery":
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=DISCOVERY)
        expected = "discovery_ungueltig"
    else:
        result = await _progress(hass, await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}))
        expected = "addon_fehler"

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, expected)
    assert server.base_url is None and supervisor.client.store.install_addon.await_count == 0


async def test_a_second_bridge_before_the_version_check_aborts_as_ambiguous(hass, server, supervisor):
    """`addon_mehrdeutig` entsteht nur, wenn die zweite Installation nach der Herkunftspruefung der Discovery und vor
    der Pruefung der Mindestversion auftaucht (zwischen den Schritten `hassio` und `bestaetigen`)."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=DISCOVERY)
    assert result["step_id"] == "bestaetigen"
    supervisor.installed.append(installed_addon(BRIDGE_SLUG, version="0.9.0"))

    result = await _configure(hass, result, {})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "addon_mehrdeutig")
    assert server.base_url is None and server.calls == []


# --- Neu konfigurieren ---


async def test_reconfigure_shows_the_menu_after_a_new_login(hass, server):
    entry = await _eingerichtet(hass, server)

    result = await _reconfigure(hass, entry)

    assert result["type"] == FlowResultType.MENU and result["step_id"] == "menue"
    assert result["menu_options"] == ["zuordnung_aendern", "geraet_entfernen"]
    assert ("uebernehmen", GERAET_ID, None) in server.calls


async def test_reconfigure_changes_the_mapping(hass, server):
    entry = await _eingerichtet(hass, server)
    result = await _reconfigure(hass, entry)

    result = await _configure(hass, result, {"next_step_id": "zuordnung_aendern"})
    assert result["step_id"] == "system"
    result = await _configure(hass, result, {"kreis": "mypyllant|e1|SYSTEM|0",
                                             "profil": "vaillant_gastherme_heizkoerper"})
    result = await _configure(hass, result, {"raumfuehler": ["climate.wohnzimmer_thermostat"],
                                             "room_target": "climate.wohnzimmer_thermostat"})
    result = await _configure(hass, result, {"curve": "number.zuhause_circuit_0_heating_curve",
                                             "room_setpoint": "climate.zuhause_zone_1_circuit_0_climate",
                                             "outdoor_temp": "sensor.zuhause_outdoor_temperature"})
    result = await _configure(hass, result, {"raum_soll": 20.0})
    result = await _configure(hass, result, {})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "reconfigure_successful")
    anfrage = next(call[1] for call in server.calls if call[0] == "neu_konfigurieren")
    assert "tenant_id" not in anfrage and anfrage["raumfuehler"] == ["climate.wohnzimmer_thermostat"]
    assert not any(call[0] == "einrichten" for call in server.calls)
    assert entry.data["geraet_id"] == GERAET_ID and entry.data["tenant_id"] == TENANT
    assert server.calls[-1] == ("abmelden",)


async def test_reconfigure_keeps_phones_hints_and_batteries(hass, server):
    entry = await _eingerichtet(hass, server)
    server.geraet_sicht["konfiguration"] = {**KONFIGURATION, "bindung": {
        "benachrichtigung": ["notify.mobile_app_handy", "notify.mobile_app_weg"], "hinweise_aus": ["batterie", "alt"],
        "batterien": ["sensor.wohnzimmer_batterie", "sensor.weg_batterie"]}}
    result = await _reconfigure(hass, entry)
    result = await _configure(hass, result, {"next_step_id": "zuordnung_aendern"})
    result = await _configure(hass, result, {"kreis": "mypyllant|e1|SYSTEM|0",
                                             "profil": "vaillant_gastherme_heizkoerper"})
    result = await _configure(hass, result, {"raumfuehler": ["sensor.wohnzimmer_temperatur"],
                                             "room_target": "sensor.wohnzimmer_temperatur"})
    # nur eine Rolle aendern (Rest bleibt vorbelegt)
    result = await _configure(hass, result, {"curve": "number.zuhause_circuit_0_heating_curve",
                                             "room_setpoint": "climate.zuhause_zone_1_circuit_0_climate",
                                             "outdoor_temp": "sensor.zuhause_outdoor_temperature"})
    assert result["step_id"] == "benachrichtigung"
    defaults = {str(key): key.default() for key in result["data_schema"].schema if hasattr(key, "default")}
    assert defaults["benachrichtigung"] == ["notify.mobile_app_handy"]
    assert defaults["hinweise_aus"] == ["batterie"]
    assert defaults["batterien"] == ["sensor.wohnzimmer_batterie"]

    result = await _configure(hass, result, {"raum_soll": 20.0})
    result = await _configure(hass, result, {})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "reconfigure_successful")
    anfrage = next(call[1] for call in server.calls if call[0] == "neu_konfigurieren")
    assert anfrage["benachrichtigung"] == ["notify.mobile_app_handy"] and anfrage["hinweise_aus"] == ["batterie"]
    assert anfrage["batterien"] == ["sensor.wohnzimmer_batterie"]


async def test_reconfigure_removes_the_device(hass, server):
    entry = await _eingerichtet(hass, server)
    result = await _reconfigure(hass, entry)

    result = await _configure(hass, result, {"next_step_id": "geraet_entfernen"})
    assert result["step_id"] == "geraet_entfernen"
    result = await _configure(hass, result, {})
    await hass.async_block_till_done()

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "entfernt")
    assert ("entfernen",) in server.calls and ("auftrag", "j3") in server.calls
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_failed_removal_has_its_own_text_and_keeps_the_entry(hass, server):
    entry = await _eingerichtet(hass, server)
    server.auftrag_ergebnis = {"zustand": "fehlgeschlagen", "fehler": "Gerät ist gesperrt."}
    result = await _reconfigure(hass, entry)

    result = await _configure(hass, await _configure(hass, result, {"next_step_id": "geraet_entfernen"}), {})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "entfernen_fehler")
    assert result["description_placeholders"]["grund"] == "Gerät ist gesperrt."
    assert hass.config_entries.async_entries(DOMAIN) == [entry]


async def test_ein_entfernen_ohne_rueckmeldung_entfernt_den_eintrag_trotzdem(hass, server, supervisor, monkeypatch):
    """Antwortet das Geraet in den 300 s nicht, ist der Eintrag trotzdem entfernt (wie bei „entfernt“): das
    Loeschen meldet das Geraet lokal ab (stdin-Zeile), der Server gibt es frei, sobald es erreichbar ist."""
    entry = await _eingerichtet(hass, server)
    server.auftrag_ergebnis = {"zustand": "wartet_bestaetigung", "fehler": None}
    monkeypatch.setattr(config_flow, "ENTFERNEN_WAIT_SECONDS", 0)
    result = await _reconfigure(hass, entry)

    result = await _configure(hass, await _configure(hass, result, {"next_step_id": "geraet_entfernen"}), {})
    await hass.async_block_till_done()

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "entfernen_laeuft")
    assert hass.config_entries.async_entries(DOMAIN) == []
    assert [eintrag for eintrag in supervisor.stdin if eintrag[1].startswith(b"entfernen")] == [
        (BRIDGE_SLUG, f"entfernen {GERAET_ID}\n\n".encode())]
    assert server.calls[-1] == ("abmelden",)


@pytest.mark.parametrize("data", [{"tenant_id": TENANT}, {"geraet_id": None, "tenant_id": TENANT},
                                  {"geraet_id": "", "tenant_id": TENANT}, {"geraet_id": 7, "tenant_id": TENANT},
                                  {"geraet_id": "shg-aaaaaaaaaaaaaaa1", "tenant_id": TENANT}],
                         ids=["fehlt", "null", "leer", "zahl", "falsche_form"])
async def test_reconfigure_without_a_valid_device_id_aborts_before_login(hass, server, caplog, data):
    """Ein Eintrag ohne gueltige Geraete-ID (etwa VERSION 2, MIGRATION_ERROR) bricht mit `unknown` ab
    statt mit einem KeyError; es entsteht kein Client und keine Anfrage."""
    entry = MockConfigEntry(domain=DOMAIN, data=data, version=config_flow.SmartHeatConfigFlow.VERSION, title=TENANT)
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "unknown")
    assert server.base_url is None and server.calls == []
    assert "ohne gueltige Geraete-ID" in caplog.text
    assert hass.config_entries.async_entries(DOMAIN) == [entry]


async def test_there_is_no_options_dialog(hass, server):
    entry = await _eingerichtet(hass, server)

    assert not entry.supports_options


# --- Texte ---

_ABORTS = {
    "nachweis_fehlt", "nachweis_abgelehnt", "code_abgelehnt", "server_veraltet", "portal_bestaetigung",
    "kein_supervisor", "addon_fehler", "addon_veraltet", "addon_mehrdeutig", "bootstrap_url_ungueltig",
    "repository_mehrdeutig", "fremde_heizungsbruecke",
    "keine_discovery", "discovery_ungueltig", "geraet_nicht_gemeldet", "keine_anlage", "inventur_fehler", "keine_integration",
    "nicht_unterstuetzt", "rolle_ohne_kandidaten", "einrichten_fehler", "einrichten_zeit", "keine_konfiguration",
    "entfernen_fehler", "entfernen_laeuft", "entfernt", "cannot_connect", "abgelehnt", "geraet_unbekannt",
    "unknown", "already_configured", "already_in_progress", "single_instance_allowed",
    "reconfigure_successful",
}
_ERRORS = {
    "uebernahme_gesperrt", "uebernahme_fehlgeschlagen", "anmeldung_gebunden", "cannot_connect", "unknown",
    "sitzung_abgelaufen", "erneut_anmelden", "code_abgelaufen", "required", "wrong_domain", "entity_not_found", "entity_unavailable", "not_numeric",
    "unit_mismatch", "out_of_range", "duplicate_entity", "zone_is_room_target", "entity_wrong_integration",
    "entity_wrong_installation", "room_sensors_required", "wrong_state_class", "unknown_service", "unknown_hint",
    "zu_viele",
}
_STEPS = {"bestaetigen", "anmelden", "schon_eingerichtet", "anlage", "ersetzen", "inventur_zeit", "integration",
          "system", "hebelsatz", "raeume", "anlagenwerte", "benachrichtigung", "zusammenfassung", "menue",
          "geraet_entfernen"}


@pytest.mark.parametrize("path", ["strings.json", "translations/en.json", "translations/de.json"])
def test_every_step_error_and_abort_has_a_text(path):
    data = json.loads((_COMPONENT / path).read_text(encoding="utf-8"))
    config = data["config"]

    assert set(config["abort"]) == _ABORTS
    assert set(config["error"]) == _ERRORS
    assert set(config["step"]) == _STEPS
    assert set(config["progress"]) == {"addon_starten", "anfordern", "warten", "warten_eigener_server", "inventur",
                                       "einrichten", "entfernen"}
    for key in ("warten", "warten_eigener_server"):
        for platzhalter in ("{anmeldecode}", "{link}", "{link_komplett}"):
            assert platzhalter in config["progress"][key], (path, key, platzhalter)
    assert "{server}" in config["progress"]["warten_eigener_server"] and "{server}" not in config["progress"]["warten"]
    assert set(config["step"]["menue"]["menu_options"]) == {"zuordnung_aendern", "geraet_entfernen"}
    assert "options" not in data
    for key in ("inventur_fehler", "keine_integration", "einrichten_fehler", "entfernen_fehler", "abgelehnt"):
        assert "{grund}" in config["abort"][key], (path, key)
    for key in ("uebernahme_fehlgeschlagen", "uebernahme_gesperrt", "anmeldung_gebunden"):
        assert "{grund}" in config["error"][key], (path, key)


@pytest.mark.parametrize("path", ["strings.json", "translations/en.json", "translations/de.json"])
def test_neue_texte_des_assistenten_sind_in_allen_sprachen_da(path):
    """Abbruchtext beim unerreichbaren Geraet (Eintrag ist entfernt), Hinweis „Geraet nicht erreichbar“,
    Feldfehler `zu_viele`."""
    data = json.loads((_COMPONENT / path).read_text(encoding="utf-8"))
    deutsch = path.endswith("de.json")

    assert ("Der Eintrag ist entfernt; der SmartHeat-Dienst gibt das Gerät frei, sobald es erreichbar ist."
            if deutsch else "The entry has been removed; the SmartHeat service releases the device as soon as it "
            "can be reached.") == data["config"]["abort"]["entfernen_laeuft"]
    assert data["common"]["geraet_nicht_erreichbar"] == (
        "Das Gerät ist nicht erreichbar." if deutsch else "The device cannot be reached.")
    assert data["config"]["error"]["zu_viele"]


def test_both_languages_label_the_same_roles():
    """Dass jede Rolle des Vertrags (HA_ROLLEN ohne room_target) einen Text hat, prueft ein Contract-Check (Vertrag V1,
    Liste: tools/contract_check.py --liste) direkt gegen den Server; die Integration fuehrt keine Kopie der Rollen."""
    labels = [set(json.loads((_COMPONENT / path).read_text(encoding="utf-8"))["config"]["step"]["anlagenwerte"]["data"])
              for path in ("translations/en.json", "translations/de.json")]
    assert labels[0] == labels[1] and "curve" in labels[0]


def test_strings_json_equals_the_english_translation():
    assert json.loads((_COMPONENT / "strings.json").read_text()) == json.loads(
        (_COMPONENT / "translations/en.json").read_text())


def test_translations_have_the_same_keys():
    def keys(node, prefix=""):
        if not isinstance(node, dict):
            return {prefix}
        return set().union(*(keys(value, f"{prefix}.{key}") for key, value in node.items()))

    english = json.loads((_COMPONENT / "translations/en.json").read_text())
    german = json.loads((_COMPONENT / "translations/de.json").read_text())

    assert keys(english) == keys(german)
