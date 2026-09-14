import pytest
from aiohttp import web

from custom_components.smartheat.supervisor_client import SupervisorApiError, SupervisorClient

# pytest-homeassistant-custom-component blockt echte Sockets standardmaessig
# (via pytest-socket). aiohttp_client startet aber einen echten lokalen
# TCP-Testserver -- ohne diesen Opt-in-Fixture schlaegt jeder Testfall mit
# SocketBlockedError fehl, noch bevor der eigentliche Testcode laeuft.
# (Gleiches Muster wie in test_api_client.py aus Task 8.)
pytestmark = pytest.mark.usefixtures("socket_enabled")


async def test_set_addon_options_posts_to_correct_slug(aiohttp_client):
    received = {}

    async def handler(request):
        received["slug"] = request.match_info["slug"]
        received["auth"] = request.headers["Authorization"]
        received["body"] = await request.json()
        return web.json_response({"result": "ok"})

    app = web.Application()
    app.router.add_post("/addons/{slug}/options", handler)
    client = await aiohttp_client(app)

    # base_url="" -- aiohttp's TestClient rejects absolute URLs (asserts
    # `not url.absolute` in test_utils.py); production code keeps the real
    # default "http://supervisor" (see SupervisorClient), only the test
    # client is pointed at the local test server via a relative path.
    await SupervisorClient(client, token="sup-tok", base_url="").set_addon_options(
        "heizungsbruecke", {"tenant_id": "wohnung1"}
    )

    assert received["slug"] == "heizungsbruecke"
    assert received["auth"] == "Bearer sup-tok"
    assert received["body"] == {"options": {"tenant_id": "wohnung1"}}


async def test_set_addon_options_raises_on_non_200(aiohttp_client):
    async def handler(request):
        return web.json_response({"result": "error"}, status=400)

    app = web.Application()
    app.router.add_post("/addons/{slug}/options", handler)
    client = await aiohttp_client(app)

    with pytest.raises(SupervisorApiError):
        await SupervisorClient(client, token="sup-tok", base_url="").set_addon_options("heizungsbruecke", {})


async def test_restart_addon_posts_to_correct_slug(aiohttp_client):
    received = {}

    async def handler(request):
        received["slug"] = request.match_info["slug"]
        return web.json_response({"result": "ok"})

    app = web.Application()
    app.router.add_post("/addons/{slug}/restart", handler)
    client = await aiohttp_client(app)

    await SupervisorClient(client, token="sup-tok", base_url="").restart_addon("cloudflared_access_mqtt")

    assert received["slug"] == "cloudflared_access_mqtt"
