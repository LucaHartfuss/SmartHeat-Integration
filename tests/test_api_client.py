import asyncio
import base64

import aiohttp
import pytest
from aiohttp import web

from custom_components.smartheat.api_client import (
    AccessDenied,
    ApiError,
    CannotConnect,
    HeizungsserverClient,
    InvalidAuth,
    InvalidResponse,
    ProfileRejected,
)

# pytest-homeassistant-custom-component blockt echte Sockets standardmaessig
# (via pytest-socket). aiohttp_client startet aber einen echten lokalen
# TCP-Testserver -- ohne diesen Opt-in-Fixture schlaegt jeder Testfall mit
# SocketBlockedError fehl, noch bevor der eigentliche Testcode laeuft.
pytestmark = pytest.mark.usefixtures("socket_enabled")


async def test_login_returns_token_on_success(aiohttp_client):
    async def handler(request):
        body = await request.json()
        assert body == {"email": "a@b.de", "password": "pw"}
        return web.json_response({"token": "tok123"})

    app = web.Application()
    app.router.add_post("/auth/login", handler)
    client = await aiohttp_client(app)

    result = await HeizungsserverClient(client, "").login("a@b.de", "pw")

    assert result == "tok123"


async def test_login_raises_invalid_auth_on_401(aiohttp_client):
    async def handler(request):
        return web.json_response({"error": "Ungueltige Zugangsdaten"}, status=401)

    app = web.Application()
    app.router.add_post("/auth/login", handler)
    client = await aiohttp_client(app)

    with pytest.raises(InvalidAuth):
        await HeizungsserverClient(client, "").login("a@b.de", "falsch")


async def test_get_catalog_returns_catalog(aiohttp_client):
    catalog = {
        "catalog_version": 1,
        "profiles": [{"hersteller": "Vaillant", "erzeuger_typ": "Gastherme",
                      "verteilsystem": "Heizkoerper", "profile_id": "vaillant_gastherme_heizkoerper",
                      "verified": True}],
        "integrations": [],
    }

    async def handler(request):
        assert request.headers["Authorization"] == "Bearer tok123"
        return web.json_response(catalog)

    app = web.Application()
    app.router.add_get("/catalog", handler)
    client = await aiohttp_client(app)

    assert await HeizungsserverClient(client, "").get_catalog("tok123") == catalog


async def test_get_catalog_raises_api_error_on_404(aiohttp_client):
    # Alter Server ohne /catalog (Rollout-Reihenfolge verletzt): Fehler statt Absturz.
    app = web.Application()
    client = await aiohttp_client(app)

    with pytest.raises(ApiError):
        await HeizungsserverClient(client, "").get_catalog("tok123")


@pytest.mark.parametrize("body", [[], {"catalog_version": 1}, {"profiles": "x"}])
async def test_get_catalog_rejects_body_without_profiles_list(aiohttp_client, body):
    async def handler(request):
        return web.json_response(body)

    app = web.Application()
    app.router.add_get("/catalog", handler)
    client = await aiohttp_client(app)

    with pytest.raises(ApiError):
        await HeizungsserverClient(client, "").get_catalog("tok123")


async def test_provision_raises_api_error_on_failure(aiohttp_client):
    async def handler(request):
        return web.json_response({"error": "Provisioning fehlgeschlagen"}, status=400)

    app = web.Application()
    app.router.add_post("/tenants/wohnung1/provision", handler)
    client = await aiohttp_client(app)

    with pytest.raises(ApiError):
        await HeizungsserverClient(client, "").provision("tok123", "wohnung1", "vaillant_gastherme_heizkoerper")


async def test_provision_raises_invalid_auth_on_401(aiohttp_client):
    # Analog zu test_login_raises_invalid_auth_on_401 / _get_authenticated()s 401-Handling:
    # ein abgelaufenes Token waehrend des Provisionierens muss InvalidAuth werfen, nicht den
    # generischen ApiError-Zweig treffen (siehe config_flow.py::async_step_finish, das auf
    # InvalidAuth explizit mit einem Re-Login-Routing reagiert).
    async def handler(request):
        return web.json_response({"error": "Sitzung abgelaufen"}, status=401)

    app = web.Application()
    app.router.add_post("/tenants/wohnung1/provision", handler)
    client = await aiohttp_client(app)

    with pytest.raises(InvalidAuth):
        await HeizungsserverClient(client, "").provision("tok123", "wohnung1", "vaillant_gastherme_heizkoerper")


async def test_provision_raises_api_error_on_a_200_with_the_wrong_content_type(aiohttp_client):
    # Ein 200 mit falschem Content-Type ist eine kaputte Antwort (ApiError), keine
    # Verbindungsstoerung -- aiohttp.ContentTypeError ist sonst eine ClientError-Unterklasse und
    # wuerde faelschlich als CannotConnect ankommen.
    async def handler(request):
        return web.Response(status=200, text="not json", content_type="text/plain")

    app = web.Application()
    app.router.add_post("/tenants/wohnung1/provision", handler)
    client = await aiohttp_client(app)

    with pytest.raises(ApiError):
        await HeizungsserverClient(client, "").provision("tok123", "wohnung1", "vaillant_gastherme_heizkoerper")


async def test_provision_raises_api_error_on_invalid_json_body(aiohttp_client):
    async def handler(request):
        return web.Response(status=200, text="{not valid json", content_type="application/json")

    app = web.Application()
    app.router.add_post("/tenants/wohnung1/provision", handler)
    client = await aiohttp_client(app)

    with pytest.raises(ApiError):
        await HeizungsserverClient(client, "").provision("tok123", "wohnung1", "vaillant_gastherme_heizkoerper")


@pytest.mark.parametrize("status", [200, 204])
async def test_logout_posts_bearer_token(aiohttp_client, status):
    seen = {}

    async def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        return web.Response(status=status)

    app = web.Application()
    app.router.add_post("/auth/logout", handler)
    client = await aiohttp_client(app)

    await HeizungsserverClient(client, "").logout("tok123")

    assert seen["auth"] == "Bearer tok123"


async def test_logout_raises_invalid_auth_on_401(aiohttp_client):
    async def handler(request):
        return web.json_response({"error": "x"}, status=401)

    app = web.Application()
    app.router.add_post("/auth/logout", handler)
    client = await aiohttp_client(app)

    with pytest.raises(InvalidAuth):
        await HeizungsserverClient(client, "").logout("tok123")


async def test_logout_raises_api_error_on_500(aiohttp_client):
    async def handler(request):
        return web.Response(status=500)

    app = web.Application()
    app.router.add_post("/auth/logout", handler)
    client = await aiohttp_client(app)

    with pytest.raises(ApiError):
        await HeizungsserverClient(client, "").logout("tok123")


async def test_update_profile_posts_the_profile_and_returns_profile_params(aiohttp_client):
    async def handler(request):
        assert request.headers["Authorization"] == "Bearer tok123"
        assert await request.json() == {"profile_id": "vaillant_gastherme_heizkoerper"}
        return web.json_response({"profile_params": {"verteilsystem": "Heizkoerper"}})

    app = web.Application()
    app.router.add_post("/tenants/wohnung1/profile", handler)
    client = await aiohttp_client(app)

    result = await HeizungsserverClient(client, "").update_profile("tok123", "wohnung1", "vaillant_gastherme_heizkoerper")

    assert result == {"verteilsystem": "Heizkoerper"}


@pytest.mark.parametrize("status,body,error", [
    (401, {"error": "x"}, InvalidAuth),
    (403, {"error": "x"}, ApiError),
    (200, {"profile_params": "Heizkoerper"}, ApiError),
    (200, {}, ApiError),
])
async def test_update_profile_errors(aiohttp_client, status, body, error):
    async def handler(request):
        return web.json_response(body, status=status)

    app = web.Application()
    app.router.add_post("/tenants/wohnung1/profile", handler)
    client = await aiohttp_client(app)

    with pytest.raises(error):
        await HeizungsserverClient(client, "").update_profile("tok", "wohnung1", "p")


async def test_update_profile_raises_api_error_on_a_200_with_the_wrong_content_type(aiohttp_client):
    async def handler(request):
        return web.Response(status=200, text="not json", content_type="text/plain")

    app = web.Application()
    app.router.add_post("/tenants/wohnung1/profile", handler)
    client = await aiohttp_client(app)

    with pytest.raises(ApiError):
        await HeizungsserverClient(client, "").update_profile("tok", "wohnung1", "p")


async def test_update_profile_raises_api_error_on_invalid_json_body(aiohttp_client):
    async def handler(request):
        return web.Response(status=200, text="{not valid json", content_type="application/json")

    app = web.Application()
    app.router.add_post("/tenants/wohnung1/profile", handler)
    client = await aiohttp_client(app)

    with pytest.raises(ApiError):
        await HeizungsserverClient(client, "").update_profile("tok", "wohnung1", "p")


@pytest.mark.parametrize("status", [204, 401, 500])
async def test_delete_installation_sends_basic_auth_and_returns_the_status(aiohttp_client, status):
    seen = {}

    async def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        seen["tenant"] = request.match_info["tenant_id"]
        return web.Response(status=status)

    app = web.Application()
    app.router.add_delete("/tenants/{tenant_id}/installation", handler)
    client = await aiohttp_client(app)

    result = await HeizungsserverClient(client, "").delete_installation("wohnung1", "wohnung1_abc", "geheim")

    assert result == status
    expected_token = base64.b64encode(b"wohnung1_abc:geheim").decode()
    assert seen == {"auth": f"Basic {expected_token}", "tenant": "wohnung1"}


async def test_delete_installation_returns_none_without_connection():
    session = aiohttp.ClientSession()
    try:
        result = await HeizungsserverClient(session, "http://127.0.0.1:9").delete_installation("w", "u", "p")
    finally:
        await session.close()
    assert result is None


async def _server(aiohttp_client, method, path, handler):
    app = web.Application()
    app.router.add_route(method, path, handler)
    return HeizungsserverClient(await aiohttp_client(app), "")


async def test_login_with_broken_json_is_an_invalid_response(aiohttp_client):
    async def handler(request):
        return web.Response(text="kein json", content_type="application/json")

    client = await _server(aiohttp_client, "POST", "/auth/login", handler)
    with pytest.raises(InvalidResponse):
        await client.login("a@b.de", "pw")


@pytest.mark.parametrize("body", [{"x": 1}, {"token": ""}, {"token": 5}, []])
async def test_login_without_token_is_an_invalid_response(aiohttp_client, body):
    async def handler(request):
        return web.json_response(body)

    client = await _server(aiohttp_client, "POST", "/auth/login", handler)
    with pytest.raises(InvalidResponse):
        await client.login("a@b.de", "pw")


@pytest.mark.parametrize("body", [{"tenants": []}, [{"x": 1}], [{"tenant_id": 5}]])
async def test_tenant_list_with_the_wrong_shape_is_an_invalid_response(aiohttp_client, body):
    async def handler(request):
        return web.json_response(body)

    client = await _server(aiohttp_client, "GET", "/accounts/me/tenants", handler)
    with pytest.raises(InvalidResponse):
        await client.list_tenants("tok")


async def test_timeout_is_cannot_connect(aiohttp_client, monkeypatch):
    monkeypatch.setattr("custom_components.smartheat.api_client.REQUEST_TIMEOUT_SECONDS", 0.05)

    async def handler(request):
        await asyncio.sleep(1)
        return web.json_response({"token": "t"})

    client = await _server(aiohttp_client, "POST", "/auth/login", handler)
    with pytest.raises(CannotConnect):
        await client.login("a@b.de", "pw")


async def test_403_carries_the_server_text(aiohttp_client):
    text = "Diese Anlage ist derzeit nicht aktiv (Abo abgelaufen/pausiert)"

    async def handler(request):
        return web.json_response({"error": text}, status=403)

    client = await _server(aiohttp_client, "POST", "/tenants/t1/provision", handler)
    with pytest.raises(AccessDenied) as caught:
        await client.provision("tok", "t1", "p")
    assert caught.value.reason == text


async def test_403_reason_is_shortened_and_may_be_empty(aiohttp_client):
    async def long_handler(request):
        return web.json_response({"error": "x" * 500}, status=403)

    async def plain_handler(request):
        return web.Response(text="nope", status=403)

    with pytest.raises(AccessDenied) as caught:
        await (await _server(aiohttp_client, "POST", "/tenants/t1/provision", long_handler)).provision("tok", "t1", "p")
    assert len(caught.value.reason) == 200
    with pytest.raises(AccessDenied) as caught:
        await (await _server(aiohttp_client, "POST", "/tenants/t1/provision", plain_handler)).provision("tok", "t1", "p")
    assert caught.value.reason == ""


async def test_400_on_profile_change_is_profile_rejected(aiohttp_client):
    async def handler(request):
        return web.json_response({"error": "Profil unbekannt"}, status=400)

    client = await _server(aiohttp_client, "POST", "/tenants/t1/profile", handler)
    with pytest.raises(ProfileRejected):
        await client.update_profile("tok", "t1", "p")


async def test_rejections_are_logged_without_secrets(aiohttp_client, caplog):
    async def handler(request):
        return web.json_response({"error": "kaputt"}, status=500)

    client = await _server(aiohttp_client, "POST", "/tenants/t1/provision", handler)
    with pytest.raises(ApiError):
        await client.provision("geheimes-token-123", "t1", "p")
    assert "HTTP 500" in caplog.text and "kaputt" in caplog.text
    assert "geheimes-token-123" not in caplog.text
