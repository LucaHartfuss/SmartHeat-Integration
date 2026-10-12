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
    async_resolve_addons,
)

REPO_URL = "https://github.com/LucaHartfuss/SmartHeat-for-HomeAssistant"
MIN = {"heizungsbruecke": "0.19.0", "cloudflared_access_mqtt": "1.0.0"}


REPO = "f5f6325b"


def _installed_addon(slug: str, url: str = REPO_URL, version: str | None = "9.9.9",
                     repository: str | None = None) -> SimpleNamespace:
    # Nur die Felder, die die Zuordnung liest (ein echtes InstalledAddon hat viele mehr); `repository` ist der Slug
    # des Store-Repositorys, `url` die frei waehlbare Homepage aus der config.yaml des Add-ons.
    return SimpleNamespace(slug=slug, url=url, version=version, repository=repository or slug.split("_")[0])


def _repository(slug: str = REPO, source: str = REPO_URL, url: str = REPO_URL) -> SimpleNamespace:
    return SimpleNamespace(slug=slug, source=source, url=url, name="x", maintainer="x")


def _patch_supervisor_client(monkeypatch, installed=None, error=None, repositories=None):
    list_mock = AsyncMock(side_effect=error) if error else AsyncMock(return_value=installed)
    store = SimpleNamespace(repositories_list=AsyncMock(return_value=[_repository()] if repositories is None
                                                         else repositories),
                            add_repository=AsyncMock())
    fake_client = SimpleNamespace(addons=SimpleNamespace(list=list_mock), store=store)
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
    _patch_supervisor_client(monkeypatch, _both() + [_installed_addon("f5f6325b_heizungsbruecke", REPO_URL)])

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

    with pytest.raises(AddonError) as error:
        await async_resolve_addons(None, REPO_URL, MIN)
    assert "weg" not in str(error.value)
    assert "SupervisorError" in str(error.value)


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
        _installed_addon("f5f6325b_cloudflared_access_mqtt", REPO_URL),  # zweites cloudflared
    ])

    managers = await async_find_addon_managers(
        None, [("Heizungsbruecke", "heizungsbruecke"), ("Cloudflared", "cloudflared_access_mqtt")],
    )

    assert managers == {"heizungsbruecke": None, "cloudflared_access_mqtt": None}


async def test_find_addon_managers_turns_a_supervisor_error_into_addon_error(monkeypatch):
    _patch_supervisor_client(monkeypatch, error=SupervisorError("weg"))

    with pytest.raises(AddonError) as error:
        await async_find_addon_managers(None, [("Heizungsbruecke", "heizungsbruecke")])
    assert "weg" not in str(error.value)
    assert "SupervisorError" in str(error.value)


async def test_find_addon_managers_logs_missing_and_ambiguous_reasons_separately(monkeypatch, caplog):
    _patch_supervisor_client(monkeypatch, _both()[1:] + [
        _installed_addon("f5f6325b_cloudflared_access_mqtt", REPO_URL),  # zweites cloudflared
    ])

    await async_find_addon_managers(
        None, [("Heizungsbruecke", "heizungsbruecke"), ("Cloudflared", "cloudflared_access_mqtt")],
    )

    assert "heizungsbruecke" in caplog.text and "nicht installiert" in caplog.text
    assert "cloudflared_access_mqtt" in caplog.text and "mehrfach installiert" in caplog.text


async def test_find_addon_managers_without_hassio_raises_addon_error(hass):
    # Fehlt Hass.io ganz (Core/Container-Installation, aus einem Backup wiederhergestellt), bricht
    # der rohe get_supervisor_client() mit einem KeyError ab -- der Aufrufer soll aber eine
    # reguläre AddonError sehen, um sauber zu degradieren (z.B. der Waechter).
    with pytest.raises(AddonError):
        await async_find_addon_managers(hass, [("Heizungsbruecke", "heizungsbruecke")])


# --- Nur Add-ons aus unserem Repository (Herkunft an Store-Adresse und exaktem Slug) ---


@pytest.mark.parametrize("fake", [
    # lokales Add-on mit unserer url
    _installed_addon("local_heizungsbruecke", REPO_URL, repository="local"),
    # Add-on aus einem fremden Repository mit unserer url und einem *_heizungsbruecke
    _installed_addon("0badc0de_heizungsbruecke", REPO_URL, repository="0badc0de"),
    # Slug aus unserem Hash, aber aus einem anderen Repository
    _installed_addon("f5f6325b_heizungsbruecke", REPO_URL, repository="0badc0de"),
])
async def test_addon_outside_our_repository_never_matches(monkeypatch, fake):
    _patch_supervisor_client(monkeypatch, [fake, *_both()[1:]], repositories=[
        _repository(), _repository("0badc0de", "https://github.com/fremd/addons")])

    with pytest.raises(AddonNotFoundError):
        await async_resolve_addons(None, REPO_URL, {"heizungsbruecke": "0.19.0"})
    managers = await async_find_addon_managers(None, [("Heizungsbruecke", "heizungsbruecke")])
    assert managers == {"heizungsbruecke": None}


async def test_repository_whose_url_claims_ours_is_not_ours(monkeypatch):
    """`url` kommt aus der repository.yaml des Autors; massgeblich ist nur `source`."""
    _patch_supervisor_client(monkeypatch, [_installed_addon("0badc0de_heizungsbruecke", repository="0badc0de")],
                             repositories=[_repository("0badc0de", source="https://github.com/fremd/addons")])

    with pytest.raises(AddonNotFoundError):
        await async_resolve_addons(None, REPO_URL, {"heizungsbruecke": "0.19.0"})


async def test_without_our_repository_nothing_matches(monkeypatch):
    _patch_supervisor_client(monkeypatch, _both(), repositories=[])

    managers = await async_find_addon_managers(
        None, [("Heizungsbruecke", "heizungsbruecke"), ("Cloudflared", "cloudflared_access_mqtt")])

    assert managers == {"heizungsbruecke": None, "cloudflared_access_mqtt": None}


@pytest.mark.parametrize("source", [
    REPO_URL + "/", REPO_URL + ".git", REPO_URL.lower(), REPO_URL.upper().replace("HTTPS", "https"),
    REPO_URL.lower() + ".GIT/",
])
async def test_repository_source_is_normalised(monkeypatch, source):
    _patch_supervisor_client(monkeypatch, _both(), repositories=[_repository(source=source)])

    result = await async_resolve_addons(None, REPO_URL, {"heizungsbruecke": "0.19.0"})

    assert result["heizungsbruecke"].slug == "f5f6325b_heizungsbruecke"


async def test_a_repository_over_plain_http_is_not_ours(monkeypatch):
    """Das Schema bleibt unterschieden: http ist nicht https."""
    _patch_supervisor_client(monkeypatch, _both(),
                             repositories=[_repository(source=REPO_URL.replace("https://", "http://"))])

    with pytest.raises(AddonNotFoundError):
        await async_resolve_addons(None, REPO_URL, {"heizungsbruecke": "0.19.0"})


async def test_store_error_becomes_addon_error(monkeypatch):
    _patch_supervisor_client(monkeypatch, _both())
    from custom_components.smartheat import supervisor_client

    client = supervisor_client.get_supervisor_client(None)
    client.store.repositories_list = AsyncMock(side_effect=SupervisorError("geheim"))

    with pytest.raises(AddonError) as error:
        await async_resolve_addons(None, REPO_URL, MIN)
    assert "geheim" not in str(error.value)
