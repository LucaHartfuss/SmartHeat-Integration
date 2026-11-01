"""V3-Bodies der Einrichtungs-API: anfrage.py baut nur deklarierte Felder."""
import pytest

from custom_components.smartheat import anfrage, vertrag_pin
from custom_components.smartheat.const import (
    EINRICHTUNG_ANFRAGE_FELDER,
    EINRICHTUNG_ANFRAGE_KREIS_FELDER,
    EINRICHTUNG_ANFRAGE_RAUM_FELDER,
    EINRICHTUNG_GERAET_ANLAGE_FELDER,
)

AUSWAHL = {"profil": "p", "hebelsatz": "vaillant_vrc720"}
ZUORDNUNG = {"kreis": "k", "profil": "p", "hebelsatz": "vaillant_vrc720"}
KREIS = {"nummer": 1, "kreis": "k", "rollen": {"curve": "number.c"}, "room_target": "climate.r", "raum_name": "Wohnzimmer",
         "raumfuehler": ["sensor.r"], "kontroll_name": "", "kontroll_fuehler": [], "raum_soll": 21.0, "abo_id": None}
EINGABEN = {"batterien": [], "benachrichtigung": [], "hinweise_aus": []}


def _passt(route, body):
    pflicht, optional = EINRICHTUNG_ANFRAGE_FELDER[route]
    return set(pflicht) <= set(body) <= set(pflicht) | set(optional)


def _kreis_passt(kreis):
    pflicht, optional = EINRICHTUNG_ANFRAGE_KREIS_FELDER
    return set(pflicht) <= set(kreis) <= set(pflicht) | set(optional)


def _raum_passt(raum):
    pflicht, optional = EINRICHTUNG_ANFRAGE_RAUM_FELDER
    return set(pflicht) <= set(raum) <= set(pflicht) | set(optional)


def test_einrichten_und_pruefen_beim_ersetzen():
    body = anfrage.anfrage(AUSWAHL, [KREIS], EINGABEN, tenant_id="t", ersetzen=True)
    assert body["tenant_id"] == "t" and body["ersetzen"] is True
    assert _passt("einrichten", body) and _passt("pruefen", body)


def test_neu_konfigurieren_ohne_anlage():
    body = anfrage.anfrage(AUSWAHL, [KREIS], EINGABEN, tenant_id=None, ersetzen=False)
    assert "tenant_id" not in body and "ersetzen" not in body and _passt("neu_konfigurieren", body)


def test_kreis_traegt_rollen_und_einen_referenzraum_mit_der_soll_quelle():
    kreis = anfrage.anfrage(AUSWAHL, [KREIS], EINGABEN, tenant_id="t", ersetzen=False)["kreise"][0]
    assert _kreis_passt(kreis) and kreis["nummer"] == 1 and kreis["kreis"] == "k" and kreis["raum_soll"] == 21.0
    assert kreis["rollen"] == {"curve": "number.c", "room_target": "climate.r"}
    assert kreis["raeume"] == [{"name": "Wohnzimmer", "rolle": "referenz", "soll_quelle": True, "fuehler": ["sensor.r"]}]
    assert all(_raum_passt(raum) for raum in kreis["raeume"]) and "abo_id" not in kreis


def test_zweiter_raum_mit_der_rolle_kontroll_und_ohne_soll_quelle():
    kreis = {**KREIS, "kontroll_name": "Bad", "kontroll_fuehler": ["sensor.bad"]}
    raeume = anfrage.anfrage(AUSWAHL, [kreis], EINGABEN, tenant_id="t", ersetzen=False)["kreise"][0]["raeume"]
    assert raeume[1] == {"name": "Bad", "rolle": "kontroll", "fuehler": ["sensor.bad"]}
    assert all(_raum_passt(raum) for raum in raeume)


def test_kontrollraum_ohne_fuehler_oder_name_entfaellt():
    for zusatz in ({"kontroll_name": "Bad"}, {"kontroll_fuehler": ["sensor.bad"]}):
        kreis = {**KREIS, **zusatz}
        assert len(anfrage.anfrage(AUSWAHL, [kreis], EINGABEN, tenant_id="t", ersetzen=False)["kreise"][0]["raeume"]) == 1


def test_mehrere_kreise_behalten_ihre_reihenfolge_und_das_abo():
    zweiter = {**KREIS, "nummer": 2, "kreis": "k2", "abo_id": "abo-1"}
    kreise = anfrage.anfrage(AUSWAHL, [KREIS, zweiter], EINGABEN, tenant_id="t", ersetzen=False)["kreise"]
    assert [k["nummer"] for k in kreise] == [1, 2] and kreise[1]["abo_id"] == "abo-1" and _kreis_passt(kreise[1])


def test_zuordnung():
    assert _passt("zuordnung", anfrage.auswahl_body(ZUORDNUNG, None))
    assert anfrage.auswahl_body(ZUORDNUNG, "t")["tenant_id"] == "t"


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
