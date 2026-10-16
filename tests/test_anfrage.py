"""V3-Bodies der Einrichtungs-API (Spec Stufe 3 S5 4, A5-33): anfrage.py baut nur deklarierte Felder."""
from custom_components.smartheat import anfrage
from custom_components.smartheat.const import (
    EINRICHTUNG_ANFRAGE_FELDER,
    EINRICHTUNG_GERAET_ANLAGE_FELDER,
    EINRICHTUNG_GERAET_INVENTUR_FELDER,
    EINRICHTUNG_GERAET_KONFIGURATION_ZUSATZ,
    EINRICHTUNG_VORSCHLAG_AUSWAHL_FELDER,
    EINRICHTUNG_VORSCHLAG_HEBELSATZ_FELDER,
    EINRICHTUNG_VORSCHLAG_INTEGRATION_FELDER,
    EINRICHTUNG_VORSCHLAG_KREIS_FELDER,
    EINRICHTUNG_VORSCHLAG_PROFIL_FELDER,
)

AUSWAHL = {"kreis": "k", "profil": "p", "hebelsatz": "vaillant_vrc720"}
EINGABEN = {"rollen": {"curve": "number.c"}, "room_target": "climate.r", "raumfuehler": ["sensor.r"],
            "batterien": [], "benachrichtigung": [], "hinweise_aus": [], "raum_soll": 21.0}


def _passt(route, body):
    pflicht, optional = EINRICHTUNG_ANFRAGE_FELDER[route]
    return set(pflicht) <= set(body) <= set(pflicht) | set(optional)


def test_einrichten_und_pruefen_beim_ersetzen():
    body = anfrage.anfrage(AUSWAHL, EINGABEN, tenant_id="t", ersetzen=True)
    assert body["tenant_id"] == "t" and body["ersetzen"] is True
    assert _passt("einrichten", body) and _passt("pruefen", body)


def test_neu_konfigurieren_ohne_anlage():
    body = anfrage.anfrage(AUSWAHL, EINGABEN, tenant_id=None, ersetzen=False)
    assert "tenant_id" not in body and "ersetzen" not in body and _passt("neu_konfigurieren", body)


def test_zuordnung():
    assert _passt("zuordnung", anfrage.auswahl_body(AUSWAHL, None))
    assert anfrage.auswahl_body(AUSWAHL, "t")["tenant_id"] == "t"


def test_modul_importiert_kein_home_assistant():
    import ast
    import inspect
    baum = ast.parse(inspect.getsource(anfrage))
    importe = [n for n in ast.walk(baum) if isinstance(n, ast.Import | ast.ImportFrom)]
    assert importe == []  # Contract-Check und E2E laden die Datei per Pfad


def test_gelesene_felder_der_geraeteantwort_sind_deklariert():
    """Felder, die config_flow aus geraet.anlagen[], geraet.inventur und geraet.konfiguration liest: ein Umbenennen
    auf dem Server faellt ueber Contract-Check 62 und diesen Test auf."""
    assert {"tenant_id", "aktiv", "altpfad", "belegt", "ersetzbar"} <= set(EINRICHTUNG_GERAET_ANLAGE_FELDER)
    assert {"zustand", "fehler"} <= set(EINRICHTUNG_GERAET_INVENTUR_FELDER)
    assert {"hebel", "shift_lever"} <= set(EINRICHTUNG_GERAET_KONFIGURATION_ZUSATZ)


def test_gelesene_felder_des_vorschlags_sind_deklariert():
    """Felder, die config_flow aus den Integrationseintraegen von `vorschlag` liest (async_step_integration, _system,
    _hebelsatz): ein Umbenennen auf dem Server faellt ueber Contract-Check 62 und diesen Test auf."""
    assert {"plattform", "label", "hinweis", "kreise", "profile", "vorschlag"} <= set(
        EINRICHTUNG_VORSCHLAG_INTEGRATION_FELDER)
    assert {"schluessel", "label"} <= set(EINRICHTUNG_VORSCHLAG_KREIS_FELDER)
    assert {"profile_id", "label", "hebelsaetze"} <= set(EINRICHTUNG_VORSCHLAG_PROFIL_FELDER)
    assert {"id"} <= set(EINRICHTUNG_VORSCHLAG_HEBELSATZ_FELDER)
    assert {"kreis", "profil"} <= set(EINRICHTUNG_VORSCHLAG_AUSWAHL_FELDER)
