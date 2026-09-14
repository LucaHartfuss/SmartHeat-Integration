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


async def test_list_profiles_returns_catalog(aiohttp_client):
    async def handler(request):
        assert request.headers["Authorization"] == "Bearer tok123"
        return web.json_response([
            {"hersteller": "Vaillant", "erzeuger_typ": "Gastherme",
             "verteilsystem": "Heizkoerper", "profile_id": "vaillant_gastherme_heizkoerper",
             "verified": True},
        ])

    app = web.Application()
    app.router.add_get("/profiles", handler)
    client = await aiohttp_client(app)

    result = await HeizungsserverClient(client, "").list_profiles("tok123")

    assert result[0]["profile_id"] == "vaillant_gastherme_heizkoerper"


async def test_provision_raises_api_error_on_failure(aiohttp_client):
    async def handler(request):
        return web.json_response({"error": "Provisioning fehlgeschlagen"}, status=400)

    app = web.Application()
    app.router.add_post("/tenants/wohnung1/provision", handler)
    client = await aiohttp_client(app)

    with pytest.raises(ApiError):
        await HeizungsserverClient(client, "").provision("tok123", "wohnung1", "vaillant_gastherme_heizkoerper")
