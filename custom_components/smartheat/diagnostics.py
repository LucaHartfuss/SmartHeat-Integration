"""Diagnose-Download des Eintrags: Versionen, Waechter, letzter Status mit Kreisen und die Verbindung zum Server.

Der Kunde laedt die Datei herunter und schickt sie bei Bedarf dem Support; sie darf nichts verraten, was einen Menschen,
ein Haus oder einen Zeitplan kennzeichnet. Darum gilt eine Positivliste statt einer Schwaerzung: nur benannte Felder
kommen heraus, und jedes nur in der Form, die sein Zweck braucht. Felder, die einen Code tragen, geben nie freien Text
weiter (ein veraendertes Add-on oder ein Fremdereignis koennte ihn einschleusen), sondern nichts. Geraete-ID nur als
`shg-` plus sechs Zeichen, Kreisnamen als "Kreis n"; Abwesenheit, Plan und Leerstand nur als ja/nein. Nie: Anlagen-ID,
Anlagenname, Entity-IDs der Zuordnung, Notify-Dienste, Uebernahme-Code, Rueckkehrzeit."""
from __future__ import annotations

import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant

from .const import (
    CODE_RE,
    DATENFEHLER_ARTEN,
    HINWEIS_FELD_KEY,
    HINWEIS_FELD_STUFE,
    INTEGRATION_EVENT_SCHEMA,
    MINDEST_INTEGRATION_RE,
    V5_SCHEMAS,
)

_ZEITPUNKT = re.compile(r"[0-9T:.+\-Z]{1,40}")
_GERAET = re.compile(r"shg-([a-z0-9]{6})[a-z0-9]*")
# Felder je Kreis, die als Code, als Zahl, als Zeitpunkt, als Zahl der Eintraege oder als Zahlen je Code erscheinen.
_KREIS_CODES = ("boost", "hebelsatz", "abo", "zustand", "kaelteschutz")
_KREIS_ZAHLEN = ("raum_soll", "raum_ist", "einlernphase_tag")
_KREIS_ZEITEN = ("testphase_bis", "abo_frist_ende")
_KREIS_ZAHLEN_JE_CODE = ("hebel", "gelernt")
# Was ein Kreis kuenftig ueber Abwesenheit, Plan und Leerstand melden kann: nur ob es gesetzt ist.
_KREIS_JA_NEIN = ("abwesend", "wochenplan", "leerstand")
_MAX_HINWEISE = 20


def _code(wert: object) -> str | None:
    return wert if isinstance(wert, str) and CODE_RE.fullmatch(wert) else None


def _datenfehler(wert: object) -> str | None:
    """Der Datenfehler kommt als Objekt mit Art und Rollen; heraus kommt nur die Art, und nur eine bekannte."""
    art = wert.get("art") if isinstance(wert, dict) else None
    return art if isinstance(art, str) and art in DATENFEHLER_ARTEN else None


def _version(wert: object) -> str | None:
    return wert if isinstance(wert, str) and MINDEST_INTEGRATION_RE.fullmatch(wert) else None


def _zeitpunkt(wert: object) -> str | None:
    return wert if isinstance(wert, str) and _ZEITPUNKT.fullmatch(wert) else None


def _zahl(wert: object) -> int | float | None:
    return wert if isinstance(wert, (int, float)) and not isinstance(wert, bool) else None


def _wahrheit(wert: object) -> bool | None:
    return wert if isinstance(wert, bool) else None


def _anzahl(wert: object) -> int | None:
    return len(wert) if isinstance(wert, list) else None


def _zahlen_je_code(wert: object) -> dict[str, int | float | None]:
    """Zahlen (oder nichts) je Code-Schluessel; jeder andere Schluessel oder Wert faellt weg."""
    if not isinstance(wert, dict):
        return {}
    return {schluessel: eintrag for schluessel, eintrag in wert.items()
            if _code(schluessel) is not None and (eintrag is None or _zahl(eintrag) is not None)}


def _geraet(wert: object) -> str | None:
    """`shg-` plus die ersten sechs Zeichen der Geraete-ID: genug zum Finden, nicht genug zum Nachbilden."""
    treffer = _GERAET.fullmatch(wert) if isinstance(wert, str) else None
    return None if treffer is None else f"shg-{treffer.group(1)}"


def _hinweise(wert: object) -> dict[str, int | bool | None]:
    """Hinweise der Anlage als Zahlen: wie viele Fuehler und Batterien betroffen sind, ob ein manueller Eingriff vorliegt
    (die Entity-IDs dahinter bleiben draussen)."""
    hinweise = wert if isinstance(wert, dict) else {}
    return {"raumfuehler_ausgefallen": _anzahl(hinweise.get("raumfuehler_ausgefallen")),
            "batterie_niedrig": _anzahl(hinweise.get("batterie_niedrig")),
            "manueller_eingriff": bool(hinweise.get("manueller_eingriff"))}


def _geraet_hinweise(wert: object) -> list[dict[str, str | None]]:
    """Schluessel und Stufe der Hinweise des Geraets, ohne deren Text."""
    if not isinstance(wert, list):
        return []
    return [{"key": _code(hinweis.get(HINWEIS_FELD_KEY)), "stufe": _code(hinweis.get(HINWEIS_FELD_STUFE))}
            for hinweis in wert[:_MAX_HINWEISE] if isinstance(hinweis, dict)]


def _kreis(nummer: int, kreis: dict) -> dict[str, Any]:
    ergebnis: dict[str, Any] = {"kreis": nummer, "name": f"Kreis {nummer}"}
    ergebnis.update({feld: _zahl(kreis.get(feld)) for feld in _KREIS_ZAHLEN})
    ergebnis.update({feld: _zeitpunkt(kreis.get(feld)) for feld in _KREIS_ZEITEN})
    ergebnis["befunde"] = _anzahl(kreis.get("befunde"))
    ergebnis.update({feld: _code(kreis.get(feld)) for feld in _KREIS_CODES})
    ergebnis.update({feld: _zahlen_je_code(kreis.get(feld)) for feld in _KREIS_ZAHLEN_JE_CODE})
    ergebnis.update({feld: bool(kreis[feld]) for feld in _KREIS_JA_NEIN if feld in kreis})
    return ergebnis


def _status(coordinator) -> dict[str, Any] | None:
    daten = coordinator.data
    if daten is None:
        return None
    return {
        "schema": _zahl(daten.get("schema")),
        "status": _code(daten.get("status")),
        "grund": _code(daten.get("grund")),
        "geraet_zustand": _code(daten.get("geraet_zustand")),
        "server_verbunden": _wahrheit(daten.get("server_verbunden")),
        "notbetrieb": _wahrheit(daten.get("notbetrieb")),
        "datenfehler": _datenfehler(daten.get("datenfehler")),
        "letzte_serverantwort": _zeitpunkt(daten.get("letzte_serverantwort")),
        "hinweise": _hinweise(daten.get("hinweise")),
        "geraet_hinweise": _geraet_hinweise(daten.get("geraet_hinweise")),
        "kreise": [_kreis(nummer, kreis) for nummer, kreis in sorted(coordinator.kreise.items())],
    }


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    daten = coordinator.data or {}
    return {
        "versionen": {
            "integration": _version(coordinator.version),
            "addon": _version(daten.get("addon_version")),
            "home_assistant": HA_VERSION,
            "integration_schema": INTEGRATION_EVENT_SCHEMA,
            "v5_schemas": list(V5_SCHEMAS),
            "status_schema": coordinator.wunsch_schema,
        },
        "geraet": _geraet(entry.data.get("geraet_id")),
        "waechter": {
            "befund": coordinator.watchdog_status,
            "versionsbefund": coordinator.version_status,
            **coordinator.waechter_stand(),
        },
        "status": _status(coordinator),
    }
