"""V3-Bodies der Einrichtungs-API (Spec Stufe 3 S5 4, A5-33): anfrage.py baut nur deklarierte Felder."""
import pytest

from custom_components.smartheat import anfrage, vertrag_pin
from custom_components.smartheat.const import (
    EINRICHTUNG_ANFRAGE_FELDER,
    EINRICHTUNG_GERAET_ANLAGE_FELDER,
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


def test_vertrag_pin_importiert_kein_home_assistant():
    import ast
    import inspect
    importe = [n for n in ast.walk(ast.parse(inspect.getsource(vertrag_pin))) if isinstance(n, ast.Import | ast.ImportFrom)]
    assert [n.module for n in importe] == ["collections.abc"]


def test_pin_wirft_bei_fremdem_feld():
    """Liest der Assistent ein Feld, das der Vertrag nicht nennt, scheitert der Import mit dem Feldnamen."""
    with pytest.raises(RuntimeError, match="name"):
        vertrag_pin.enthalten("modul", {"ANLAGE": (EINRICHTUNG_GERAET_ANLAGE_FELDER, ("tenant_id", "name"))})
    vertrag_pin.enthalten("modul", {"ANLAGE": (EINRICHTUNG_GERAET_ANLAGE_FELDER, ("tenant_id", "aktiv"))})


def test_config_flow_pinnt_seine_gelesenen_felder():
    """Der Import von config_flow gelingt (Pin gegen die Konstanten), und jedes gepinnte Objekt ist belegt."""
    from custom_components.smartheat import config_flow, einrichtung_client
    for modul in (config_flow, einrichtung_client):
        assert modul._VERTRAG_GEPINNT
        assert all(literal for _, literal in modul._VERTRAG_GEPINNT.values())
        vertrag_pin.enthalten(modul.__name__, modul._VERTRAG_GEPINNT)
    # ein gepinntes Feld, das der Vertrag nicht (mehr) nennt, faellt auf
    kaputt = {**config_flow._VERTRAG_GEPINNT, "ANTWORT_FELDER[entfernen]": (("job_id",), ("freigegeben", "job_id"))}
    with pytest.raises(RuntimeError, match="freigegeben"):
        vertrag_pin.enthalten("config_flow", kaputt)
