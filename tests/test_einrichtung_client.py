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
        return web.json_response({"zustand": "fertig", "job": request.match_info["job_id"]})

    client = await _client(aiohttp_client, [("GET", "/geraet/auftraege/{job_id}", handler)])
    assert (await client.auftrag("j1"))["job"] == "j1"
    assert (await client.auftrag("a/b c"))["job"] == "a/b c"


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
        return web.json_response({"kreise": []})

    async def pruefen(request):
        return web.json_response({"fehler": {}, "warnungen": {"write_role_unmatched": ["x"]}})

    async def entfernen(request):
        return web.json_response({"freigegeben": True})

    client = await _client(aiohttp_client, [("GET", "/geraet/vorschlag", vorschlag),
                                            ("POST", "/geraet/pruefen", pruefen),
                                            ("POST", "/geraet/entfernen", entfernen)])
    assert await client.vorschlag() == {"kreise": []}
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
