"""Spec 5c 1.3/2.2: Hinweise des Geraets werden HA-Reparatur-Hinweise; verschwindet ein Hinweis, verschwindet das
Issue. Je Schluessel ein nicht behebbares Issue, bekannte Schluessel mit eigenem Text."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartheat import repairs
from custom_components.smartheat.const import DOMAIN, HINWEIS_KEYS, STATUS_EVENT

DATA = {"geraet_id": "shg-a", "tenant_id": "zuhause2", "profile_id": "vaillant_gastherme_heizkoerper",
        "lever_set": "vaillant_vrc720", "hebel": ["curve", "room_setpoint", "heat_limit", "min_flow"],
        "shift_lever": "room_setpoint"}
EVENT = {"schema": 3, "geraet_id": "shg-a", "tenant_id": "zuhause2", "status": "regelt", "raum_soll": 21.0,
         "geraet_hinweise": []}
_COMPONENT = Path(__file__).parents[1] / "custom_components" / "smartheat"


async def _setup(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="shg-a", version=2)
    entry.add_to_hass(hass)
    with patch("custom_components.smartheat.coordinator.async_track_time_interval"):
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


@pytest.mark.parametrize("path", ["strings.json", "translations/en.json", "translations/de.json"])
def test_every_known_hint_has_a_text_with_the_device_text(path):
    issues = json.loads((_COMPONENT / path).read_text())["issues"]
    for key in (*HINWEIS_KEYS, "geraet_hinweis"):
        assert issues[key]["title"].strip(), (path, key)
        assert "{text}" in issues[key]["description"], (path, key)
    assert "complete_setup" not in issues
