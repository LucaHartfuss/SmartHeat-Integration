from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohasupervisor.exceptions import SupervisorError
from homeassistant.components.hassio import AddonError

from custom_components.smartheat.supervisor_client import (
    AddonNotFoundError,
    AddonOutdatedError,
    AmbiguousAddonMatchError,
    ResolvedAddon,
    async_find_addon_managers,
    async_get_addon_managers,
    async_resolve_addons,
)

REPO_URL = "https://github.com/LucaHartfuss/SmartHeat-for-HomeAssistant"
MIN = {"heizungsbruecke": "0.19.0", "cloudflared_access_mqtt": "1.0.0"}


def _installed_addon(slug: str, url: str, version: str | None = "9.9.9") -> SimpleNamespace:
    # Nur die Felder, die async_resolve_addons liest (ein echtes InstalledAddon hat viele mehr).
    return SimpleNamespace(slug=slug, url=url, version=version)


def _patch_supervisor_client(monkeypatch, installed=None, error=None):
    list_mock = AsyncMock(side_effect=error) if error else AsyncMock(return_value=installed)
    fake_client = SimpleNamespace(addons=SimpleNamespace(list=list_mock))
    monkeypatch.setattr("custom_components.smartheat.supervisor_client.get_supervisor_client", lambda hass: fake_client)
    # Diese Tests rufen mit hass=None auf und pruefen nicht die Hass.io-Gate-Klausel selbst
    # (eigene Tests unten mit dem echten hass-Fixture); ohne den Patch wuerde is_hassio(None) crashen.
    monkeypatch.setattr("custom_components.smartheat.supervisor_client.is_hassio", lambda hass: True)
    # AddonManager.__init__ holt sich seinen Client ueber einen eigenen Import.
    monkeypatch.setattr("homeassistant.components.hassio.addon_manager.get_supervisor_client", lambda hass: fake_client)
    return list_mock


def _both(hb_version="0.19.0", cf_version="1.0.0"):
    return [
        _installed_addon("f5f6325b_heizungsbruecke", REPO_URL, hb_version),
        _installed_addon("f5f6325b_cloudflared_access_mqtt", REPO_URL, cf_version),
        _installed_addon("core_matter_server", "https://github.com/home-assistant/addons"),
    ]


async def test_resolves_repo_hash_prefixed_slugs_with_versions(monkeypatch):
    list_mock = _patch_supervisor_client(monkeypatch, _both())

    result = await async_resolve_addons(None, REPO_URL, MIN)

    assert result == {
        "heizungsbruecke": ResolvedAddon("f5f6325b_heizungsbruecke", "0.19.0"),
        "cloudflared_access_mqtt": ResolvedAddon("f5f6325b_cloudflared_access_mqtt", "1.0.0"),
    }
    list_mock.assert_awaited_once()


async def test_missing_addon_names_the_slug(monkeypatch):
    _patch_supervisor_client(monkeypatch, _both()[1:])

    with pytest.raises(AddonNotFoundError) as info:
        await async_resolve_addons(None, REPO_URL, MIN)

    assert info.value.config_slug == "heizungsbruecke"


async def test_ambiguous_match_names_the_slug(monkeypatch):
    _patch_supervisor_client(monkeypatch, _both() + [_installed_addon("abcd1234_heizungsbruecke", REPO_URL)])

    with pytest.raises(AmbiguousAddonMatchError) as info:
        await async_resolve_addons(None, REPO_URL, MIN)

    assert info.value.config_slug == "heizungsbruecke"


@pytest.mark.parametrize("installed", ["0.18.0", "0.18.9", None])
async def test_outdated_addon_reports_installed_and_required(monkeypatch, installed):
    _patch_supervisor_client(monkeypatch, _both(hb_version=installed))

    with pytest.raises(AddonOutdatedError) as info:
        await async_resolve_addons(None, REPO_URL, MIN)

    assert (info.value.config_slug, info.value.required) == ("heizungsbruecke", "0.19.0")
    assert info.value.installed == (installed or "unbekannt")


async def test_newer_addon_is_accepted(monkeypatch):
    _patch_supervisor_client(monkeypatch, _both(hb_version="0.20.1"))

    result = await async_resolve_addons(None, REPO_URL, MIN)

    assert result["heizungsbruecke"].version == "0.20.1"


async def test_supervisor_error_becomes_addon_error(monkeypatch):
    _patch_supervisor_client(monkeypatch, error=SupervisorError("weg"))

    with pytest.raises(AddonError):
        await async_resolve_addons(None, REPO_URL, MIN)


async def test_get_addon_managers_keeps_order_and_uses_resolved_slugs(monkeypatch):
    # Reale Mindestversion aus const.MIN_ADDON_VERSIONS (0.26.0, TP12f); dieser Test
    # prueft nur die Reihenfolge/Aufloesung, nicht die Versionsgrenze selbst.
    _patch_supervisor_client(monkeypatch, _both(hb_version="0.26.0"))

    managers = await async_get_addon_managers(
        None, [("Heizungsbruecke", "heizungsbruecke"), ("Cloudflared", "cloudflared_access_mqtt")],
    )

    assert [m.addon_slug for m in managers] == ["f5f6325b_heizungsbruecke", "f5f6325b_cloudflared_access_mqtt"]


async def test_get_addon_managers_refuses_outdated_addons(monkeypatch):
    _patch_supervisor_client(monkeypatch, _both(hb_version="0.18.0"))

    with pytest.raises(AddonOutdatedError):
        await async_get_addon_managers(None, [("Heizungsbruecke", "heizungsbruecke")])


async def test_find_addon_managers_resolves_each_slug_without_a_minimum_version(monkeypatch):
    _patch_supervisor_client(monkeypatch, _both(hb_version="0.18.0"))

    managers = await async_find_addon_managers(
        None, [("Heizungsbruecke", "heizungsbruecke"), ("Cloudflared", "cloudflared_access_mqtt")],
    )

    assert {slug: m.addon_slug for slug, m in managers.items()} == {
        "heizungsbruecke": "f5f6325b_heizungsbruecke", "cloudflared_access_mqtt": "f5f6325b_cloudflared_access_mqtt",
    }


async def test_find_addon_managers_gives_none_for_missing_or_ambiguous(monkeypatch):
    _patch_supervisor_client(monkeypatch, _both()[1:] + [
        _installed_addon("a_cloudflared_access_mqtt", REPO_URL),  # zweites cloudflared
    ])

    managers = await async_find_addon_managers(
        None, [("Heizungsbruecke", "heizungsbruecke"), ("Cloudflared", "cloudflared_access_mqtt")],
    )

    assert managers == {"heizungsbruecke": None, "cloudflared_access_mqtt": None}


async def test_find_addon_managers_turns_a_supervisor_error_into_addon_error(monkeypatch):
    _patch_supervisor_client(monkeypatch, error=SupervisorError("weg"))

    with pytest.raises(AddonError):
        await async_find_addon_managers(None, [("Heizungsbruecke", "heizungsbruecke")])


async def test_find_addon_managers_logs_missing_and_ambiguous_reasons_separately(monkeypatch, caplog):
    _patch_supervisor_client(monkeypatch, _both()[1:] + [
        _installed_addon("a_cloudflared_access_mqtt", REPO_URL),  # zweites cloudflared
    ])

    await async_find_addon_managers(
        None, [("Heizungsbruecke", "heizungsbruecke"), ("Cloudflared", "cloudflared_access_mqtt")],
    )

    assert "heizungsbruecke" in caplog.text and "nicht installiert" in caplog.text
    assert "cloudflared_access_mqtt" in caplog.text and "mehrfach installiert" in caplog.text


async def test_find_addon_managers_without_hassio_raises_addon_error(hass):
    # Fehlt Hass.io ganz (Core/Container-Installation, aus einem Backup wiederhergestellt), bricht
    # der rohe get_supervisor_client() mit einem KeyError ab -- der Aufrufer soll aber eine
    # reguläre AddonError sehen, um sauber zu degradieren (z.B. addon_control.async_sign_off).
    with pytest.raises(AddonError):
        await async_find_addon_managers(hass, [("Heizungsbruecke", "heizungsbruecke")])
