"""Bodies der Einrichtungs-API (V3): ohne jeden Import, damit Contract-Check und E2E die Datei per
Pfad laden und dieselben Anfragen bauen wie der Assistent (config_flow)."""


def auswahl_body(auswahl: dict, tenant_id: str | None) -> dict:
    """Route zuordnung: Kreis, Profil, Hebelsatz; tenant_id nur beim Ersetzen."""
    body = {"kreis": auswahl["kreis"], "profil": auswahl["profil"], "hebelsatz": auswahl["hebelsatz"]}
    if tenant_id:
        body["tenant_id"] = tenant_id
    return body


def anfrage(auswahl: dict, eingaben: dict, *, tenant_id: str | None, ersetzen: bool) -> dict:
    """Routen pruefen, einrichten, neu_konfigurieren. tenant_id None = neu konfigurieren (dieselbe Anlage)."""
    body = {
        **auswahl_body(auswahl, None),
        "rollen": {**eingaben.get("rollen", {}), "room_target": eingaben.get("room_target")},
        "raumfuehler": list(eingaben.get("raumfuehler") or []), "batterien": list(eingaben.get("batterien") or []),
        "benachrichtigung": list(eingaben.get("benachrichtigung") or []),
        "hinweise_aus": list(eingaben.get("hinweise_aus") or []), "raum_soll": eingaben.get("raum_soll"),
    }
    if tenant_id is not None:
        body["tenant_id"] = tenant_id
        if ersetzen:
            body["ersetzen"] = True
    return body
