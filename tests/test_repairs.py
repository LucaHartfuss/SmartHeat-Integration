"""Spec 5c 1.3/2.2: Hinweise des Geraets werden HA-Reparatur-Hinweise; verschwindet ein Hinweis, verschwindet das
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
from custom_components.smartheat.const import DOMAIN, ERROR_TEXT_MAX, HINWEIS_KEYS, STATUS_EVENT

DATA = {"geraet_id": "shg-a", "tenant_id": "zuhause2", "profile_id": "vaillant_gastherme_heizkoerper",
        "lever_set": "vaillant_vrc720", "hebel": ["curve", "room_setpoint", "heat_limit", "min_flow"],
        "shift_lever": "room_setpoint"}
EVENT = {"schema": 3, "geraet_id": "shg-a", "tenant_id": "zuhause2", "status": "regelt", "raum_soll": 21.0,
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
    other = registry.async_get_issue(DOMAIN, "hinweis_neu_vom_server")
    assert other.translation_key == "geraet_hinweis" and other.translation_placeholders == {"text": "Freier Text"}
    assert other.severity == ir.IssueSeverity.WARNING
    hass.bus.async_fire(STATUS_EVENT, EVENT)
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, "hinweis_update_noetig") is None
    assert registry.async_get_issue(DOMAIN, "hinweis_neu_vom_server") is None


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


def test_hint_without_text_or_stufe_is_a_warning_with_empty_text(hass):
    repairs.async_update(hass, [{"key": "soll_quelle_aus"}])
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "hinweis_soll_quelle_aus")
    assert (issue.severity, issue.translation_key, issue.translation_placeholders) == (
        ir.IssueSeverity.WARNING, "soll_quelle_aus", {"text": ""})


_MARKDOWN_ZEICHEN = "\\`*_[]()<>#!|~&:.@"


def test_servertext_erscheint_als_klartext(hass):
    """A6-42: HA stellt die Beschreibung eines Hinweises als Markdown dar; der Text kommt vom Geraet (und damit
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
    """Security-Nachtrag A6-42: auch ohne Link-Syntax verlinkt Markdown nackte Adressen; `:`, `.` und `@` sind
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


def test_servertext_wird_gekuerzt(hass):
    """A6-42: der Text eines Hinweises hat hoechstens ERROR_TEXT_MAX Zeichen (vor der Maskierung); danach stehen
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
    """5c-I-14, heutiges Verhalten festgehalten: die Issue-ID ist nur `hinweis_<schluessel>` (nicht je Eintrag, passt zu
    `single_config_entry`); die Beschreibung von `update_noetig` wiederholt nach dem Geraetetext dessen Anweisung; die
    deutschen Geraetetexte stehen auch in den englischen Beschreibungen. Seit A6-42 stehen die Geraetetexte mit
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
        "The add-on rejected the new setting: Die Einrichtung wurde abgelehnt \\(Code rolle\\_fehlt\\)\\. Die Begründung "
        "zeigt der Einrichtungsassistent\\. Please check it under SmartHeat → Reconfigure.")


@pytest.mark.parametrize("path", ["strings.json", "translations/en.json", "translations/de.json"])
def test_every_known_hint_has_a_text_with_the_device_text(path):
    issues = json.loads((_COMPONENT / path).read_text())["issues"]
    for key in (*HINWEIS_KEYS, "geraet_hinweis"):
        assert issues[key]["title"].strip(), (path, key)
        assert "{text}" in issues[key]["description"], (path, key)
    assert "complete_setup" not in issues
