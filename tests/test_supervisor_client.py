import pytest
from aiohasupervisor.exceptions import SupervisorError
from homeassistant.components.hassio import AddonError

from custom_components.smartheat.supervisor_client import (
    AddonNotFoundError,
    AddonOutdatedError,
    AmbiguousAddonMatchError,
    ResolvedAddon,
    async_find_addon_managers,
    async_repository_slug,
    async_resolve_addons,
)

from .supervisor_fakes import installed_addon, repository

REPO_URL = "https://github.com/LucaHartfuss/SmartHeat-for-HomeAssistant"
MIN = {"heizungsbruecke": "0.19.0", "cloudflared_access_mqtt": "1.0.0"}


REPO = "f5f6325b"


def _installed_addon(slug: str, url: str = REPO_URL, version: str | None = "9.9.9", repository: str | None = None):
    # `repository` ist der Slug des Store-Repositorys, `url` die frei waehlbare Homepage aus der config.yaml des Add-ons.
    return installed_addon(slug, repository=repository, url=url, version=version)


def _repository(slug: str = REPO, source: str = REPO_URL, url: str = REPO_URL):
    return repository(slug, source=source, url=url)


def _setup(supervisor, installed=None, error=None, repositories=None):
    """Supervisor mit unserem Repository (Slug REPO) und den installierten Add-ons `installed`; `error` wirft
    addons.list."""
    supervisor.installed = list(installed or [])
    supervisor.repositories = [_repository()] if repositories is None else list(repositories)
    if error is not None:
        supervisor.errors["addons.list"] = error


def _both(hb_version="0.19.0", cf_version="1.0.0"):
    return [
        _installed_addon("f5f6325b_heizungsbruecke", REPO_URL, hb_version),
        _installed_addon("f5f6325b_cloudflared_access_mqtt", REPO_URL, cf_version),
        _installed_addon("core_matter_server", "https://github.com/home-assistant/addons"),
    ]


async def test_resolves_repo_hash_prefixed_slugs_with_versions(supervisor):
    _setup(supervisor, _both())

    result = await async_resolve_addons(None, REPO_URL, MIN)

    assert result == {
        "heizungsbruecke": ResolvedAddon("f5f6325b_heizungsbruecke", "0.19.0"),
        "cloudflared_access_mqtt": ResolvedAddon("f5f6325b_cloudflared_access_mqtt", "1.0.0"),
    }
    supervisor.client.addons.list.assert_awaited_once()


async def test_missing_addon_names_the_slug(supervisor):
    _setup(supervisor, _both()[1:])

    with pytest.raises(AddonNotFoundError) as info:
        await async_resolve_addons(None, REPO_URL, MIN)

    assert info.value.config_slug == "heizungsbruecke"


async def test_ambiguous_match_names_the_slug(supervisor):
    _setup(supervisor, _both() + [_installed_addon("f5f6325b_heizungsbruecke", REPO_URL)])

    with pytest.raises(AmbiguousAddonMatchError) as info:
        await async_resolve_addons(None, REPO_URL, MIN)

    assert info.value.config_slug == "heizungsbruecke"


@pytest.mark.parametrize("version", ["GEHEIM-kein-semver", ""])
async def test_a_version_that_cannot_be_compared_is_an_addon_error_without_its_text(supervisor, version):
    """Eine Version, die AwesomeVersion nicht vergleichen kann, ist ein Fehlerpfad (AddonError), keine fremde
    Ausnahme; der Text nennt sie nicht."""
    _setup(supervisor, _both(hb_version=version))

    with pytest.raises(AddonError) as info:
        await async_resolve_addons(None, REPO_URL, MIN)

    assert "heizungsbruecke" in str(info.value)
    assert version == "" or version not in str(info.value)


@pytest.mark.parametrize("installed", ["0.18.0", "0.18.9", None])
async def test_outdated_addon_reports_installed_and_required(supervisor, installed):
    _setup(supervisor, _both(hb_version=installed))

    with pytest.raises(AddonOutdatedError) as info:
        await async_resolve_addons(None, REPO_URL, MIN)

    assert (info.value.config_slug, info.value.required) == ("heizungsbruecke", "0.19.0")
    assert info.value.installed == (installed or "unbekannt")


async def test_newer_addon_is_accepted(supervisor):
    _setup(supervisor, _both(hb_version="0.20.1"))

    result = await async_resolve_addons(None, REPO_URL, MIN)

    assert result["heizungsbruecke"].version == "0.20.1"


async def test_supervisor_error_becomes_addon_error(supervisor):
    _setup(supervisor, error=SupervisorError("weg"))

    with pytest.raises(AddonError) as error:
        await async_resolve_addons(None, REPO_URL, MIN)
    assert "weg" not in str(error.value)
    assert "SupervisorError" in str(error.value)


async def test_find_addon_managers_resolves_each_slug_without_a_minimum_version(supervisor):
    _setup(supervisor, _both(hb_version="0.18.0"))

    managers = await async_find_addon_managers(
        None, [("Heizungsbruecke", "heizungsbruecke"), ("Cloudflared", "cloudflared_access_mqtt")],
    )

    assert {slug: m.addon_slug for slug, m in managers.items()} == {
        "heizungsbruecke": "f5f6325b_heizungsbruecke", "cloudflared_access_mqtt": "f5f6325b_cloudflared_access_mqtt",
    }


async def test_find_addon_managers_gives_none_for_missing_or_ambiguous(supervisor):
    _setup(supervisor, _both()[1:] + [
        _installed_addon("f5f6325b_cloudflared_access_mqtt", REPO_URL),  # zweites cloudflared
    ])

    managers = await async_find_addon_managers(
        None, [("Heizungsbruecke", "heizungsbruecke"), ("Cloudflared", "cloudflared_access_mqtt")],
    )

    assert managers == {"heizungsbruecke": None, "cloudflared_access_mqtt": None}


async def test_find_addon_managers_turns_a_supervisor_error_into_addon_error(supervisor):
    _setup(supervisor, error=SupervisorError("weg"))

    with pytest.raises(AddonError) as error:
        await async_find_addon_managers(None, [("Heizungsbruecke", "heizungsbruecke")])
    assert "weg" not in str(error.value)
    assert "SupervisorError" in str(error.value)


async def test_find_addon_managers_logs_missing_and_ambiguous_reasons_separately(supervisor, caplog):
    _setup(supervisor, _both()[1:] + [
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
async def test_addon_outside_our_repository_never_matches(supervisor, fake):
    _setup(supervisor, [fake, *_both()[1:]], repositories=[
        _repository(), _repository("0badc0de", "https://github.com/fremd/addons")])

    with pytest.raises(AddonNotFoundError):
        await async_resolve_addons(None, REPO_URL, {"heizungsbruecke": "0.19.0"})
    managers = await async_find_addon_managers(None, [("Heizungsbruecke", "heizungsbruecke")])
    assert managers == {"heizungsbruecke": None}


async def test_repository_whose_url_claims_ours_is_not_ours(supervisor):
    """`url` kommt aus der repository.yaml des Autors; massgeblich ist nur `source`."""
    _setup(supervisor, [_installed_addon("0badc0de_heizungsbruecke", repository="0badc0de")],
           repositories=[_repository("0badc0de", source="https://github.com/fremd/addons")])

    with pytest.raises(AddonNotFoundError):
        await async_resolve_addons(None, REPO_URL, {"heizungsbruecke": "0.19.0"})


async def test_without_our_repository_nothing_matches(supervisor):
    _setup(supervisor, _both(), repositories=[])

    managers = await async_find_addon_managers(
        None, [("Heizungsbruecke", "heizungsbruecke"), ("Cloudflared", "cloudflared_access_mqtt")])

    assert managers == {"heizungsbruecke": None, "cloudflared_access_mqtt": None}


@pytest.mark.parametrize("source", [
    REPO_URL + "/", REPO_URL + ".git", REPO_URL.lower(), REPO_URL.upper().replace("HTTPS", "https"),
    REPO_URL.lower() + ".GIT/",
])
async def test_repository_source_is_normalised(supervisor, source):
    _setup(supervisor, _both(), repositories=[_repository(source=source)])

    result = await async_resolve_addons(None, REPO_URL, {"heizungsbruecke": "0.19.0"})

    assert result["heizungsbruecke"].slug == "f5f6325b_heizungsbruecke"


async def test_a_repository_over_plain_http_is_not_ours(supervisor):
    """Das Schema bleibt unterschieden: http ist nicht https."""
    _setup(supervisor, _both(), repositories=[_repository(source=REPO_URL.replace("https://", "http://"))])

    with pytest.raises(AddonNotFoundError):
        await async_resolve_addons(None, REPO_URL, {"heizungsbruecke": "0.19.0"})


async def test_store_error_becomes_addon_error(supervisor):
    _setup(supervisor, _both())
    supervisor.errors["store.repositories_list"] = SupervisorError("geheim")

    with pytest.raises(AddonError) as error:
        await async_resolve_addons(None, REPO_URL, MIN)
    assert "geheim" not in str(error.value)


# --- 5c-W-11: Eintrag mit Branch desselben Repositorys (Entscheidung 2026-10-17) ---


@pytest.mark.parametrize("source", [
    REPO_URL,
    REPO_URL + "#develop",
    REPO_URL + ".git#develop",
    REPO_URL + "/#develop",
    REPO_URL + "#feat/testoffensive-t5",
    REPO_URL + "#v1.0.0",
    # GitHub unterscheidet Gross- und Kleinschreibung im Pfad nicht
    "https://github.com/lucahartfuss/smartheat-for-homeassistant#develop",
])
async def test_a_branch_of_our_repository_is_our_repository(supervisor, source):
    _setup(supervisor, _both(), repositories=[_repository(source=source)])

    assert await async_repository_slug(None, REPO_URL) == REPO
    result = await async_resolve_addons(None, REPO_URL, {"heizungsbruecke": "0.19.0"})
    assert result["heizungsbruecke"].slug == "f5f6325b_heizungsbruecke"


@pytest.mark.parametrize("fake", [
    # Slug aus fremdem Repository, das Repository mit Branch ist unseres
    _installed_addon("0badc0de_heizungsbruecke", REPO_URL, repository="0badc0de"),
    # unser Slug, aber das Add-on gehoert zu einem anderen Repository
    _installed_addon("f5f6325b_heizungsbruecke", REPO_URL, repository="0badc0de"),
    # unser Repository, aber nicht der exakte Slug
    _installed_addon("f5f6325b_heizungsbruecke_dev", REPO_URL, repository=REPO),
])
async def test_a_branch_entry_keeps_the_slug_check(supervisor, fake):
    _setup(supervisor, [fake], repositories=[
        _repository(source=REPO_URL + "#develop"), _repository("0badc0de", "https://github.com/fremd/addons")])

    assert await async_repository_slug(None, REPO_URL) == REPO  # der Branch-Eintrag ist erkannt
    with pytest.raises(AddonNotFoundError):
        await async_resolve_addons(None, REPO_URL, {"heizungsbruecke": "0.19.0"})


@pytest.mark.parametrize("source", [
    REPO_URL + "#",  # leerer Branch
    REPO_URL + "#develop#x",  # mehr als ein `#` ist keine Branch-Angabe
    "https://github.com/andere/SmartHeat-for-HomeAssistant",  # anderer Nutzer
    "https://github.com/andere/SmartHeat-for-HomeAssistant#develop",  # Fork mit Branch
    REPO_URL + "-fork",  # Anhang am Namen
    REPO_URL + "-fork#develop",
    REPO_URL + "/sub",  # Unterpfad
    REPO_URL + "/sub#develop",
    REPO_URL.replace("https://", "http://") + "#develop",  # Schema http
])
async def test_a_similar_address_is_never_our_repository(supervisor, source):
    _setup(supervisor, _both(), repositories=[_repository(source=source)])

    assert await async_repository_slug(None, REPO_URL) is None
    with pytest.raises(AddonNotFoundError):
        await async_resolve_addons(None, REPO_URL, {"heizungsbruecke": "0.19.0"})


@pytest.mark.parametrize("sources", [
    (REPO_URL, REPO_URL),
    (REPO_URL, REPO_URL + "#develop"),
    (REPO_URL + "#develop", REPO_URL + "#main"),
])
async def test_two_entries_of_our_repository_give_no_slug(supervisor, sources):
    _setup(supervisor, _both(), repositories=[_repository(REPO, source=sources[0]),
                                              _repository("0badc0de", source=sources[1])])

    assert await async_repository_slug(None, REPO_URL) is None
    with pytest.raises(AddonNotFoundError):
        await async_resolve_addons(None, REPO_URL, {"heizungsbruecke": "0.19.0"})


async def test_two_invalid_addresses_are_not_the_same_repository(supervisor):
    """Eine ungueltige Branch-Angabe ergibt keine Adresse; zwei ungueltige gelten nie als gleich."""
    _setup(supervisor, _both(), repositories=[_repository(source="https://github.com/andere/addons#")])

    assert await async_repository_slug(None, REPO_URL + "#") is None


@pytest.mark.parametrize("source", [
    REPO_URL + "# develop",  # Leerzeichen
    REPO_URL + "#../../x",  # ..
    REPO_URL + "#\x00",  # Steuerzeichen
    REPO_URL + "#de v",  # Leerzeichen im Branch
    REPO_URL + "#/x",  # fuehrendes /
    REPO_URL + "#a..b",  # .. im Branch
])
async def test_an_invalid_branch_is_never_our_repository(supervisor, source):
    """Als Branch gilt nur [A-Za-z0-9._/-]+ ohne `..` und ohne fuehrendes `/` (fail closed)."""
    _setup(supervisor, _both(), repositories=[_repository(source=source)])

    assert await async_repository_slug(None, REPO_URL) is None
    with pytest.raises(AddonNotFoundError):
        await async_resolve_addons(None, REPO_URL, {"heizungsbruecke": "0.19.0"})
