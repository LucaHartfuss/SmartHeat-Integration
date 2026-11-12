"""Hinweise des Geraets werden HA-Reparatur-Hinweise; verschwindet ein Hinweis, verschwindet das
Issue. Je Schluessel ein nicht behebbares Issue, bekannte Schluessel mit eigenem Text."""
import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartheat import repairs
from custom_components.smartheat.const import (
    CODE_RE,
    DOMAIN,
    ERROR_TEXT_MAX,
    HINWEIS_KEYS,
    STATUS_EVENT,
    hinweis_issue_id,
)

DATA = {"geraet_id": "shg-a", "tenant_id": "zuhause2", "profile_id": "vaillant_gastherme_heizkoerper",
        "lever_set": "vaillant_vrc720", "hebel": ["curve", "room_setpoint", "heat_limit", "min_flow"],
        "shift_lever": "room_setpoint"}
EVENT = {"schema": 4, "geraet_id": "shg-a", "tenant_id": "zuhause2", "status": "regelt", "kreise": [{"kreis": 1}],
         "geraet_hinweise": []}
_COMPONENT = Path(__file__).parents[1] / "custom_components" / "smartheat"


async def _setup(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="shg-a", version=3)
    entry.add_to_hass(hass)
    with patch("custom_components.smartheat.coordinator.async_track_time_interval", autospec=True):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _own_issues(hass) -> dict:
    return {issue_id: issue for (domain, issue_id), issue in ir.async_get(hass).issues.items() if domain == DOMAIN}


async def test_device_hints_become_repairs_and_disappear(hass):
    await _setup(hass)
    hass.bus.async_fire(STATUS_EVENT, {**EVENT, "geraet_hinweise": [
        {"key": "update_noetig", "stufe": "kritisch", "text": "Bitte aktualisieren"},
        {"key": "neu_vom_server", "stufe": "warnung", "text": "Freier Text"}]})
    await hass.async_block_till_done()
    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, "hinweis_update_noetig")
    assert issue is not None and issue.severity == ir.IssueSeverity.ERROR and issue.translation_key == "update_noetig"
    assert issue.is_fixable is False
    other = registry.async_get_issue(DOMAIN, hinweis_issue_id("neu_vom_server"))
    assert other.translation_key == "geraet_hinweis" and other.translation_placeholders == {"text": "Freier Text"}
    assert other.severity == ir.IssueSeverity.WARNING
    hass.bus.async_fire(STATUS_EVENT, EVENT)
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, "hinweis_update_noetig") is None
    assert registry.async_get_issue(DOMAIN, hinweis_issue_id("neu_vom_server")) is None


async def test_only_the_vanished_hint_is_deleted(hass):
    await _setup(hass)
    both = [{"key": "geraet_doppelt", "stufe": "kritisch", "text": "Zweite Instanz"},
            {"key": "wunsch_begrenzt", "stufe": "warnung", "text": "Auf 25 °C begrenzt"}]
    hass.bus.async_fire(STATUS_EVENT, {**EVENT, "geraet_hinweise": both})
    await hass.async_block_till_done()
    hass.bus.async_fire(STATUS_EVENT, {**EVENT, "geraet_hinweise": both[:1]})
    await hass.async_block_till_done()
    assert set(_own_issues(hass)) == {"hinweis_geraet_doppelt"}


async def test_hints_of_a_foreign_device_create_no_issue(hass):
    await _setup(hass)
    hass.bus.async_fire(STATUS_EVENT, {**EVENT, "geraet_id": "shg-b", "geraet_hinweise": [
        {"key": "gesperrt", "stufe": "kritisch", "text": "x"}]})
    await hass.async_block_till_done()
    assert _own_issues(hass) == {}


async def test_issues_of_other_integrations_and_own_non_hint_issues_stay(hass):
    ir.async_create_issue(hass, "andere", "hinweis_gesperrt", is_fixable=False, severity=ir.IssueSeverity.WARNING,
                          translation_key="x")
    ir.async_create_issue(hass, DOMAIN, "etwas_anderes", is_fixable=False, severity=ir.IssueSeverity.WARNING,
                          translation_key="x")
    repairs.async_update(hass, [{"key": "gesperrt", "stufe": "kritisch", "text": "x"}])
    repairs.async_clear(hass)
    registry = ir.async_get(hass)
    assert registry.async_get_issue("andere", "hinweis_gesperrt") is not None
    assert registry.async_get_issue(DOMAIN, "etwas_anderes") is not None
    assert registry.async_get_issue(DOMAIN, "hinweis_gesperrt") is None


async def test_event_with_broken_hint_list_still_updates_the_status(hass):
    entry = await _setup(hass)
    hass.bus.async_fire(STATUS_EVENT, {**EVENT, "geraet_hinweise": "kaputt"})
    await hass.async_block_till_done()
    assert entry.runtime_data.data["status"] == "regelt"
    assert _own_issues(hass) == {}


@pytest.mark.parametrize("hinweise", [
    [{"stufe": "kritisch", "text": "ohne Schluessel"}], [{"key": "", "text": "leer"}], [{"key": 3, "text": "Zahl"}],
    ["kein Objekt"], [None],
])
def test_broken_hints_create_no_issue_and_do_not_raise(hass, hinweise):
    repairs.async_update(hass, hinweise)
    assert _own_issues(hass) == {}


@pytest.mark.parametrize("key", ["Frau Mustermann ist verreist", "Update-Noetig", "a" * 41, "ä", "x/y", "a b"])
def test_a_key_that_is_not_a_code_creates_no_issue(hass, key):
    """Die Issue-ID steht im Diagnose-Download: ein Schluessel mit freiem Text darf dort nie erscheinen."""
    repairs.async_update(hass, [{"key": key, "stufe": "kritisch", "text": "x"}, {"key": "wunsch_begrenzt"}])
    assert set(_own_issues(hass)) == {"hinweis_wunsch_begrenzt"}


def test_hint_without_text_or_stufe_is_a_warning_with_empty_text(hass):
    repairs.async_update(hass, [{"key": "soll_quelle_aus"}])
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "hinweis_soll_quelle_aus")
    assert (issue.severity, issue.translation_key, issue.translation_placeholders) == (
        ir.IssueSeverity.WARNING, "soll_quelle_aus", {"text": ""})


def _device_issues(hass) -> dict:
    return {i: issue for i, issue in _own_issues(hass).items() if i.startswith("hinweis_")}


def _flut(anzahl: int, stufe: str = "warnung", prefix: str = "flut") -> list[dict]:
    return [{"key": f"{prefix}_{n:03d}", "stufe": stufe, "text": "x"} for n in range(anzahl)]


async def test_a_flood_of_device_hints_is_capped_with_one_summary_issue(hass):
    """Ein fremdes Ereignis kann beliebig viele Hinweise melden; die Integration legt hoechstens 50 Issues an und
    ein Sammel-Issue mit der Zahl der uebrigen."""
    await _setup(hass)
    hass.bus.async_fire(STATUS_EVENT, {**EVENT, "geraet_hinweise": _flut(120)})
    await hass.async_block_till_done()
    issues = _device_issues(hass)
    sammel = issues.pop("hinweis_sammel")
    assert len(issues) == 50
    assert sammel.translation_key == "geraet_hinweise_viele"
    assert sammel.translation_placeholders == {"anzahl": "70"}
    assert sammel.is_fixable is False and sammel.severity == ir.IssueSeverity.WARNING
    assert all(issue.translation_key == "geraet_hinweis" for issue in issues.values())


def test_exactly_fifty_hints_need_no_summary_issue(hass):
    repairs.async_update(hass, _flut(50))
    assert len(_device_issues(hass)) == 50 and "hinweis_sammel" not in _device_issues(hass)
    repairs.async_update(hass, _flut(51))
    assert len(_device_issues(hass)) == 51 and _device_issues(hass)["hinweis_sammel"].translation_placeholders == {
        "anzahl": "1"}


def test_critical_hints_survive_the_cap(hass):
    """Kommen die kritischen Hinweise erst nach den ersten 50, fallen sie nicht weg."""
    kritisch = _flut(5, "kritisch", "rot")
    repairs.async_update(hass, [*_flut(80), *kritisch])
    issues = _device_issues(hass)
    assert {hinweis_issue_id(h["key"]) for h in kritisch} <= set(issues)
    assert issues["hinweis_sammel"].translation_placeholders == {"anzahl": "35"}


def test_the_summary_issue_follows_the_flood_and_goes_with_it(hass):
    repairs.async_update(hass, _flut(60))
    assert "hinweis_sammel" in _device_issues(hass)
    repairs.async_update(hass, _flut(10))
    assert len(_device_issues(hass)) == 10
    repairs.async_update(hass, _flut(60))
    repairs.async_clear(hass)
    assert _own_issues(hass) == {}


def test_a_repeated_key_counts_once(hass):
    repairs.async_update(hass, _flut(50) + _flut(50))
    assert len(_device_issues(hass)) == 50


_NAMEN = ("raumfuehler", "wohnzimmer", "schlafzimmer", "sensor", "batterie")


@pytest.mark.parametrize("key", [
    "k2_raumfuehler_wohnzimmer_sensor", "k1_batterie_schlafzimmer", "raumfuehler_wohnzimmer", "sensor_kueche_1",
    "k12_x", "k_wohnzimmer", "k2", "k2_", "K4_Wohnzimmer", "k1234_x", "neuer_hinweis", "k3_gesperrt", "a" * 40,
])
def test_issue_ids_of_unknown_keys_carry_only_the_circuit_and_a_hash(key):
    """Die Issue-ID steht im Diagnose-Download und in der Registry: kein Entity- oder Fuehlername, hoechstens 64 Zeichen
    aus dem Alphabet eines Codes; der Kreis bleibt als `k<n>` lesbar, der Rest ist ein Kurzhash."""
    issue_id = hinweis_issue_id(key)
    assert len(issue_id) <= 64 and CODE_RE.fullmatch(issue_id[len("hinweis_"):]) is not None
    assert not any(name in issue_id for name in _NAMEN)
    kreis = re.match(r"k\d{1,3}(?=_.)", key.lower())
    erwartet = rf"hinweis_{kreis.group()}_h[0-9a-f]{{10}}" if kreis else r"hinweis_h[0-9a-f]{10}"
    assert re.fullmatch(erwartet, issue_id), issue_id
    assert hinweis_issue_id(key) == issue_id


def test_issue_ids_are_stable_and_distinct_and_known_keys_stay_readable():
    assert hinweis_issue_id("k2_raumfuehler_a") != hinweis_issue_id("k2_raumfuehler_b")
    assert hinweis_issue_id("k2_raumfuehler_a") != hinweis_issue_id("k3_raumfuehler_a")
    assert hinweis_issue_id("raumfuehler_a") != hinweis_issue_id("raumfuehler_b")
    for key in HINWEIS_KEYS:
        assert hinweis_issue_id(key) == f"hinweis_{key}"


def test_a_hint_with_a_sensor_name_never_reaches_the_registry_ids(hass):
    repairs.async_update(hass, [{"key": "k2_raumfuehler_wohnzimmer_sensor", "stufe": "kritisch", "text": "x"}])
    (issue_id,) = _device_issues(hass)
    assert "wohnzimmer" not in issue_id and "raumfuehler" not in issue_id and issue_id.startswith("hinweis_k2_h")


@pytest.mark.parametrize("path", ["strings.json", "translations/en.json", "translations/de.json"])
def test_the_summary_issue_has_a_text_with_the_count(path):
    issue = json.loads((_COMPONENT / path).read_text())["issues"]["geraet_hinweise_viele"]
    assert issue["title"].strip() and "{anzahl}" in issue["description"]


_MARKDOWN_ZEICHEN = "\\`*_[]()<>#!|~&:.@"


def test_servertext_erscheint_als_klartext(hass):
    """HA stellt die Beschreibung eines Hinweises als Markdown dar; der Text kommt vom Geraet (und damit
    mittelbar vom Server) und darf weder einen Link noch Formatierung einschleusen: jedes Markdown-Zeichen wird mit
    `\\` maskiert, der Nutzer liest den Text unveraendert."""
    text = "[Link](https://x) **fett** `code` <b>x</b> a_b # h ! i | t ~ s \\ ende"
    repairs.async_update(hass, [{"key": "gesperrt", "stufe": "kritisch", "text": text}])

    angezeigt = ir.async_get(hass).async_get_issue(DOMAIN, "hinweis_gesperrt").translation_placeholders["text"]

    assert angezeigt.startswith("\\[Link\\]\\(https\\://x\\) \\*\\*fett\\*\\* ")
    ungeschuetzt = re.sub(r"\\.", "", angezeigt)  # jedes maskierte Zeichen entfernen
    assert not any(zeichen in ungeschuetzt for zeichen in _MARKDOWN_ZEICHEN)
    assert re.sub(r"\\(.)", r"\1", angezeigt) == text  # Maskierung verlustfrei


@pytest.mark.parametrize("zeichen", list(_MARKDOWN_ZEICHEN))
def test_every_markdown_character_is_masked(hass, zeichen):
    repairs.async_update(hass, [{"key": "gesperrt", "text": f"a{zeichen}b"}])
    angezeigt = ir.async_get(hass).async_get_issue(DOMAIN, "hinweis_gesperrt").translation_placeholders["text"]
    assert angezeigt == f"a\\{zeichen}b"


def _angezeigt(hass, text) -> str:
    repairs.async_update(hass, [{"key": "gesperrt", "text": text}])
    return ir.async_get(hass).async_get_issue(DOMAIN, "hinweis_gesperrt").translation_placeholders["text"]


# Was HAs Markdown (GFM) ohne Zutun eines Links selbst verlinkt: URL mit Schema, `www.`-Adresse, E-Mail-Adresse.
_AUTOLINK = re.compile(r"(?<!\\)(?:[a-z][a-z0-9+.-]*://|www\.|[\w.+-]+@[\w-]+\.)", re.IGNORECASE)


@pytest.mark.parametrize("text", [
    "Bitte https://evil.example/x oeffnen", "Bitte http://evil.example oeffnen", "www.evil.example",
    "Mail an angreifer@evil.example", "ftp://evil.example/x",
])
def test_servertext_enthaelt_keine_automatisch_verlinkte_adresse(hass, text):
    """Auch ohne Link-Syntax verlinkt Markdown nackte Adressen; `:`, `.` und `@` sind
    maskiert, damit keine anklickbare Adresse entsteht (ein manipulierter Server koennte sonst einen Phishing-Link in
    einen Hinweis legen)."""
    assert text != _angezeigt(hass, text)
    assert _AUTOLINK.search(text)  # Gegenprobe: der Roh-Text waere verlinkt worden
    assert not _AUTOLINK.search(_angezeigt(hass, text))


def test_servertext_zeilenumbrueche_und_steuerzeichen_werden_zu_leerzeichen(hass):
    """Ein Zeilenumbruch koennte einen eigenen Markdown-Block (Liste, Ueberschrift, Code) beginnen."""
    assert _angezeigt(hass, "a\r\nb\nc\rd\te\x00f\x7fg\u2028h\u2029i") == "a  b c d e f g h i"


def test_servertext_und_zeichen_bleibt_woertlich(hass):
    """`&amp;` darf nicht als HTML-Entity gelesen werden: das `&` ist maskiert."""
    assert _angezeigt(hass, "Tom &amp; Jerry &#60;") == "Tom \\&amp; Jerry \\&\\#60;"
    assert re.sub(r"\\(.)", r"\1", _angezeigt(hass, "Tom &amp; Jerry")) == "Tom &amp; Jerry"


def test_gewoehnlicher_deutscher_text_bleibt_lesbar(hass):
    """Gegenprobe: Umlaute, ein Komma-Dezimalwert und die Einheit bleiben unveraendert."""
    text = "Thermostat aus, SmartHeat hält 21,5 °C – bitte prüfen"
    assert _angezeigt(hass, text) == text


@pytest.mark.parametrize("text, erwartet", [
    ("- Punkt", "\\- Punkt"),
    ("+ Punkt", "\\+ Punkt"),
    ("* Punkt", "\\* Punkt"),
    ("1. Punkt", "1\\. Punkt"),
    ("2) Punkt", "2\\) Punkt"),
    ("    Code", "Code"),
    ("\t\n  Text", "Text"),
    ("---", "\\---"),
    ("===", "\\==="),
    ("> Zitat", "\\> Zitat"),
    ("# Titel", "\\# Titel"),
    ("-5 K", "\\-5 K"),
])
def test_servertext_beginnt_keinen_markdown_block(hass, text, erwartet):
    """Steht der Text am Anfang der Beschreibung, wuerde fuehrende Block-Syntax
    (Liste, Codeblock, Linie, Ueberschrift, Zitat) eine Darstellung erzeugen. Fuehrender Leerraum faellt weg, ein
    fuehrendes `-`, `+`, `=` wird maskiert (Ziffer mit Punkt und `*`, `>`, `#` maskiert schon die Zeichenliste)."""
    assert _angezeigt(hass, text) == erwartet


def test_ein_bindestrich_mitten_im_text_bleibt_unveraendert(hass):
    """Gegenprobe: nur am Anfang wird maskiert (`SmartHeat-Dienst` bleibt lesbar)."""
    assert _angezeigt(hass, "Der SmartHeat-Dienst - 5 K") == "Der SmartHeat-Dienst - 5 K"


def test_servertext_ohne_bidi_und_zero_width_zeichen(hass):
    """Zero-width- und Bidi-Steuerzeichen (U+200B-200F, U+202A-202E,
    U+2066-2069, U+FEFF) koennten den Text optisch umdrehen oder Zeichen verstecken: sie fallen weg."""
    zeichen = [chr(c) for c in (*range(0x200B, 0x2010), *range(0x202A, 0x202F), *range(0x2066, 0x206A), 0xFEFF)]
    assert _angezeigt(hass, "a" + "".join(zeichen) + "b\u202Ec\u200Bd") == "abcd"
    assert _angezeigt(hass, "ä ö ü ß – 21,5 °C") == "ä ö ü ß – 21,5 °C"  # Gegenprobe: Umlaute usw. bleiben


def test_servertext_wird_gekuerzt(hass):
    """Der Text eines Hinweises hat hoechstens ERROR_TEXT_MAX Zeichen (vor der Maskierung); danach stehen
    keine halben Maskierungen am Ende."""
    repairs.async_update(hass, [{"key": "gesperrt", "text": "x" * 1000},
                                {"key": "wunsch_begrenzt", "text": "*" * 1000}])
    issues = _own_issues(hass)

    assert issues["hinweis_gesperrt"].translation_placeholders["text"] == "x" * ERROR_TEXT_MAX
    assert issues["hinweis_wunsch_begrenzt"].translation_placeholders["text"] == "\\*" * ERROR_TEXT_MAX


# Texte des Geraets wie heizungsbruecke/geraet_status.py und smartheat_device.device.ABLEHNUNG_ANZEIGE (Deutsch).
_UPDATE_TEXT = "Das Add-on ist zu alt für den SmartHeat-Dienst. Bitte das Add-on aktualisieren."
_ABGELEHNT_TEXT = "Die Einrichtung wurde abgelehnt (Code rolle_fehlt). Die Begründung zeigt der Einrichtungsassistent."


async def test_hint_issues_keep_todays_ids_and_texts(hass):
    """Heutiges Verhalten festgehalten (offene-punkte.md 5c-I-14): die Issue-ID ist nur `hinweis_<schluessel>` (nicht je
    Eintrag, passt zu `single_config_entry`); die Beschreibung von `update_noetig` wiederholt nach dem Geraetetext dessen Anweisung; die
    deutschen Geraetetexte stehen auch in den englischen Beschreibungen. Die Geraetetexte stehen mit
    maskierten Zeichen darin (`(`, `)`, `_`, `.`); HA stellt sie als Klartext dar."""
    entry = await _setup(hass)
    hass.bus.async_fire(STATUS_EVENT, {**EVENT, "geraet_hinweise": [
        {"key": "update_noetig", "stufe": "kritisch", "text": _UPDATE_TEXT},
        {"key": "konfiguration_abgelehnt", "stufe": "kritisch", "text": _ABGELEHNT_TEXT}]})
    await hass.async_block_till_done()
    issues = _own_issues(hass)
    assert set(issues) == {"hinweis_update_noetig", "hinweis_konfiguration_abgelehnt"}
    assert not any(entry.entry_id in issue_id for issue_id in issues)

    def beschreibung(texte, issue_id):
        issue = issues[issue_id]
        return texte[f"component.{DOMAIN}.issues.{issue.translation_key}.description"].format(
            **issue.translation_placeholders)

    en = await async_get_translations(hass, "en", "issues", [DOMAIN])
    de = await async_get_translations(hass, "de", "issues", [DOMAIN])
    assert beschreibung(en, "hinweis_update_noetig") == (
        "Das Add-on ist zu alt für den SmartHeat-Dienst\\. Bitte das Add-on aktualisieren\\. "
        "Settings → Add-ons → Heizungsbrücke → Update.")
    assert beschreibung(de, "hinweis_update_noetig") == (
        "Das Add-on ist zu alt für den SmartHeat-Dienst\\. Bitte das Add-on aktualisieren\\. "
        "Einstellungen → Add-ons → Heizungsbrücke → Aktualisieren.")
    assert beschreibung(en, "hinweis_konfiguration_abgelehnt") == (
        "The add-on rejected the new setting: Die Einrichtung wurde abgelehnt \\(Code rolle\\_fehlt\\)\\. "
        "Die Begründung zeigt der Einrichtungsassistent\\. Please check it under SmartHeat → Reconfigure.")


@pytest.mark.parametrize("path", ["strings.json", "translations/en.json", "translations/de.json"])
def test_every_known_hint_has_a_text_with_the_device_text(path):
    issues = json.loads((_COMPONENT / path).read_text())["issues"]
    for key in (*HINWEIS_KEYS, "geraet_hinweis"):
        assert issues[key]["title"].strip(), (path, key)
        assert "{text}" in issues[key]["description"], (path, key)
    assert "complete_setup" not in issues


# --- Schalter "Automatische Updates" des Add-ons ---


def test_an_update_switch_that_is_off_is_a_warning_that_can_be_ignored_and_goes_when_it_is_on(hass):
    repairs.async_set_auto_update(hass, False)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, "auto_update_aus")
    assert (issue.severity, issue.is_fixable, issue.is_persistent, issue.translation_key) == (
        ir.IssueSeverity.WARNING, False, False, "auto_update_aus")
    assert issue.translation_placeholders is None

    repairs.async_set_auto_update(hass, False)  # wiederholt: dasselbe Issue, kein zweites
    assert list(_own_issues(hass)) == ["auto_update_aus"]

    repairs.async_set_auto_update(hass, True)
    assert _own_issues(hass) == {}


def test_an_unknown_update_switch_changes_nothing(hass):
    repairs.async_set_auto_update(hass, None)
    assert _own_issues(hass) == {}

    repairs.async_set_auto_update(hass, False)
    repairs.async_set_auto_update(hass, None)  # Lesefehler: kein Beleg fuer "an", das Issue bleibt, wie es war
    assert list(_own_issues(hass)) == ["auto_update_aus"]


def test_an_ignored_update_repair_stays_ignored_while_the_switch_stays_off(hass):
    repairs.async_set_auto_update(hass, False)
    ir.async_ignore_issue(hass, DOMAIN, "auto_update_aus", True)

    repairs.async_set_auto_update(hass, False)

    assert ir.async_get(hass).async_get_issue(DOMAIN, "auto_update_aus").dismissed_version is not None


async def test_an_ignored_update_repair_stays_ignored_after_the_integration_is_reloaded(hass):
    entry = await _setup(hass)
    repairs.async_set_auto_update(hass, False)
    ir.async_ignore_issue(hass, DOMAIN, "auto_update_aus", True)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    repairs.async_set_auto_update(hass, False)  # das naechste Lebenszeichen meldet den Schalter weiter als "aus"

    assert ir.async_get(hass).async_get_issue(DOMAIN, "auto_update_aus").dismissed_version is not None


async def test_a_visible_update_repair_goes_when_the_integration_is_unloaded(hass):
    entry = await _setup(hass)
    repairs.async_set_auto_update(hass, False)

    assert await hass.config_entries.async_unload(entry.entry_id)

    assert _own_issues(hass) == {}


async def test_removing_the_entry_takes_an_ignored_update_repair_too(hass):
    entry = await _setup(hass)
    repairs.async_set_auto_update(hass, False)
    ir.async_ignore_issue(hass, DOMAIN, "auto_update_aus", True)

    await hass.config_entries.async_remove(entry.entry_id)

    assert _own_issues(hass) == {}


def test_clearing_removes_the_update_repair(hass):
    repairs.async_set_auto_update(hass, False)
    repairs.async_clear(hass)
    assert _own_issues(hass) == {}


@pytest.mark.parametrize("path", ["strings.json", "translations/en.json", "translations/de.json"])
def test_the_update_repair_names_the_place_to_switch_it_back_on(path):
    issue = json.loads((_COMPONENT / path).read_text())["issues"]["auto_update_aus"]
    assert issue["title"].strip() and issue["description"].strip()
