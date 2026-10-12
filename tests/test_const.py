"""Spec 5c: Konstanten der schlanken Integration (Ereignis Schema 3, Discovery, Routen, Bedienbereich)."""
from custom_components.smartheat import const


def test_status_event_schema_3_keeps_the_runtime_fields():
    assert const.STATUS_EVENT_SCHEMA == 3
    assert const.STATUS_EVENT_FIELDS[:16] == (
        "schema", "tenant_id", "setup_id", "addon_version", "status", "grund", "notbetrieb", "datenfehler",
        "boost", "letzte_serverantwort", "hebelsatz", "hebel", "gelernt", "abo", "abo_frist_ende", "hinweise")
    assert const.STATUS_EVENT_FIELDS[16:] == (
        "geraet_id", "geraet_zustand", "server_verbunden", "raum_soll", "geraet_hinweise")


def test_device_contract_values():
    assert (const.DISCOVERY_SERVICE, const.DISCOVERY_FIELDS) == ("smartheat", ("geraet_id", "zustand", "claim_code"))
    assert const.WUNSCH_EVENT == "smartheat_wunsch"
    assert (const.RAUM_SOLL_MIN, const.RAUM_SOLL_MAX, const.RAUM_SOLL_STEP) == (15.0, 25.0, 0.5)
    assert const.MIN_ADDON_VERSIONS == {"heizungsbruecke": "1.0.0"}
    assert const.EINRICHTUNG_ROUTEN["anmelden"] == ("POST", "/anmelden")
    assert const.EINRICHTUNG_ROUTEN["auftrag"] == ("GET", "/geraet/auftraege/<job_id>")


def test_device_states_and_hint_keys_follow_the_add_on_wire():
    assert const.HINWEIS_FIELDS == ("key", "stufe", "text")
    assert const.GERAET_ZUSTAENDE == (
        "startet", "nicht_uebernommen", "keine_verbindung", "wartet_auf_einrichtung", "regelt", "stoerung", "gesperrt",
        "update_noetig")
    assert set(const.HINWEIS_KEYS) == {
        "wunsch_begrenzt", "soll_quelle_aus", "geraet_doppelt", "update_noetig", "gesperrt", "konfiguration_abgelehnt"}
    assert const.hinweis_issue_id("Update-Noetig") == "hinweis_update_noetig"


def test_setup_api_routes_match_the_server():
    assert const.EINRICHTUNG_PREFIX == "/einrichtung/v1"
    assert const.EINRICHTUNG_ROUTEN == {
        "anmelden": ("POST", "/anmelden"),
        "abmelden": ("POST", "/abmelden"),
        "uebernehmen": ("POST", "/geraete/uebernehmen"),
        "geraet": ("GET", "/geraet"),
        "inventur": ("POST", "/geraet/inventur"),
        "vorschlag": ("GET", "/geraet/vorschlag"),
        "zuordnung": ("POST", "/geraet/zuordnung"),
        "pruefen": ("POST", "/geraet/pruefen"),
        "einrichten": ("POST", "/geraet/einrichten"),
        "neu_konfigurieren": ("POST", "/geraet/neu-konfigurieren"),
        "entfernen": ("POST", "/geraet/entfernen"),
        "auftrag": ("GET", "/geraet/auftraege/<job_id>"),
    }


def test_addon_and_wait_constants():
    assert const.DEFAULT_BASE_URL == "https://accounts.hartfussha.org"
    assert const.OPTION_BOOTSTRAP_URL == "bootstrap_url"
    assert const.ADDON_SPECS == [("Heizungsbruecke", "heizungsbruecke")]
    assert const.ADDON_DISPLAY_NAMES == {"heizungsbruecke": "Heizungsbrücke"}
    assert (const.DISCOVERY_WAIT_SECONDS, const.INVENTUR_WAIT_SECONDS, const.AUFTRAG_WAIT_SECONDS,
            const.ENTFERNEN_WAIT_SECONDS, const.POLL_SECONDS) == (300, 120, 900, 300, 2)


def test_entity_ids_are_unchanged_for_client1():
    assert const.entity_id("sensor", "client1", "status") == "sensor.smartheat_client1_status"
    assert const.entity_id("number", "client1", "wunschtemperatur") == "number.smartheat_client1_wunschtemperatur"
    assert const.entity_id("binary_sensor", "Smoke-Test 01", "notbetrieb") == "binary_sensor.smartheat_smoke_test_01_notbetrieb"
    assert const.watchdog_notification_id("client1") == "smartheat_client1_addon"
