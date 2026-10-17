"""Reparatur-Hinweise aus den Hinweisen des Geraets (Spec 5c 1.3/2.2): je Schluessel ein nicht behebbares Issue;
bekannte Schluessel mit eigenem Text, andere mit dem Text des Geraets. Verschwindet ein Schluessel, verschwindet das
Issue. Kein Fix-Flow (alle Issues sind nicht behebbar); HA laedt dieses Modul als repairs-Plattform nur fuer
behebbare Issues."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, HINWEIS_FELD_KEY, HINWEIS_FELD_STUFE, HINWEIS_FELD_TEXT, HINWEIS_KEYS, hinweis_issue_id

KRITISCH = "kritisch"
_PREFIX = "hinweis_"
GERAET_HINWEIS = "geraet_hinweis"


def async_update(hass: HomeAssistant, hinweise: list[dict]) -> None:
    """Issues `hinweis_<key>` auf den Stand von `hinweise` bringen; Eintraege ohne Schluessel ignorieren."""
    registry = ir.async_get(hass)
    aktuell = set()
    for hinweis in hinweise:
        if not isinstance(hinweis, dict):
            continue
        key = hinweis.get(HINWEIS_FELD_KEY)
        if not isinstance(key, str) or not key:
            continue
        issue_id = hinweis_issue_id(key)
        aktuell.add(issue_id)
        ir.async_create_issue(
            hass, DOMAIN, issue_id, is_fixable=False,
            severity=ir.IssueSeverity.ERROR if hinweis.get(HINWEIS_FELD_STUFE) == KRITISCH else ir.IssueSeverity.WARNING,
            translation_key=key if key in HINWEIS_KEYS else GERAET_HINWEIS,
            translation_placeholders={"text": str(hinweis.get(HINWEIS_FELD_TEXT) or "")},
        )
    for domain, issue_id in list(registry.issues):
        if domain == DOMAIN and issue_id.startswith(_PREFIX) and issue_id not in aktuell:
            ir.async_delete_issue(hass, DOMAIN, issue_id)


def async_clear(hass: HomeAssistant) -> None:
    async_update(hass, [])
