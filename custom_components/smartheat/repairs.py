"""Reparatur-Hinweise aus den Hinweisen des Geraets: je Schluessel ein nicht behebbares Issue;
bekannte Schluessel mit eigenem Text, andere mit dem Text des Geraets. Verschwindet ein Schluessel, verschwindet das
Issue. Kein Fix-Flow (alle Issues sind nicht behebbar); HA laedt dieses Modul als repairs-Plattform nur fuer
behebbare Issues."""
from __future__ import annotations

import re

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import (
    CODE_RE,
    DOMAIN,
    ERROR_TEXT_MAX,
    HINWEIS_FELD_KEY,
    HINWEIS_FELD_STUFE,
    HINWEIS_FELD_TEXT,
    HINWEIS_KEYS,
    ISSUE_OHNE_VERSION,
    MINDEST_INTEGRATION_RE,
    STATUS_INTEGRATION_VERALTET,
    STATUS_VERSION_VALUES,
    hinweis_issue_id,
)

KRITISCH = "kritisch"
_PREFIX = "hinweis_"
GERAET_HINWEIS = "geraet_hinweis"
# Markdown-Zeichen, dazu `&` (HTML-Entitaeten) und `:`, `.`, `@`: ohne sie verlinkt Markdown nackte Adressen selbst
# (`https://…`, `www.…`, E-Mail-Adressen). Ein Backslash vor einem ASCII-Satzzeichen zeigt dieses woertlich.
_MARKDOWN = re.compile(r"([\\`*_\[\]()<>#!|~&:.@])")
_STEUERZEICHEN = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
# Zero-width- und Bidi-Steuerzeichen: sie verstecken Zeichen oder drehen den Text optisch um.
_UNSICHTBAR = re.compile("[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
# Liste, Linie, Setext-Ueberschrift; `*`, `>`, `#`, Ziffer mit Punkt maskiert _MARKDOWN
_BLOCKANFANG = re.compile(r"^[-+=]")


def _klartext(text: object) -> str:
    """Der Text des Geraets als Klartext fuer die Markdown-Beschreibung eines Issues: Zeilenumbrueche und andere
    Steuerzeichen zu Leerzeichen, Zero-width- und Bidi-Zeichen weg, fuehrender Leerraum weg (sonst Codeblock), auf
    ERROR_TEXT_MAX Zeichen gekuerzt, dann jedes Markdown-Zeichen sowie `&`, `:`, `.` und `@` mit `\\` maskiert und ein
    fuehrendes `-`, `+`, `=` ebenso -- weder ein Link (auch kein automatisch erkannter) noch Formatierung noch ein
    Block (Liste, Linie, Ueberschrift) kommt durch."""
    sauber = _UNSICHTBAR.sub("", _STEUERZEICHEN.sub(" ", str(text or ""))).lstrip()
    maskiert = _MARKDOWN.sub(r"\\\1", sauber[:ERROR_TEXT_MAX])
    return _BLOCKANFANG.sub(r"\\\g<0>", maskiert)


def async_update(hass: HomeAssistant, hinweise: list[dict]) -> None:
    """Issues `hinweis_<key>` auf den Stand von `hinweise` bringen; Eintraege ohne Schluessel oder mit einem Schluessel,
    der kein Code ist, ignorieren (die Issue-ID steht im Diagnose-Download und darf keinen freien Text tragen)."""
    registry = ir.async_get(hass)
    aktuell = set()
    for hinweis in hinweise:
        if not isinstance(hinweis, dict):
            continue
        key = hinweis.get(HINWEIS_FELD_KEY)
        if not isinstance(key, str) or CODE_RE.fullmatch(key) is None:
            continue
        issue_id = hinweis_issue_id(key)
        aktuell.add(issue_id)
        severity = ir.IssueSeverity.ERROR if hinweis.get(HINWEIS_FELD_STUFE) == KRITISCH else ir.IssueSeverity.WARNING
        ir.async_create_issue(
            hass, DOMAIN, issue_id, is_fixable=False,
            severity=severity,
            translation_key=key if key in HINWEIS_KEYS else GERAET_HINWEIS,
            translation_placeholders={"text": _klartext(hinweis.get(HINWEIS_FELD_TEXT))},
        )
    for domain, issue_id in list(registry.issues):
        if domain == DOMAIN and issue_id.startswith(_PREFIX) and issue_id not in aktuell:
            ir.async_delete_issue(hass, DOMAIN, issue_id)


def async_set_version(hass: HomeAssistant, befund: str | None, mindest_integration: object = None) -> None:
    """Das Repair zum Versionsbefund (`integration_veraltet` oder `addon_veraltet`) setzen; `None` nimmt es zurueck. Es
    gibt hoechstens eines. Nur der Text zu `integration_veraltet` nennt die Mindestversion aus dem Kern des Ereignisses;
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


def async_clear(hass: HomeAssistant) -> None:
    async_update(hass, [])
    async_set_version(hass, None)
