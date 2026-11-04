"""Bodies der Einrichtungs-API (V3): ohne jeden Import, damit Contract-Check und E2E die Datei per
Pfad laden und dieselben Anfragen bauen wie der Assistent (config_flow)."""


def auswahl_body(auswahl: dict, tenant_id: str | None) -> dict:
    """Route zuordnung: ein Kreis, Profil, Hebelsatz; tenant_id nur beim Ersetzen."""
    body = {"kreis": auswahl["kreis"], "profil": auswahl["profil"], "hebelsatz": auswahl["hebelsatz"]}
    if tenant_id:
        body["tenant_id"] = tenant_id
    return body


def _raeume(kreis: dict) -> list[dict]:
    """Der Referenzraum (Soll-Quelle) und, wenn Name und Fuehler da sind, ein Kontrollraum."""
    raeume = [{"name": kreis["raum_name"], "rolle": "referenz", "soll_quelle": True,
               "fuehler": list(kreis.get("raumfuehler") or [])}]
    if kreis.get("kontroll_name") and kreis.get("kontroll_fuehler"):
        raeume.append({"name": kreis["kontroll_name"], "rolle": "kontroll", "fuehler": list(kreis["kontroll_fuehler"])})
    return raeume


def kreis_body(kreis: dict) -> dict:
    """Ein Kreis der Anfrage: Nummer, erkannter Kreis, Rollen samt Soll-Entity, Raeume, Solltemperatur, Abo."""
    body = {"nummer": kreis["nummer"], "kreis": kreis["kreis"],
            "rollen": {**kreis.get("rollen", {}), "room_target": kreis.get("room_target")},
            "raeume": _raeume(kreis), "raum_soll": kreis.get("raum_soll")}
    if kreis.get("abo_id"):
        body["abo_id"] = kreis["abo_id"]
    return body


def anfrage(auswahl: dict, kreise: list[dict], eingaben: dict, *, tenant_id: str | None, ersetzen: bool) -> dict:
    """Routen pruefen, einrichten, neu_konfigurieren. `auswahl` nennt Profil und Hebelsatz der Anlage, `kreise` die
    Eingaben je Kreis in der Reihenfolge der Nummern. tenant_id None = neu konfigurieren (dieselbe Anlage)."""
    body = {
        "profil": auswahl["profil"], "hebelsatz": auswahl["hebelsatz"], "kreise": [kreis_body(k) for k in kreise],
        "batterien": list(eingaben.get("batterien") or []),
        "benachrichtigung": list(eingaben.get("benachrichtigung") or []),
        "hinweise_aus": list(eingaben.get("hinweise_aus") or []),
    }
    if tenant_id is not None:
        body["tenant_id"] = tenant_id
        if ersetzen:
            body["ersetzen"] = True
    return body
