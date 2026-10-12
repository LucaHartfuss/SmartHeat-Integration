"""Spec 5c 2.1: Assistent ueber der Einrichtungs-API (Discovery, manuell, Ersetzen, eingerichtetes Geraet,
Neu konfigurieren, Fehler). Die Einrichtungs-API ersetzt FakeServer (einrichtung_fakes.py), die Add-on-Seite die
Fixture `server` (Discovery, bootstrap_url, Mindestversion)."""
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.hassio import AddonError
from homeassistant.config_entries import SOURCE_HASSIO, SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from custom_components.smartheat import config_flow
from custom_components.smartheat.const import ADDON_REPOSITORY_URL, DOMAIN
from custom_components.smartheat.einrichtung_client import ApiError, InvalidResponse, NotFound, TooMany
from custom_components.smartheat.supervisor_client import AddonOutdatedError

from .einrichtung_fakes import (
    CLAIM_CODE,
    GERAET_ID,
    KONFIGURATION,
    PASSWORT,
    ROLLEN,
    TENANT,
    TOKEN,
    ZUORDNUNG,
    CannotConnect,
    Conflict,
    FakeServer,
    InvalidAuth,
    Rejected,
    TokenUngueltig,
)

DISCOVERY = HassioServiceInfo(config={"geraet_id": GERAET_ID, "zustand": "nicht_uebernommen",
                                      "claim_code": CLAIM_CODE},
                              name="Heizungsbruecke", slug="abc_heizungsbruecke", uuid="u1")
LOGIN = {"email": "a@b.de", "password": PASSWORT}
BRUECKE = SimpleNamespace(slug="abc_heizungsbruecke", repository="abc", url=ADDON_REPOSITORY_URL, version="1.0.0")
_COMPONENT = Path(__file__).parents[1] / "custom_components" / "smartheat"


@pytest.fixture
def server(monkeypatch):
    fake = FakeServer()
    original = config_flow.EinrichtungClient.__init__

    def init(self, session, base_url):
        original(self, session, base_url)
        fake.base_url = base_url
        fake.bind(self)

    monkeypatch.setattr(config_flow.EinrichtungClient, "__init__", init)

    async def bootstrap_url(hass):
        return "https://labor.example.test"

    async def discovery(hass):
        return fake.discovery

    async def resolve(hass, repository_url, min_versions):
        fake.calls.append(("addon_version", dict(min_versions)))
        if fake.addon_error is not None:
            raise fake.addon_error
        return {}

    def supervisor(hass):
        return SimpleNamespace(addons=SimpleNamespace(list=AsyncMock(side_effect=lambda: list(fake.addons))),
                               store=SimpleNamespace(repositories_list=AsyncMock(return_value=[SimpleNamespace(
                                   slug="abc", source=ADDON_REPOSITORY_URL, url=ADDON_REPOSITORY_URL)])))

    fake.discovery = {"geraet_id": GERAET_ID, "zustand": "nicht_uebernommen", "claim_code": CLAIM_CODE}
    fake.addons = [BRUECKE]
    fake.addon_error = None
    monkeypatch.setattr(config_flow.addon_control, "get_supervisor_client", supervisor)
    monkeypatch.setattr(config_flow.supervisor_client, "get_supervisor_client", supervisor)
    monkeypatch.setattr(config_flow.addon_control, "async_bootstrap_url", bootstrap_url)
    monkeypatch.setattr(config_flow.addon_control, "async_discovery", discovery)
    monkeypatch.setattr(config_flow.supervisor_client, "async_resolve_addons", resolve)
    monkeypatch.setattr(config_flow, "POLL_SECONDS", 0)
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
    result = await _configure(hass, result, {})
    assert result["step_id"] == "anmelden"
    return await _configure(hass, result, LOGIN)


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
    assert result["step_id"] == "anmelden"
    return await _configure(hass, result, LOGIN)


# --- Einrichten ---


async def test_discovery_flow_creates_the_entry(hass, server):
    result = await _configure(hass, await _bis_zusammenfassung(hass), {})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == TENANT
    assert result["result"].unique_id == GERAET_ID and result["result"].version == config_flow.SmartHeatConfigFlow.VERSION
    assert result["data"] == {"geraet_id": GERAET_ID, "tenant_id": TENANT, "profile_id": KONFIGURATION["profil"],
                              "lever_set": "vaillant_vrc720", "hebel": KONFIGURATION["hebel"],
                              "shift_lever": "room_setpoint"}
    assert server.base_url == "https://labor.example.test"
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
    assert ("addon_version", {"heizungsbruecke": "1.0.0"}) in server.calls
    assert server.calls[-1] == ("abmelden",)


async def test_no_token_password_or_code_is_stored_or_logged(hass, server, caplog):
    caplog.set_level(logging.DEBUG)

    await _configure(hass, await _bis_zusammenfassung(hass), {})

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    stored = str(entry.data) + str(entry.options)
    for secret in (TOKEN, PASSWORT, CLAIM_CODE):
        assert secret not in stored and secret not in caplog.text, secret


async def test_battery_of_the_chosen_thermostat_is_preselected(hass, server):
    result = await _bis_benachrichtigung(hass)

    schema = result["data_schema"].schema
    default = next(key for key in schema if key == "batterien").default()
    assert default == ["sensor.wohnzimmer_batterie"]


async def test_wrong_password_stays_on_the_form(hass, server):
    server.fehler["anmelden"] = InvalidAuth("Ungueltige Zugangsdaten", 401)

    result = await _bis_anlage(hass)

    assert result["step_id"] == "anmelden" and result["errors"] == {"base": "invalid_auth"}
    assert not any(call[0] == "uebernehmen" for call in server.calls)


@pytest.mark.parametrize(("route", "error", "key", "grund"), [
    ("anmelden", CannotConnect(), "cannot_connect", "-"),
    ("anmelden", TooMany("Zu viele Versuche", 429), "too_many", "-"),
    ("anmelden", InvalidResponse("anmelden: kein JSON"), "unknown", "-"),
    ("anmelden", NotFound("", 404), "server_veraltet", "-"),
    ("anmelden", ApiError("", 405), "server_veraltet", "-"),
    ("anmelden", ApiError("", 307), "unknown", "-"),
    ("uebernehmen", Rejected("Code oder Gerät unbekannt."), "uebernahme_fehlgeschlagen", "Code oder Gerät unbekannt."),
    ("uebernehmen", Conflict("Diese Anmeldung gehört schon zu einem anderen Gerät.", 409), "anmeldung_gebunden",
     "Diese Anmeldung gehört schon zu einem anderen Gerät."),
    ("uebernehmen", TooMany("Zu viele Fehlversuche, bitte 15 Minuten warten.", 429), "uebernahme_gesperrt",
     "Zu viele Fehlversuche, bitte 15 Minuten warten."),
])
async def test_login_and_claim_errors_stay_on_the_form_and_log_out(hass, server, route, error, key, grund):
    server.fehler[route] = error

    result = await _bis_anlage(hass)

    assert result["step_id"] == "anmelden" and result["errors"] == {"base": key}
    assert result["description_placeholders"]["grund"] == grund
    if route == "uebernehmen":
        assert server.calls[-1] == ("abmelden",)


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


async def test_already_set_up_device_only_creates_the_entry(hass, server):
    server.geraet_sicht.update(eingerichtet=True, tenant_id=TENANT, konfiguration=KONFIGURATION)
    server.discovery = {"geraet_id": GERAET_ID, "zustand": "regelt"}

    result = await _bis_anlage(hass, HassioServiceInfo(config={"geraet_id": GERAET_ID, "zustand": "regelt"}, name="x",
                                                       slug="abc_heizungsbruecke", uuid="u2"))
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


@pytest.mark.parametrize("config", [{}, {"geraet_id": 7}, {"geraet_id": ""}, {"geraet_id": "shg-aaaaaaaaaaaaaaa1"},
                                    {"geraet_id": "SHG-AAAAAAAAAAAAAAAA"}, {"geraet_id": f"{GERAET_ID} [x](y)"}])
async def test_invalid_discovery_aborts(hass, server, config):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=HassioServiceInfo(
        config=config, name="x", slug="abc_heizungsbruecke", uuid="u3"))

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "discovery_ungueltig")


@pytest.mark.parametrize("addons", [
    [BRUECKE],  # die Discovery stammt von einem anderen Add-on (slug passt nicht)
    [SimpleNamespace(slug="xyz_heizungsbruecke", repository="xyz", url="https://github.com/fremd/addons",
                     version="9.9.9")],
    # fremdes Repository, das sich mit unserer url und einem *_heizungsbruecke als Heizungsbruecke ausgibt
    [SimpleNamespace(slug="xyz_heizungsbruecke", repository="xyz", url=ADDON_REPOSITORY_URL, version="1.0.0")],
    [],
])
async def test_discovery_of_a_foreign_addon_aborts(hass, server, addons):
    server.addons = addons
    foreign = HassioServiceInfo(config=DISCOVERY.config, name="x", slug="xyz_heizungsbruecke", uuid="u5")

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=foreign)

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "discovery_ungueltig")


async def test_local_addon_imitating_the_bridge_is_rejected(hass, server):
    server.addons = [SimpleNamespace(slug="local_heizungsbruecke", repository="local", url=ADDON_REPOSITORY_URL,
                                     version="1.0.0")]
    local = HassioServiceInfo(config=DISCOVERY.config, name="x", slug="local_heizungsbruecke", uuid="u6")

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=local)

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "discovery_ungueltig")


@pytest.mark.parametrize("discovery", [None, {"geraet_id": "shg-bbbbbbbbbbbbbbbb", "zustand": "nicht_uebernommen",
                                               "claim_code": "ANDERERCODE1"}])
async def test_claim_without_a_trusted_discovery_of_this_device_aborts(hass, server, discovery):
    """Nie auf Geraete-ID und Code der ersten Meldung zurueckfallen."""
    server.discovery = discovery

    result = await _bis_anlage(hass)

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "geraet_nicht_gemeldet")
    assert not any(call[0] == "uebernehmen" for call in server.calls)
    assert server.calls[-1] == ("abmelden",)


async def test_claim_uses_the_current_discovery_code(hass, server):
    """Die erste Discovery kann `startet` ohne Code sein; vor dem Uebernehmen liest der Assistent sie neu."""
    server.discovery = {"geraet_id": GERAET_ID, "zustand": "nicht_uebernommen", "claim_code": "NEUERCODE123"}

    await _bis_anlage(hass, HassioServiceInfo(config={"geraet_id": GERAET_ID, "zustand": "startet"}, name="x",
                                              slug="abc_heizungsbruecke", uuid="u4"))

    assert ("uebernehmen", GERAET_ID, "NEUERCODE123") in server.calls


async def test_outdated_addon_aborts_before_login(hass, server):
    server.addon_error = AddonOutdatedError("heizungsbruecke", "0.36.0", "1.0.0")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=DISCOVERY)

    result = await _configure(hass, result, {})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "addon_veraltet")
    assert not any(call[0] == "anmelden" for call in server.calls)


async def test_invalid_bootstrap_url_aborts(hass, server, monkeypatch):
    async def bootstrap_url(hass_):
        return None

    monkeypatch.setattr(config_flow.addon_control, "async_bootstrap_url", bootstrap_url)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=DISCOVERY)

    result = await _configure(hass, result, {})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "bootstrap_url_ungueltig")


async def test_replace_needs_confirmation_and_uses_the_old_mapping(hass, server):
    server.geraet_sicht["anlagen"] = [{"tenant_id": TENANT, "aktiv": True, "belegt": True, "ersetzbar": True,
                                       "altpfad": False}]
    result = await _bis_anlage(hass)
    result = await _configure(hass, result, {"anlage": TENANT})
    assert result["step_id"] == "ersetzen"

    result = await _configure(hass, result, {})
    assert result["step_id"] == "system"
    await _configure(hass, result, {"kreis": "mypyllant|e1|SYSTEM|0", "profil": "vaillant_gastherme_heizkoerper"})

    assert ("zuordnung", "mypyllant|e1|SYSTEM|0", "vaillant_gastherme_heizkoerper", "vaillant_vrc720", TENANT) \
        in server.calls


async def test_replace_sends_the_replace_flag(hass, server):
    server.geraet_sicht["anlagen"] = [{"tenant_id": TENANT, "aktiv": True, "belegt": True, "ersetzbar": True,
                                       "altpfad": False}]
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
    {"tenant_id": TENANT, "aktiv": True, "belegt": True, "ersetzbar": False, "altpfad": True},
    {"tenant_id": TENANT, "aktiv": False, "belegt": False, "ersetzbar": False, "altpfad": False},
    {"tenant_id": TENANT, "aktiv": True, "belegt": True, "ersetzbar": False, "altpfad": False},
])
async def test_tenant_that_cannot_be_used_is_not_offered(hass, server, anlage):
    server.geraet_sicht["anlagen"] = [anlage]

    result = await _bis_anlage(hass)

    assert result["type"] == FlowResultType.ABORT and result["reason"] == "keine_anlage"
    assert server.calls[-1] == ("abmelden",)


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
    result = await _configure(hass, result, LOGIN)
    assert result["step_id"] == "anlage"


async def test_required_role_without_candidates_aborts_with_a_hint(hass, server):
    hass.config.language = "de"
    server.zuordnung_antwort = {**ZUORDNUNG, "rollen": [*ROLLEN[:2], {**ROLLEN[2], "vorschlag": None,
                                                                       "kandidaten": []}]}

    result = await _configure(hass, await _bis_raeume_vorher(hass), {
        "kreis": "mypyllant|e1|SYSTEM|0", "profil": "vaillant_gastherme_heizkoerper"})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "rolle_ohne_kandidaten")
    assert result["description_placeholders"]["rolle"] == "Außentemperatur"


async def test_closing_the_dialog_logs_out(hass, server):
    result = await _bis_anlage(hass)
    assert result["step_id"] == "anlage"

    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done()

    assert server.calls[-1] == ("abmelden",)


# --- Manueller Start ---


async def test_manual_start_installs_the_addon_and_waits_for_discovery(hass, server, monkeypatch):
    installed = []

    async def install(hass_):
        installed.append(True)

    answers = [None, {"geraet_id": GERAET_ID, "zustand": "startet"}]

    async def discovery(hass_):
        return answers.pop(0) if answers else {"geraet_id": GERAET_ID, "zustand": "nicht_uebernommen",
                                               "claim_code": CLAIM_CODE}

    monkeypatch.setattr(config_flow.addon_control, "async_install_and_start", install)
    monkeypatch.setattr(config_flow.addon_control, "async_discovery", discovery)
    monkeypatch.setattr(config_flow, "is_hassio", lambda hass_: True)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await _progress(hass, result)
    assert installed and result["step_id"] == "anmelden"

    result = await _configure(hass, result, LOGIN)

    assert result["step_id"] == "anlage"
    assert ("uebernehmen", GERAET_ID, CLAIM_CODE) in server.calls


async def test_manual_start_without_supervisor_aborts(hass, server):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "kein_supervisor")


async def test_manual_start_with_an_outdated_addon_does_not_wait(hass, server, monkeypatch):
    async def install(hass_):
        return None

    async def discovery(hass_):
        raise AssertionError("ein zu altes Add-on meldet sich nie; nicht warten")

    server.addon_error = AddonOutdatedError("heizungsbruecke", "0.36.0", "1.0.0")
    monkeypatch.setattr(config_flow.addon_control, "async_install_and_start", install)
    monkeypatch.setattr(config_flow.addon_control, "async_discovery", discovery)
    monkeypatch.setattr(config_flow, "is_hassio", lambda hass_: True)

    result = await _progress(hass, await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}))

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "addon_veraltet")


async def test_addon_error_shows_no_supervisor_text(hass, server, monkeypatch, caplog):
    async def install(hass_):
        raise AddonError("Supervisor sagt: geheimer-wert")

    monkeypatch.setattr(config_flow.addon_control, "async_install_and_start", install)
    monkeypatch.setattr(config_flow, "is_hassio", lambda hass_: True)

    result = await _progress(hass, await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}))

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "addon_fehler")
    assert "geheimer-wert" not in str(result) and "geheimer-wert" not in caplog.text


async def test_manual_start_without_discovery_times_out(hass, server, monkeypatch):
    async def install(hass_):
        return None

    server.discovery = None

    monkeypatch.setattr(config_flow.addon_control, "async_install_and_start", install)
    monkeypatch.setattr(config_flow, "is_hassio", lambda hass_: True)
    monkeypatch.setattr(config_flow, "DISCOVERY_WAIT_SECONDS", 0)

    result = await _progress(hass, await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}))

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "keine_discovery")


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


async def test_removal_of_an_offline_device_keeps_running(hass, server, monkeypatch):
    entry = await _eingerichtet(hass, server)
    server.auftrag_ergebnis = {"zustand": "wartet_bestaetigung", "fehler": None}
    monkeypatch.setattr(config_flow, "ENTFERNEN_WAIT_SECONDS", 0)
    result = await _reconfigure(hass, entry)

    result = await _configure(hass, await _configure(hass, result, {"next_step_id": "geraet_entfernen"}), {})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "entfernen_laeuft")
    assert hass.config_entries.async_entries(DOMAIN) == [entry]


async def test_reconfigure_of_an_old_entry_aborts_with_a_text(hass, server):
    from .addon_fakes import make_entry

    entry = make_entry(hass, version=config_flow.SmartHeatConfigFlow.VERSION)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "alter_eintrag")


async def test_there_is_no_options_dialog(hass, server):
    entry = await _eingerichtet(hass, server)

    assert not entry.supports_options


# --- Texte ---

_ABORTS = {
    "kein_supervisor", "addon_fehler", "addon_veraltet", "addon_mehrdeutig", "bootstrap_url_ungueltig",
    "keine_discovery", "discovery_ungueltig", "geraet_nicht_gemeldet", "keine_anlage", "inventur_fehler", "keine_integration",
    "nicht_unterstuetzt", "rolle_ohne_kandidaten", "einrichten_fehler", "einrichten_zeit", "keine_konfiguration",
    "entfernen_fehler", "entfernen_laeuft", "entfernt", "cannot_connect", "abgelehnt", "geraet_unbekannt",
    "alter_eintrag", "unknown", "already_configured", "already_in_progress", "single_instance_allowed",
    "reconfigure_successful",
}
_ERRORS = {
    "invalid_auth", "too_many", "uebernahme_gesperrt", "uebernahme_fehlgeschlagen", "anmeldung_gebunden", "cannot_connect", "unknown",
    "server_veraltet", "sitzung_abgelaufen", "required", "wrong_domain", "entity_not_found", "entity_unavailable", "not_numeric",
    "unit_mismatch", "out_of_range", "duplicate_entity", "zone_is_room_target", "entity_wrong_integration",
    "entity_wrong_installation", "room_sensors_required", "wrong_state_class", "unknown_service", "unknown_hint",
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
    assert set(config["progress"]) == {"addon_starten", "inventur", "einrichten", "entfernen"}
    assert set(config["step"]["menue"]["menu_options"]) == {"zuordnung_aendern", "geraet_entfernen"}
    assert "options" not in data
    for key in ("inventur_fehler", "keine_integration", "einrichten_fehler", "entfernen_fehler", "abgelehnt"):
        assert "{grund}" in config["abort"][key], (path, key)
    for key in ("uebernahme_fehlgeschlagen", "uebernahme_gesperrt", "anmeldung_gebunden"):
        assert "{grund}" in config["error"][key], (path, key)


def test_every_role_has_a_label():
    from custom_components.smartheat.const import ROLLEN_ANLAGENWERTE

    for path in ("translations/en.json", "translations/de.json"):
        labels = json.loads((_COMPONENT / path).read_text(encoding="utf-8"))["config"]["step"]["anlagenwerte"]["data"]
        assert set(labels) == set(ROLLEN_ANLAGENWERTE), path


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
