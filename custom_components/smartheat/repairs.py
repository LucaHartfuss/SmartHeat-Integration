"""Reparatur-Hinweise aus den Hinweisen des Geraets: je Schluessel ein nicht behebbares Issue;
bekannte Schluessel mit eigenem Text, andere mit dem Text des Geraets. Verschwindet ein Schluessel, verschwindet das
Issue. Kein Fix-Flow (alle Issues sind nicht behebbar); HA laedt dieses Modul als repairs-Plattform nur fuer
behebbare Issues."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import (
    AUTO_UPDATE_ISSUE_ID,
    CODE_RE,
    DOMAIN,
    HINWEIS_FELD_KEY,
    HINWEIS_FELD_STUFE,
    HINWEIS_FELD_TEXT,
    HINWEIS_KEYS,
    HINWEIS_SAMMEL_ID,
    HINWEIS_SAMMEL_KEY,
    HINWEISE_MAX,
    ISSUE_OHNE_VERSION,
    MINDEST_INTEGRATION_RE,
    STATUS_ADDON_MEHRFACH,
    STATUS_INTEGRATION_VERALTET,
    STATUS_VERSION_VALUES,
    WATCHDOG_ISSUE_ID,
    hinweis_issue_id,
)
from .texts import klartext

KRITISCH = "kritisch"
_PREFIX = "hinweis_"
GERAET_HINWEIS = "geraet_hinweis"


def async_update(hass: HomeAssistant, hinweise: list[dict]) -> None:
    """Issues `hinweis_…` auf den Stand von `hinweise` bringen; Eintraege ohne Schluessel oder mit einem Schluessel,
    der kein Code ist, ignorieren (die Issue-ID steht im Diagnose-Download und darf keinen freien Text tragen; ihre
    Bildung: `const.hinweis_issue_id`). Hoechstens HINWEISE_MAX Issues, kritische zuerst; fuer die uebrigen gibt es ein
    Sammel-Issue mit deren Anzahl."""
    registry = ir.async_get(hass)
    gueltig: dict[str, tuple[str, dict]] = {}
    for hinweis in hinweise:
        if not isinstance(hinweis, dict):
            continue
        key = hinweis.get(HINWEIS_FELD_KEY)
        if not isinstance(key, str) or CODE_RE.fullmatch(key) is None:
            continue
        gueltig.setdefault(hinweis_issue_id(key), (key, hinweis))
    # sorted ist stabil: innerhalb einer Stufe bleibt die Reihenfolge des Geraets
    ausgewaehlt = sorted(gueltig.items(), key=lambda eintrag: eintrag[1][1].get(HINWEIS_FELD_STUFE) != KRITISCH)
    aktuell = set()
    for issue_id, (key, hinweis) in ausgewaehlt[:HINWEISE_MAX]:
        aktuell.add(issue_id)
        severity = ir.IssueSeverity.ERROR if hinweis.get(HINWEIS_FELD_STUFE) == KRITISCH else ir.IssueSeverity.WARNING
        ir.async_create_issue(
            hass, DOMAIN, issue_id, is_fixable=False,
            severity=severity,
            translation_key=key if key in HINWEIS_KEYS else GERAET_HINWEIS,
            translation_placeholders={"text": klartext(hinweis.get(HINWEIS_FELD_TEXT))},
        )
    ueberzaehlig = len(ausgewaehlt) - HINWEISE_MAX
    if ueberzaehlig > 0:
        aktuell.add(HINWEIS_SAMMEL_ID)
        ir.async_create_issue(
            hass, DOMAIN, HINWEIS_SAMMEL_ID, is_fixable=False, severity=ir.IssueSeverity.WARNING,
            translation_key=HINWEIS_SAMMEL_KEY, translation_placeholders={"anzahl": str(ueberzaehlig)},
        )
    for domain, issue_id in list(registry.issues):
        if domain == DOMAIN and issue_id.startswith(_PREFIX) and issue_id not in aktuell:
            ir.async_delete_issue(hass, DOMAIN, issue_id)


def async_set_version(hass: HomeAssistant, befund: str | None, mindest_integration: object = None) -> None:
    """Das Repair zum Versionsbefund (`integration_veraltet` oder `addon_veraltet`) setzen; `None` nimmt es zurueck. Es
    gibt hoechstens eines. Nur der Text zu `integration_veraltet` nennt die Mindestversion aus dem Anlagenfeld `mindest_integration` des Ereignisses;
    sie erscheint nur, wenn sie wie eine Version aussieht, sonst nimmt das Repair den Text ohne Version. Nicht
    behebbar, Warnung (Orange), kein Push."""
    for alt in STATUS_VERSION_VALUES:
        if alt != befund:
            ir.async_delete_issue(hass, DOMAIN, alt)
    if befund is None:
        return
    version = (mindest_integration if isinstance(mindest_integration, str)
               and MINDEST_INTEGRATION_RE.fullmatch(mindest_integration) is not None else None)
    nennt_version = befund == STATUS_INTEGRATION_VERALTET and version is not None
    ohne_version = befund == STATUS_INTEGRATION_VERALTET and version is None
    ir.async_create_issue(
        hass, DOMAIN, befund, is_fixable=False, severity=ir.IssueSeverity.WARNING,
        translation_key=befund + ISSUE_OHNE_VERSION if ohne_version else befund,
        translation_placeholders={"mindest_integration": version} if nennt_version and version else None,
    )


def async_set_watchdog(hass: HomeAssistant, befund: str | None) -> None:
    """Das Repair zum Befund des Waechters (`addon_gestoppt`, `reagiert_nicht`, `addon_fehlt` oder `addon_mehrfach`)
    setzen; `None` nimmt es zurueck. Es gibt hoechstens eines, unter einer festen ID: eskaliert der Vorfall, aendert
    sich nur der Text. Nicht behebbar, als persistent angelegt, aber die Integration raeumt es bei jedem Start ab,
    auch wenn der Befund fortbesteht; der neue Waechter legt es erst nach seinem ersten Befund neu an (nach einem
    Neustart von Home Assistant fehlt es daher 10 bis 15 Minuten). Fehler, nur `addon_mehrfach` ist eine Warnung (das
    Add-on kann dabei gesund laufen). Der Text steht nur in den Uebersetzungen, ohne Platzhalter."""
    if befund is None:
        ir.async_delete_issue(hass, DOMAIN, WATCHDOG_ISSUE_ID)
        return
    severity = ir.IssueSeverity.WARNING if befund == STATUS_ADDON_MEHRFACH else ir.IssueSeverity.ERROR
    ir.async_create_issue(
        hass, DOMAIN, WATCHDOG_ISSUE_ID, is_fixable=False, is_persistent=True, severity=severity,
        translation_key=befund,
    )


def async_set_auto_update(hass: HomeAssistant, auto_update: bool | None) -> None:
    """Das Repair "automatische Updates des Add-ons aus" setzen (`False`) oder nehmen (`True`). `None` (Schalter nicht
    lesbar) aendert nichts: ein Lesefehler ist kein Beleg fuer "an" und keiner fuer "aus". Nicht behebbar, Warnung,
    vom Kunden ignorierbar; erneutes Setzen behaelt das Ignorieren. Der Text steht nur in den Uebersetzungen, ohne
    Platzhalter."""
    if auto_update is None:
        return
    if auto_update:
        ir.async_delete_issue(hass, DOMAIN, AUTO_UPDATE_ISSUE_ID)
        return
    ir.async_create_issue(
        hass, DOMAIN, AUTO_UPDATE_ISSUE_ID, is_fixable=False, severity=ir.IssueSeverity.WARNING,
        translation_key=AUTO_UPDATE_ISSUE_ID,
    )


def async_clear(hass: HomeAssistant) -> None:
    async_update(hass, [])
    async_set_version(hass, None)
    async_set_watchdog(hass, None)
    ir.async_delete_issue(hass, DOMAIN, AUTO_UPDATE_ISSUE_ID)
