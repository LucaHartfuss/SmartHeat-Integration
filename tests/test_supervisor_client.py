from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.smartheat.supervisor_client import (
    AddonNotFoundError,
    async_get_addon_manager,
    async_resolve_addon_slug,
)

REPO_URL = "https://github.com/LucaHartfuss/SmartHeat-for-HomeAssistant"


def _installed_addon(slug: str, url: str) -> SimpleNamespace:
    # Nur die Felder, die async_resolve_addon_slug tatsaechlich liest -- ein echtes
    # aiohasupervisor.models.InstalledAddon hat viele weitere Pflichtfelder, die hier
    # nicht gebraucht werden.
    return SimpleNamespace(slug=slug, url=url)


def _patch_supervisor_client(monkeypatch, installed):
    fake_client = SimpleNamespace(
        addons=SimpleNamespace(list=AsyncMock(return_value=installed))
    )
    monkeypatch.setattr(
        "custom_components.smartheat.supervisor_client.get_supervisor_client",
        lambda hass: fake_client,
    )
    # AddonManager.__init__ resolves its own supervisor client via a SEPARATE import of
    # get_supervisor_client (homeassistant.components.hassio.addon_manager's, not ours) --
    # needs patching too whenever a test actually constructs an AddonManager (see
    # test_get_addon_manager_returns_manager_for_resolved_slug), otherwise it would try to
    # read hass.data[...] off the `hass=None` used throughout this test file.
    monkeypatch.setattr(
        "homeassistant.components.hassio.addon_manager.get_supervisor_client",
        lambda hass: fake_client,
    )


async def test_resolves_repo_hash_prefixed_slug(monkeypatch):
    # Der reale, gegen einen echten Supervisor verifizierte Fall (siehe Task 14 in
    # .superpowers/sdd/2026-09-14-smartheat-config-integration/progress.md): ein von
    # einem Custom-Repository installiertes Add-on bekommt einen Repository-Hash-Praefix,
    # nicht den bare config.yaml-Slug.
    _patch_supervisor_client(monkeypatch, [
        _installed_addon("f5f6325b_heizungsbruecke", REPO_URL),
        _installed_addon("f5f6325b_cloudflared_access_mqtt", REPO_URL),
        _installed_addon("core_matter_server", "https://github.com/home-assistant/addons"),
    ])

    slug = await async_resolve_addon_slug(hass=None, repository_url=REPO_URL, config_slug="heizungsbruecke")

    assert slug == "f5f6325b_heizungsbruecke"


async def test_disambiguates_addons_sharing_the_same_repository_url(monkeypatch):
    # Beide SmartHeat-Add-ons teilen dieselbe Repository-URL -- url-Filterung allein
    # wuerde beide treffen, das Slug-Suffix muss zusaetzlich unterscheiden.
    _patch_supervisor_client(monkeypatch, [
        _installed_addon("f5f6325b_heizungsbruecke", REPO_URL),
        _installed_addon("f5f6325b_cloudflared_access_mqtt", REPO_URL),
    ])

    slug = await async_resolve_addon_slug(hass=None, repository_url=REPO_URL, config_slug="cloudflared_access_mqtt")

    assert slug == "f5f6325b_cloudflared_access_mqtt"


async def test_raises_when_no_addon_matches(monkeypatch):
    _patch_supervisor_client(monkeypatch, [
        _installed_addon("core_matter_server", "https://github.com/home-assistant/addons"),
    ])

    with pytest.raises(AddonNotFoundError):
        await async_resolve_addon_slug(hass=None, repository_url=REPO_URL, config_slug="heizungsbruecke")


async def test_does_not_match_on_url_alone_without_suffix(monkeypatch):
    # Ein Add-on aus demselben Repo, aber mit einem anderen config.yaml-Slug, darf nicht
    # faelschlich als Treffer fuer einen ganz anderen gesuchten Slug durchgehen.
    _patch_supervisor_client(monkeypatch, [
        _installed_addon("f5f6325b_some_other_addon", REPO_URL),
    ])

    with pytest.raises(AddonNotFoundError):
        await async_resolve_addon_slug(hass=None, repository_url=REPO_URL, config_slug="heizungsbruecke")


async def test_get_addon_manager_returns_manager_for_resolved_slug(monkeypatch):
    _patch_supervisor_client(monkeypatch, [
        _installed_addon("f5f6325b_heizungsbruecke", REPO_URL),
    ])

    manager = await async_get_addon_manager(hass=None, addon_name="Heizungsbruecke", config_slug="heizungsbruecke")

    assert manager.addon_slug == "f5f6325b_heizungsbruecke"
    assert manager.addon_name == "Heizungsbruecke"
