"""Konstanten der schlanken Integration (Ereignis Schema 4, Discovery, Routen, Bedienbereich)."""
import pytest

from custom_components.smartheat import const


def test_status_event_schema_4_separates_the_plant_from_the_circuits():
    assert const.STATUS_EVENT_SCHEMA == 4
    assert const.STATUS_EVENT_FIELDS == (
        "schema", "tenant_id", "addon_version", "status", "grund", "notbetrieb", "datenfehler", "letzte_serverantwort",
        "hinweise", "geraet_id", "geraet_zustand", "server_verbunden", "geraet_hinweise", "anlage_name",
        "konfig_version", "mindest_integration", "benachrichtigung", "hinweise_aus", "kreise")
    assert const.STATUS_KREIS_FIELDS == (
        "kreis", "name", "raum_soll", "raum_ist", "einlernphase_tag", "testphase_bis", "befunde", "boost",
        "hebelsatz", "hebel", "gelernt", "abo", "abo_frist_ende", "zustand", "kaelteschutz")
    assert set(const.KREIS_ZUSTAND_VALUES) >= {"regelt", "abo_inaktiv", "abo_beendet", "testphase", "ausser_betrieb", "heizt_auf"}
    assert const.KAELTESCHUTZ_VALUES == ("aus", "soll", "frost")


def test_device_contract_values():
    assert (const.DISCOVERY_SERVICE, const.DISCOVERY_FIELDS) == ("smartheat", ("geraet_id", "zustand", "claim_code"))
    assert const.WUNSCH_EVENT == "smartheat_wunsch"
    assert const.WUNSCH_EVENT_SCHEMA == 4
    assert (const.RAUM_SOLL_MIN, const.RAUM_SOLL_MAX, const.RAUM_SOLL_STEP) == (15.0, 25.0, 0.5)
    assert const.MIN_ADDON_VERSIONS == {"heizungsbruecke": "1.0.0"}
    assert "anmelden" not in const.EINRICHTUNG_ROUTEN
    assert const.EINRICHTUNG_ROUTEN["geraetecode"] == ("POST", "/geraetecode")
    assert const.EINRICHTUNG_ROUTEN["auftrag"] == ("GET", "/geraet/auftraege/<job_id>")


def test_hint_fields_and_keys_follow_the_add_on_wire():
    assert const.HINWEIS_FIELDS == ("key", "stufe", "text")
    assert set(const.HINWEIS_KEYS) == {
        "wunsch_begrenzt", "soll_quelle_aus", "geraet_doppelt", "update_noetig", "gesperrt", "konfiguration_abgelehnt"}
    assert const.hinweis_issue_id("Update-Noetig") == "hinweis_update_noetig"


def test_setup_api_routes_match_the_server():
    assert const.EINRICHTUNG_PREFIX == "/einrichtung/v1"
    assert const.EINRICHTUNG_ROUTEN == {
        "geraetecode": ("POST", "/geraetecode"),
        "geraetecode_token": ("POST", "/geraetecode/token"),
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


def test_geraetecode_fields_follow_the_server():
    assert const.EINRICHTUNG_ANFRAGE_FELDER["geraetecode"] == ((), ())
    assert const.EINRICHTUNG_ANFRAGE_FELDER["geraetecode_token"] == (("geraetecode",), ())
    assert const.EINRICHTUNG_ANTWORT_FELDER["geraetecode"] == (
        "geraetecode", "anmeldecode", "link", "link_komplett", "gueltig_s", "abfrage_s")
    assert const.EINRICHTUNG_ANTWORT_FELDER["geraetecode_token"] == ("zustand", "abfrage_s", "token", "gueltig_bis")
    assert "anmelden" not in const.EINRICHTUNG_ANFRAGE_FELDER and "anmelden" not in const.EINRICHTUNG_ANTWORT_FELDER
    assert (const.GERAETECODE_ZUSTAND_WARTET, const.GERAETECODE_ZUSTAND_BESTAETIGT, const.GERAETECODE_ZUSTAND_ABGELEHNT,
            const.GERAETECODE_ZUSTAND_ABGELAUFEN, const.GERAETECODE_ZUSTAND_LANGSAMER) == (
        "wartet", "bestaetigt", "abgelehnt", "abgelaufen", "langsamer")
    assert (const.ZUSTAND_ERNEUT_ANMELDEN, const.ZUSTAND_PORTAL_BESTAETIGUNG) == ("erneut_anmelden", "portal_bestaetigung")
    assert (const.NACHWEIS_KOPF_GERAET, const.NACHWEIS_KOPF_ZEITSTEMPEL, const.NACHWEIS_KOPF_SIGNATUR) == (
        "X-SHG-Device", "X-SHG-Timestamp", "X-SHG-Signature")


def test_nachweis_contract_with_the_addon():
    assert (const.STDIN_BEFEHL_NACHWEIS, const.STDIN_BEFEHL_ENTFERNEN) == ("nachweis", "entfernen")
    assert const.STDIN_ZEILE_FORMAT.format(befehl=const.STDIN_BEFEHL_NACHWEIS, geraet_id="shg-a") == "nachweis shg-a\n"
    assert const.NACHWEIS_EVENT == "smartheat_nachweis"
    assert const.NACHWEIS_EVENT_FIELDS == ("schema", "geraet_id", "zeitstempel", "signatur")
    assert const.NACHWEIS_EVENT_SCHEMA == 1


def test_addon_and_wait_constants():
    assert const.DEFAULT_BASE_URL == "https://accounts.setwell.de"
    assert const.OPTION_BOOTSTRAP_URL == "bootstrap_url"
    assert (const.DISCOVERY_WAIT_SECONDS, const.INVENTUR_WAIT_SECONDS, const.AUFTRAG_WAIT_SECONDS,
            const.ENTFERNEN_WAIT_SECONDS, const.POLL_SECONDS) == (300, 120, 900, 300, 2)


def test_entity_ids_of_the_installation_and_of_the_circuits():
    assert const.entity_id("sensor", "status") == "sensor.smartheat_status"
    assert const.entity_id("binary_sensor", "notbetrieb") == "binary_sensor.smartheat_notbetrieb"
    assert const.entity_id("number", "wunschtemperatur", kreis=1) == "number.smartheat_k1_wunschtemperatur"
    assert const.entity_id("sensor", "zustand", kreis=4) == "sensor.smartheat_k4_zustand"
    assert const.unique_id("client1", "status") == "client1_status"
    assert const.unique_id("client1", "zustand", kreis=2) == "client1_k2_zustand"


def test_the_outdated_addon_text_names_the_minimum_version():
    """Der Abbruch `addon_veraltet` nennt dem Nutzer die Version, auf die er aktualisieren soll: genau die Mindestversion."""
    import json
    from pathlib import Path

    paket = Path(const.__file__).parent
    minimum = const.MIN_ADDON_VERSIONS[const.HEIZUNGSBRUECKE_ADDON_SLUG]
    for datei in ("strings.json", "translations/en.json", "translations/de.json"):
        text = json.loads((paket / datei).read_text(encoding="utf-8"))["config"]["abort"]["addon_veraltet"]
        assert f" {minimum} " in text, datei


def _alle_texte(knoten, pfad=""):
    if isinstance(knoten, str):
        yield pfad, knoten
    elif isinstance(knoten, dict):
        for schluessel, wert in knoten.items():
            yield from _alle_texte(wert, f"{pfad}.{schluessel}" if pfad else schluessel)
    elif isinstance(knoten, list):
        for nr, wert in enumerate(knoten):
            yield from _alle_texte(wert, f"{pfad}[{nr}]")


@pytest.mark.parametrize("datei", ["strings.json", "translations/en.json", "translations/de.json"])
def test_kein_uebersetzungstext_hat_fuehrenden_oder_nachfolgenden_leerraum(datei):
    """hassfest weist jeden Text mit Leerraum am Anfang oder Ende ab; Zeilenumbrueche zwischen Textteilen gehoeren in
    den Code, der einen Platzhalter fuellt."""
    import json
    from pathlib import Path

    texte = json.loads((Path(const.__file__).parent / datei).read_text(encoding="utf-8"))
    assert [pfad for pfad, text in _alle_texte(texte) if text != text.strip()] == []


@pytest.mark.parametrize("status", ["reagiert_nicht", "addon_gestoppt"])
@pytest.mark.parametrize("datei, pruefen, moeglich", [
    ("strings.json", "check", "if possible"),
    ("translations/en.json", "check", "if possible"),
    ("translations/de.json", "prüf", "wenn möglich"),
])
def test_die_waechter_repairs_versprechen_keinen_neustart(status, datei, pruefen, moeglich):
    """Bei einer nicht aufloesbaren Bruecke oder ausgeschoepftem Kontingent (3 je Stunde) gibt es keinen Neustart; der
    Text steht in beiden Sprachen unter dem Statuscode, fordert zur Pruefung auf und verspricht den Neustart nur
    „wenn moeglich“."""
    import json
    from pathlib import Path

    issue = json.loads((Path(const.__file__).parent / datei).read_text(encoding="utf-8"))["issues"][status]
    assert issue["title"].strip()
    text = issue["description"]
    assert pruefen in text and "Heizungsbrücke" in text and moeglich in text
    assert "{" not in text


@pytest.mark.parametrize("datei, fehlt_text, mehrfach_text", [
    ("strings.json", "install", "more than once"),
    ("translations/en.json", "install", "more than once"),
    ("translations/de.json", "install", "mehrfach"),
])
def test_die_repairs_fehlt_und_mehrfach_haben_texte_ohne_neustartversprechen(datei, fehlt_text, mehrfach_text):
    """`addon_fehlt` und `addon_mehrfach` haben in jeder Sprache Titel, Beschreibung, Statustext; die Ablehnung der
    Wunschtemperatur bei fehlendem Add-on hat einen Text. Kein Neustartversprechen, kein Platzhalter."""
    import json
    from pathlib import Path

    texte = json.loads((Path(const.__file__).parent / datei).read_text(encoding="utf-8"))
    for befund, stichwort in (("addon_fehlt", fehlt_text), ("addon_mehrfach", mehrfach_text)):
        issue = texte["issues"][befund]
        assert issue["title"].strip() and stichwort in issue["description"].lower()
        assert "Heizungsbrücke" in issue["description"]
        assert "{" not in issue["description"] and "wenn möglich" not in issue["description"]
        assert "if possible" not in issue["description"]
        assert texte["entity"]["sensor"]["status"]["state"][befund].strip()
    assert texte["exceptions"]["addon_fehlt"]["message"].strip()


def test_kein_persistent_notification_im_paket():
    """Meldungen laufen als Repair; eine persistente Benachrichtigung entsteht nirgends mehr (und bliebe nach einem
    Neustart von Home Assistant nicht erhalten)."""
    from pathlib import Path

    paket = Path(const.__file__).parent
    treffer = [str(datei.name) for datei in paket.rglob("*.py") if "persistent_notification" in datei.read_text()]
    assert treffer == []
    assert not hasattr(const, "WATCHDOG_MESSAGES") and not hasattr(const, "WATCHDOG_REASONS")
    assert not hasattr(const, "watchdog_notification_id")
