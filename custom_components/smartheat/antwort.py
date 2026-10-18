"""Verschachtelte Objekte der Antworten der Einrichtungs-API (V3, S3S8b-Vertragsleser): ohne jeden Import, damit der
Contract-Check die Datei per Pfad laedt und dieselbe Kuerzung probiert wie der Client (einrichtung_client). Gleiche
Pfadtabelle wie der Server (`einrichtung/api.py` `_VERSCHACHTELT`); statt der Felder stehen hier die Namen der
Konstanten ohne das Praefix `EINRICHTUNG_` (`const.py`), die der Aufrufer als `felder` mitgibt."""
from typing import Any

#: Je Route Pfad ("*" = jedes Listenelement) und zuletzt der Name der Feldkonstante des Objekts.
VERSCHACHTELT: dict[str, tuple[tuple[str, ...], ...]] = {
    "vorschlag": (("integrationen", "*", "VORSCHLAG_INTEGRATION_FELDER"),
                  ("integrationen", "*", "kreise", "*", "VORSCHLAG_KREIS_FELDER"),
                  ("integrationen", "*", "profile", "*", "VORSCHLAG_PROFIL_FELDER"),
                  ("integrationen", "*", "profile", "*", "hebelsaetze", "*", "VORSCHLAG_HEBELSATZ_FELDER"),
                  ("integrationen", "*", "vorschlag", "VORSCHLAG_AUSWAHL_FELDER")),
    "zuordnung": (("rollen", "*", "ZUORDNUNG_ROLLE_FELDER"), ("raumfuehler", "RAUMFUEHLER_FELDER"),
                  ("room_target", "RAUMFUEHLER_FELDER")),
    "geraet": (("anlagen", "*", "GERAET_ANLAGE_FELDER"), ("inventur", "GERAET_INVENTUR_FELDER")),
}


def _kuerzen(wert: Any, pfad: tuple[str, ...], felder: tuple[str, ...]) -> Any:
    """Kuerzt die Objekte unter `pfad` auf `felder`; Nicht-Objekte, fehlende Teile und falsche Typen bleiben
    unangetastet (der Assistent prueft sie wie bisher), der Filter wirft nie."""
    if not pfad:
        return {k: v for k, v in wert.items() if k in felder} if isinstance(wert, dict) else wert
    schritt, rest = pfad[0], pfad[1:]
    if schritt == "*":
        return [_kuerzen(kind, rest, felder) for kind in wert] if isinstance(wert, list) else wert
    if not isinstance(wert, dict) or schritt not in wert:
        return wert
    return {**wert, schritt: _kuerzen(wert[schritt], rest, felder)}


def felder_aus(konstanten: Any) -> dict[str, tuple[str, ...]]:
    """Die Feldkonstanten `EINRICHTUNG_<Name>` eines Moduls (const.py), die die Pfadtabelle nennt, nach Name ohne
    das Praefix. Fehlt eine, ist das ein AttributeError beim Import des Clients statt ein stiller Ausfall."""
    namen = {pfad[-1] for pfade in VERSCHACHTELT.values() for pfad in pfade}
    return {name: getattr(konstanten, f"EINRICHTUNG_{name}") for name in sorted(namen)}


def verschachtelt(route: str, body: dict, felder: dict[str, tuple[str, ...]]) -> dict:
    """Server -> Integration bleibt tolerant: fremde Felder verschachtelter Objekte fallen weg, fehlende bleiben
    fehlend. `felder`: Name der Konstante ohne `EINRICHTUNG_` -> Felder des Objekts."""
    for *pfad, name in VERSCHACHTELT.get(route, ()):
        body = _kuerzen(body, tuple(pfad), felder[name])
    return body
