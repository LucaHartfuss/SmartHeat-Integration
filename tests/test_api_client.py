import pytest
from aiohttp import web

from custom_components.smartheat.api_client import (
    ApiError, CannotConnect, HeizungsserverClient, InvalidAuth,
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
