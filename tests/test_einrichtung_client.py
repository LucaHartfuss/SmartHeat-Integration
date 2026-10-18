"""Spec 5c 2.1: Client der Einrichtungs-API (Token nur im Speicher, Fehler je Status)."""
import logging

import aiohttp
import pytest
from aiohttp import web

from custom_components.smartheat import einrichtung_client
from custom_components.smartheat.einrichtung_client import (
    ApiError,
    CannotConnect,
    Conflict,
    EinrichtungClient,
    InvalidAuth,
    InvalidResponse,
    NotFound,
    Rejected,
    ServerError,
    TokenUngueltig,
    TooMany,
)

pytestmark = pytest.mark.usefixtures("socket_enabled")
P = "/einrichtung/v1"


async def _client(aiohttp_client, routes, token="t"):
    app = web.Application()
    for method, path, handler in routes:
        app.router.add_route(method, f"{P}{path}", handler)
    client = EinrichtungClient(await aiohttp_client(app), "")
    client.token = token
    return client


def _antwort(status, body):
    async def handler(request):
        return web.json_response(body, status=status)

    return handler


async def test_login_keeps_the_token_in_memory_and_sends_it(aiohttp_client):
    seen = {}

    async def login(request):
        seen["login_auth"] = request.headers.get("Authorization")
        seen["body"] = await request.json()
        return web.json_response({"token": "t-123", "gueltig_bis": "x"})

    async def geraet(request):
        seen["auth"] = request.headers.get("Authorization")
        return web.json_response({"device_id": "shg-a"})

    client = await _client(aiohttp_client, [("POST", "/anmelden", login), ("GET", "/geraet", geraet)], token=None)
    await client.anmelden("a@b.de", "pw")
    assert client.token == "t-123" and seen["body"] == {"email": "a@b.de", "password": "pw"}
    assert (await client.geraet())["device_id"] == "shg-a"
    assert seen["auth"] == "Bearer t-123" and seen["login_auth"] is None


async def test_login_without_token_is_invalid_response(aiohttp_client):
    client = await _client(aiohttp_client, [("POST", "/anmelden", _antwort(200, {"gueltig_bis": "x"}))], token=None)
    with pytest.raises(InvalidResponse):
        await client.anmelden("a@b.de", "pw")
    assert client.token is None


async def test_login_401_is_invalid_auth_but_not_token_gone(aiohttp_client):
    client = await _client(aiohttp_client, [("POST", "/anmelden", _antwort(401, {"error": "Ungueltige Zugangsdaten"}))],
                           token=None)
    with pytest.raises(InvalidAuth) as caught:
        await client.anmelden("a@b.de", "pw")
    assert not isinstance(caught.value, TokenUngueltig) and caught.value.text == "Ungueltige Zugangsdaten"


async def test_login_lockout_is_too_many_with_text(aiohttp_client):
    text = "Zu viele Fehlversuche, bitte kurz warten"
    client = await _client(aiohttp_client, [("POST", "/anmelden", _antwort(429, {"error": text}))], token=None)
    with pytest.raises(TooMany) as caught:
        await client.anmelden("a@b.de", "pw")
    assert caught.value.text == text and caught.value.status == 429


@pytest.mark.parametrize("status, error", [(404, NotFound), (409, Conflict), (429, TooMany), (500, ServerError),
                                           (503, ServerError), (403, ApiError)])
async def test_status_codes_map_to_errors_with_server_text(aiohttp_client, status, error):
    client = await _client(aiohttp_client, [("GET", "/geraet", _antwort(status, {"error": "Text"}))])
    with pytest.raises(error) as caught:
        await client.geraet()
    assert type(caught.value) is error and caught.value.text == "Text" and caught.value.status == status


async def test_401_on_a_token_route_is_token_gone(aiohttp_client):
    client = await _client(aiohttp_client, [("GET", "/geraet", _antwort(401, {"error": "nicht angemeldet"}))])
    with pytest.raises(InvalidAuth) as caught:
        await client.geraet()
    assert isinstance(caught.value, TokenUngueltig)


@pytest.mark.parametrize("status", [401, 404, 405, 409, 429, 400, 500, 502])
async def test_non_json_error_bodies_map_by_status_without_crashing(aiohttp_client, status):
    async def handler(request):
        return web.Response(text="<html>nope</html>", status=status, content_type="text/html")

    client = await _client(aiohttp_client, [("GET", "/geraet", handler)])
    with pytest.raises(ApiError) as caught:
        await client.geraet()
    assert caught.value.status == status and caught.value.text == ""


async def test_unrouted_path_gives_a_status_error_not_a_crash(aiohttp_client):
    client = await _client(aiohttp_client, [])  # aiohttp antwortet 404 mit Text/HTML
    with pytest.raises(NotFound):
        await client.vorschlag()


async def test_error_text_is_capped_and_must_be_a_string(aiohttp_client):
    lang = await _client(aiohttp_client, [("GET", "/geraet", _antwort(409, {"error": "x" * 5000}))])
    with pytest.raises(Conflict) as caught:
        await lang.geraet()
    assert len(caught.value.text) == einrichtung_client.ERROR_TEXT_MAX
    kein_text = await _client(aiohttp_client, [("GET", "/geraet", _antwort(409, {"error": {"a": 1}}))])
    with pytest.raises(Conflict) as caught:
        await kein_text.geraet()
    assert caught.value.text == ""


async def test_400_carries_text_and_field_errors(aiohttp_client):
    body = {"error": "Zuordnung unvollständig", "fehler": {"raumfuehler": "required"}}
    client = await _client(aiohttp_client, [("POST", "/geraet/einrichten", _antwort(400, body))])
    with pytest.raises(Rejected) as caught:
        await client.einrichten({})
    assert caught.value.text == "Zuordnung unvollständig" and caught.value.fehler == {"raumfuehler": "required"}


async def test_400_without_field_errors(aiohttp_client):
    client = await _client(aiohttp_client, [("POST", "/geraete/uebernehmen", _antwort(400, {"error": "Falscher Code"}))])
    with pytest.raises(Rejected) as caught:
        await client.uebernehmen("shg-a", "ABCD")
    assert caught.value.text == "Falscher Code" and caught.value.fehler == {}


async def test_200_with_non_json_body_is_invalid_response(aiohttp_client):
    async def handler(request):
        return web.Response(text="<html>captive portal</html>", content_type="text/html")

    client = await _client(aiohttp_client, [("GET", "/geraet", handler)])
    with pytest.raises(InvalidResponse):
        await client.geraet()


async def test_200_with_a_list_is_invalid_response(aiohttp_client):
    client = await _client(aiohttp_client, [("GET", "/geraet", _antwort(200, [1, 2]))])
    with pytest.raises(InvalidResponse):
        await client.geraet()


@pytest.fixture
async def unreachable():
    """Client auf einen Port ohne Server (der TestClient von aiohttp nimmt keine absoluten URLs)."""
    async with aiohttp.ClientSession() as session:
        yield EinrichtungClient(session, "http://127.0.0.1:1")


async def test_unreachable_server_is_cannot_connect(unreachable):
    with pytest.raises(CannotConnect):
        await unreachable.anmelden("a@b.de", "pw")


async def test_timeout_is_cannot_connect(aiohttp_client, monkeypatch):
    import asyncio

    async def langsam(request):
        await asyncio.sleep(5)
        return web.json_response({})

    monkeypatch.setattr(einrichtung_client, "REQUEST_TIMEOUT_SECONDS", 0.05)
    client = await _client(aiohttp_client, [("GET", "/geraet", langsam)])
    with pytest.raises(CannotConnect):
        await client.geraet()


async def test_logout_never_raises(aiohttp_client, unreachable):
    client = await _client(aiohttp_client, [("POST", "/abmelden", _antwort(500, {"error": "x"}))])
    await client.abmelden()
    assert client.token is None
    unreachable.token = "t"
    await unreachable.abmelden()
    assert unreachable.token is None


async def test_logout_204_revokes_and_forgets_the_token(aiohttp_client):
    seen = {}

    async def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        return web.Response(status=204)

    client = await _client(aiohttp_client, [("POST", "/abmelden", handler)])
    await client.abmelden()
    assert seen["auth"] == "Bearer t" and client.token is None


async def test_job_route_fills_the_id(aiohttp_client):
    async def handler(request):
        return web.json_response({"zustand": "fertig", "job_id": request.match_info["job_id"]})

    client = await _client(aiohttp_client, [("GET", "/geraet/auftraege/{job_id}", handler)])
    assert (await client.auftrag("j1"))["job_id"] == "j1"
    assert (await client.auftrag("a/b c"))["job_id"] == "a/b c"


async def test_auftrag_401_after_accepted_removal_is_token_gone(aiohttp_client):
    client = await _client(aiohttp_client, [("GET", "/geraet/auftraege/{job_id}",
                                             _antwort(401, {"error": "nicht angemeldet"}))])
    with pytest.raises(TokenUngueltig):
        await client.auftrag("j1")


async def test_zuordnung_sends_tenant_id_only_when_given(aiohttp_client):
    bodies = []

    async def handler(request):
        bodies.append(await request.json())
        return web.json_response({"rollen": []})

    client = await _client(aiohttp_client, [("POST", "/geraet/zuordnung", handler)])
    assert await client.zuordnung("circuit_0", "vaillant", "vaillant_vrc720") == {"rollen": []}
    await client.zuordnung("circuit_0", "vaillant", "vaillant_vrc720", tenant_id="client1")
    assert bodies == [{"kreis": "circuit_0", "profil": "vaillant", "hebelsatz": "vaillant_vrc720"},
                      {"kreis": "circuit_0", "profil": "vaillant", "hebelsatz": "vaillant_vrc720",
                       "tenant_id": "client1"}]


async def test_uebernehmen_sends_the_code_only_when_given(aiohttp_client):
    bodies = []

    async def handler(request):
        bodies.append(await request.json())
        return web.json_response({"device_id": "shg-a"})

    client = await _client(aiohttp_client, [("POST", "/geraete/uebernehmen", handler)])
    await client.uebernehmen("shg-a", "ABCD-EFGH")
    await client.uebernehmen("shg-a", None)
    await client.uebernehmen("shg-a", "")
    assert bodies == [{"device_id": "shg-a", "code": "ABCD-EFGH"}, {"device_id": "shg-a"}, {"device_id": "shg-a"}]


async def test_ids_come_back_as_strings_and_missing_ones_are_invalid(aiohttp_client):
    async def inventur(request):
        return web.json_response({"command_id": "c-1"})

    async def einrichten(request):
        assert await request.json() == {"tenant_id": "x"}
        return web.json_response({"job_id": "j-1"})

    async def neu(request):
        return web.json_response({})

    client = await _client(aiohttp_client, [("POST", "/geraet/inventur", inventur),
                                            ("POST", "/geraet/einrichten", einrichten),
                                            ("POST", "/geraet/neu-konfigurieren", neu)])
    assert await client.inventur() == "c-1"
    assert await client.einrichten({"tenant_id": "x"}) == "j-1"
    with pytest.raises(InvalidResponse):
        await client.neu_konfigurieren({})


async def test_remaining_routes_use_their_method_and_path(aiohttp_client):
    async def vorschlag(request):
        return web.json_response({"integrationen": []})

    async def pruefen(request):
        return web.json_response({"fehler": {}, "warnungen": {"write_role_unmatched": ["x"]}})

    async def entfernen(request):
        return web.json_response({"freigegeben": True})

    client = await _client(aiohttp_client, [("GET", "/geraet/vorschlag", vorschlag),
                                            ("POST", "/geraet/pruefen", pruefen),
                                            ("POST", "/geraet/entfernen", entfernen)])
    assert await client.vorschlag() == {"integrationen": []}
    assert (await client.pruefen({}))["warnungen"] == {"write_role_unmatched": ["x"]}
    assert await client.entfernen() == {"freigegeben": True}


async def test_secrets_never_reach_the_log(aiohttp_client, unreachable, caplog):
    caplog.set_level(logging.DEBUG)
    zugangs = [("POST", "/anmelden", _antwort(200, {"token": "tok-geheim-9"})),
               ("POST", "/geraete/uebernehmen", _antwort(400, {"error": "Falscher Code"})),
               ("GET", "/geraet", _antwort(404, {"error": "unbekanntes Gerät"})),
               ("POST", "/abmelden", _antwort(500, {"error": "x"}))]
    client = await _client(aiohttp_client, zugangs, token=None)
    await client.anmelden("a@b.de", "pw-geheim-7")
    with pytest.raises(Rejected):
        await client.uebernehmen("shg-a", "code-geheim-3")
    with pytest.raises(NotFound):
        await client.geraet()
    await client.abmelden()
    unreachable.token = "tok-geheim-9"
    with pytest.raises(CannotConnect):
        await unreachable.geraet()
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("custom_components"))
    assert ours  # es wird etwas geloggt
    for secret in ("tok-geheim-9", "pw-geheim-7", "code-geheim-3", "Bearer"):
        assert secret not in caplog.text


@pytest.mark.parametrize("status", [301, 302, 307, 308])
async def test_redirects_are_not_followed_and_never_resend_the_password(aiohttp_client, status):
    seen = {"target": 0}

    async def login(request):
        return web.Response(status=status, headers={"Location": f"{P}/ziel"}, text="Ziel: geheim-im-body")

    async def ziel(request):
        seen["target"] += 1
        return web.json_response({"token": "t"})

    client = await _client(aiohttp_client, [("POST", "/anmelden", login), ("POST", "/ziel", ziel)], token=None)
    with pytest.raises(ApiError) as caught:
        await client.anmelden("a@b.de", "pw")
    assert type(caught.value) is ApiError and caught.value.status == status and caught.value.text == ""
    assert seen["target"] == 0 and client.token is None


async def test_unbekannte_antwortfelder_fallen_weg(aiohttp_client):
    client = await _client(aiohttp_client, [("GET", "/geraet", _antwort(200, {"device_id": "d", "neu": 1}))])
    assert await client.geraet() == {"device_id": "d"}


async def test_nicht_deklariertes_anfragefeld_ist_ein_programmfehler(aiohttp_client):
    client = await _client(aiohttp_client, [])
    with pytest.raises(ValueError):
        await client.pruefen({"kreis": "k", "profil": "p", "hebelsatz": "h", "rollen": {}, "raumfuehler": [],
                              "erfunden": 1})


async def test_verschachtelte_fremde_felder_fallen_weg(aiohttp_client):
    integration = {"plattform": "p", "label": "l", "hinweis": None, "neu": 1,
                   "kreise": [{"schluessel": "s", "label": "k", "neu": 1}],
                   "profile": [{"profile_id": "x", "label": "l", "erzeuger_typ": "e", "verteilsystem": "v",
                                "shift_lever": None, "hebelsaetze": [{"id": "h", "hebel": [], "neu": 1}], "neu": 1}],
                   "vorschlag": {"kreis": None, "profil": None, "neu": 1}}
    client = await _client(aiohttp_client, [("GET", "/geraet/vorschlag",
                                             _antwort(200, {"integrationen": [integration], "neu": 1}))])
    antwort = await client.vorschlag()
    eintrag = antwort["integrationen"][0]
    # Literale aus V3
    assert set(antwort) == {"integrationen"}
    assert set(eintrag) == {"plattform", "label", "hinweis", "kreise", "profile", "vorschlag"}
    assert set(eintrag["kreise"][0]) == {"schluessel", "label"}
    assert set(eintrag["profile"][0]) == {"profile_id", "label", "erzeuger_typ", "verteilsystem", "shift_lever",
                                          "hebelsaetze"}
    assert set(eintrag["profile"][0]["hebelsaetze"][0]) == {"id", "hebel"}
    assert set(eintrag["vorschlag"]) == {"kreis", "profil"}


async def test_zuordnung_und_geraet_werden_verschachtelt_gefiltert(aiohttp_client):
    rolle = {"rolle": "r", "pflicht": True, "vorschlag": None, "herkunft": None, "kandidaten": [], "neu": 1}
    raum = {"kandidaten": [], "vorschlag": None, "neu": 1}
    anlage = {"tenant_id": "t", "aktiv": True, "belegt": False, "ersetzbar": False, "neu": 1}
    inventur = {"zustand": "fertig", "command_id": "c", "zeit": "z", "fehler": None, "neu": 1}
    konfiguration = {"profil": "p", "hebel": [], "shift_lever": None, "unbekannt": 1}
    client = await _client(aiohttp_client, [
        ("POST", "/geraet/zuordnung", _antwort(200, {"rollen": [rolle], "raumfuehler": raum, "room_target": raum})),
        ("GET", "/geraet", _antwort(200, {"anlagen": [anlage], "inventur": inventur,
                                           "konfiguration": konfiguration}))])
    zuordnung = await client.zuordnung("k", "p", "h")
    assert set(zuordnung["rollen"][0]) == {"rolle", "pflicht", "vorschlag", "herkunft", "kandidaten"}
    assert set(zuordnung["raumfuehler"]) == set(zuordnung["room_target"]) == {"kandidaten", "vorschlag"}
    sicht = await client.geraet()
    assert set(sicht["anlagen"][0]) == {"tenant_id", "aktiv", "belegt", "ersetzbar"}
    assert set(sicht["inventur"]) == {"zustand", "command_id", "zeit", "fehler"}
    assert sicht["konfiguration"] == konfiguration  # Auszug des V1-Dokuments: ungekuerzt


@pytest.mark.parametrize("kaputt", [
    {"integrationen": "kein Array"},
    {"integrationen": [None, 3, "x"]},
    {"integrationen": [{"kreise": "kein Array", "profile": [None], "vorschlag": "x"}]},
    {"integrationen": [{"profile": [{"hebelsaetze": 5}]}]},
    {},
])
async def test_verschachtelte_nicht_objekte_bleiben_unangetastet(aiohttp_client, kaputt):
    """Der Filter wirft nie und ersetzt nichts: Nicht-Objekte und fehlende Teile bleiben, wie sie sind (der Assistent
    prueft sie wie bisher)."""
    client = await _client(aiohttp_client, [("GET", "/geraet/vorschlag", _antwort(200, kaputt))])
    assert await client.vorschlag() == kaputt


async def test_geraet_ohne_inventur_bleibt_ohne_inventur(aiohttp_client):
    client = await _client(aiohttp_client, [("GET", "/geraet", _antwort(200, {"inventur": None, "anlagen": []}))])
    assert await client.geraet() == {"anlagen": [], "inventur": None}


def test_felder_aus_nimmt_genau_die_konstanten_der_pfadtabelle_auf():
    from custom_components.smartheat import antwort

    namen = {pfad[-1] for pfade in antwort.VERSCHACHTELT.values() for pfad in pfade}
    assert set(einrichtung_client._OBJEKT_FELDER) == namen
    assert set(antwort.VERSCHACHTELT) <= set(einrichtung_client.EINRICHTUNG_ANTWORT_FELDER)
    assert all(einrichtung_client._OBJEKT_FELDER[n] == getattr(einrichtung_client.const, f"EINRICHTUNG_{n}")
               for n in namen)


# Bruchtest (K2) je Route: die Felder der obersten Ebene als Literale aus V3 (nicht ueber die Konstanten). Die
# Antwort traegt zusaetzlich ein fremdes Feld, das der Filter verwirft; fehlende Felder bleiben fehlend.
_OBERSTE_EBENE = [
    ("anmelden", {"token", "gueltig_bis"}),
    ("uebernehmen", {"device_id"}),
    ("geraet", {"device_id", "host", "eingerichtet", "tenant_id", "anlagen", "inventur", "auftrag", "soll_bereich",
                "konfiguration", "raum_soll"}),
    ("inventur", {"command_id"}),
    ("vorschlag", {"integrationen"}),
    ("zuordnung", {"kreis", "profil", "hebelsatz", "rollen", "raumfuehler", "room_target", "batterie_je_entity",
                   "benachrichtigung", "hinweise", "raum_soll", "abfrage_s", "hinweis"}),
    ("pruefen", {"fehler", "warnungen"}),
    ("einrichten", {"job_id"}),
    ("neu_konfigurieren", {"job_id"}),
    ("entfernen", {"freigegeben", "job_id"}),
    ("auftrag", {"job_id", "art", "zustand", "fehler", "ergebnis", "seit"}),
]


@pytest.mark.parametrize("route, felder", _OBERSTE_EBENE, ids=[route for route, _ in _OBERSTE_EBENE])
async def test_filter_haelt_auf_oberster_ebene_genau_die_felder_aus_v3(aiohttp_client, route, felder):
    methode, pfad = einrichtung_client.EINRICHTUNG_ROUTEN[route]
    voll = {**dict.fromkeys(sorted(felder)), "fremd": 1}
    client = await _client(aiohttp_client, [(methode, pfad.replace("<job_id>", "{job_id}"), _antwort(200, voll))])
    kwargs = {"job_id": "j"} if route == "auftrag" else {}
    assert set(await client._dict(route, **kwargs)) == felder


# --- Randfaelle des Clients (Testoffensive T2, F-ha-host-37) -----------------------------------------------------

_FEHLT = object()


@pytest.mark.parametrize("wert", [7, "", None, ["c-1"], _FEHLT], ids=["zahl", "leer", "null", "liste", "fehlt"])
@pytest.mark.parametrize("route, feld, aufruf", [
    ("inventur", "command_id", lambda client: client.inventur()),
    ("einrichten", "job_id", lambda client: client.einrichten({"tenant_id": "x"})),
])
async def test_id_mit_falschem_typ_leer_oder_fehlend_ist_invalid_response(aiohttp_client, route, feld, aufruf, wert):
    pfad = einrichtung_client.EINRICHTUNG_ROUTEN[route][1]
    body = {} if wert is _FEHLT else {feld: wert}
    client = await _client(aiohttp_client, [("POST", pfad, _antwort(200, body))])
    with pytest.raises(InvalidResponse) as caught:
        await aufruf(client)
    assert feld in caught.value.text and caught.value.status is None


@pytest.mark.parametrize("status", [201, 202, 204])
async def test_erfolgsstatus_ausser_200_ist_ein_fehler_mit_dem_status(aiohttp_client, status):
    """Heutiges Verhalten: nur 200 gilt als Erfolg (der Server antwortet nie anders); 201/202/204 werden wie jeder
    unbekannte Status zum `ApiError` mit Status, nicht zu einer Antwort."""
    client = await _client(aiohttp_client, [("GET", "/geraet", _antwort(status, {"device_id": "d"}))])
    with pytest.raises(ApiError) as caught:
        await client.geraet()
    assert type(caught.value) is ApiError and caught.value.status == status and caught.value.text == ""


@pytest.mark.parametrize("status", [201, 202])
async def test_erfolgsstatus_ausser_200_gilt_auch_fuer_ids(aiohttp_client, status):
    pfad = einrichtung_client.EINRICHTUNG_ROUTEN["einrichten"][1]
    client = await _client(aiohttp_client, [("POST", pfad, _antwort(status, {"job_id": "j-1"}))])
    with pytest.raises(ApiError) as caught:
        await client.einrichten({"tenant_id": "x"})
    assert caught.value.status == status


@pytest.mark.parametrize("fehler", [["raumfuehler"], "raumfuehler", 7, None, True],
                         ids=["liste", "text", "zahl", "null", "bool"])
async def test_400_mit_fehler_ohne_objekt_behaelt_den_text_und_hat_keine_feldfehler(aiohttp_client, fehler):
    """5c-I-11: `fehler` kein Objekt -> leere Feldfehler, der Text des Servers bleibt, kein Absturz."""
    body = {"error": "Zuordnung unvollständig", "fehler": fehler}
    client = await _client(aiohttp_client, [("POST", "/geraet/einrichten", _antwort(400, body))])
    with pytest.raises(Rejected) as caught:
        await client.einrichten({})
    assert caught.value.text == "Zuordnung unvollständig" and caught.value.fehler == {}
    assert caught.value.status == 400


async def test_400_mit_fehlerobjekt_macht_schluessel_und_werte_zu_text(aiohttp_client):
    body = {"error": "x", "fehler": {"raumfuehler": "required", "zahl": 3}}
    client = await _client(aiohttp_client, [("POST", "/geraet/einrichten", _antwort(400, body))])
    with pytest.raises(Rejected) as caught:
        await client.einrichten({})
    assert caught.value.fehler == {"raumfuehler": "required", "zahl": "3"}
