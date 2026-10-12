"""Add-on-Steuerung der Integration ab 1.0.0 (Spec 5c 2.1): Add-on finden, installieren und starten, Discovery und
bootstrap_url lesen."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohasupervisor.exceptions import SupervisorError
from homeassistant.components.hassio import AddonError, AddonState

from custom_components.smartheat import addon_control
from custom_components.smartheat.const import ADDON_REPOSITORY_URL, DEFAULT_BASE_URL

# --- Spec 5c 2.1: Add-on finden, installieren und starten (ohne Optionen), Discovery und bootstrap_url lesen ---

SLUG = "abc_heizungsbruecke"


def _message(config, *, service="smartheat", addon=SLUG):
    return SimpleNamespace(service=service, addon=addon, config=config)


def _supervisor(discovery=None, options=None, installed=True, repositories=None, installed_addons=None):
    client = MagicMock()
    client.discovery.list = AsyncMock(return_value=discovery or [])
    addon = SimpleNamespace(slug=SLUG, repository="abc", url=ADDON_REPOSITORY_URL, version="1.0.0")
    client.addons.list = AsyncMock(return_value=installed_addons if installed_addons is not None
                                   else ([addon] if installed else []))
    client.addons.addon_info = AsyncMock(return_value=SimpleNamespace(options=options or {}, state="started"))
    client.store.repositories_list = (AsyncMock(side_effect=repositories) if repositories is not None
                                      else AsyncMock(return_value=[_repository("abc")]))
    client.store.add_repository = AsyncMock()
    return client


def _patch(monkeypatch, client, *, hassio=True):
    monkeypatch.setattr(addon_control, "get_supervisor_client", lambda hass: client)
    # Das Store-Repository liest supervisor_client.async_repository_slug ueber den eigenen Import.
    monkeypatch.setattr("custom_components.smartheat.supervisor_client.get_supervisor_client", lambda hass: client)
    monkeypatch.setattr(addon_control, "is_hassio", lambda hass: hassio)


async def test_discovery_returns_only_contract_fields(hass, monkeypatch):
    config = {"geraet_id": "shg-a", "zustand": "nicht_uebernommen", "claim_code": "C", "fremd": 1}
    _patch(monkeypatch, _supervisor([_message(config)]))
    assert await addon_control.async_discovery(hass) == {
        "geraet_id": "shg-a", "zustand": "nicht_uebernommen", "claim_code": "C"}


async def test_discovery_prefers_the_message_with_a_claim_code_else_the_latest(hass, monkeypatch):
    with_code = {"geraet_id": "shg-a", "zustand": "nicht_uebernommen", "claim_code": "C"}
    starting = {"geraet_id": "shg-a", "zustand": "startet"}
    _patch(monkeypatch, _supervisor([_message(with_code), _message(starting)]))
    assert await addon_control.async_discovery(hass) == with_code
    _patch(monkeypatch, _supervisor([_message({"geraet_id": "shg-a", "zustand": "startet"}), _message(starting | {"geraet_id": "shg-b"})]))
    assert await addon_control.async_discovery(hass) == {"geraet_id": "shg-b", "zustand": "startet"}


async def test_discovery_ignores_other_services_and_other_addons(hass, monkeypatch):
    foreign = {"geraet_id": "x", "zustand": "nicht_uebernommen", "claim_code": "X"}
    own = {"geraet_id": "shg-a", "zustand": "startet"}
    _patch(monkeypatch, _supervisor([_message(own), _message(foreign, service="mqtt"),
                                     _message(foreign, addon="core_mosquitto")]))
    assert await addon_control.async_discovery(hass) == own
    _patch(monkeypatch, _supervisor([_message(foreign, service="mqtt")]))
    assert await addon_control.async_discovery(hass) is None


async def test_discovery_is_none_without_supervisor_addon_or_on_errors(hass, monkeypatch):
    _patch(monkeypatch, _supervisor([_message({"geraet_id": "a"})]), hassio=False)
    assert await addon_control.async_discovery(hass) is None
    _patch(monkeypatch, _supervisor([_message({"geraet_id": "a"})], installed=False))
    assert await addon_control.async_discovery(hass) is None
    client = _supervisor()
    client.discovery.list = AsyncMock(side_effect=SupervisorError("boom"))
    _patch(monkeypatch, client)
    assert await addon_control.async_discovery(hass) is None


async def test_bootstrap_url_falls_back_to_production_only_when_missing(hass, monkeypatch):
    for options in ({}, {"bootstrap_url": None}, {"log_level": "info"}):
        _patch(monkeypatch, _supervisor(options=options))
        assert await addon_control.async_bootstrap_url(hass) == DEFAULT_BASE_URL
    _patch(monkeypatch, _supervisor(installed=False))
    assert await addon_control.async_bootstrap_url(hass) == DEFAULT_BASE_URL


async def test_bootstrap_url_uses_only_https_values(hass, monkeypatch):
    for value, expected in (("https://labor.example.test/", "https://labor.example.test"),
                            ("http://192.168.1.20:5001", None)):
        _patch(monkeypatch, _supervisor(options={"bootstrap_url": value}))
        assert await addon_control.async_bootstrap_url(hass) == expected


async def test_bootstrap_url_never_falls_back_on_an_invalid_value(hass, monkeypatch):
    for value in ("", "ftp://labor.example.test", "labor.example.test", "https://", 5, ["https://a.example"]):
        _patch(monkeypatch, _supervisor(options={"bootstrap_url": value}))
        assert await addon_control.async_bootstrap_url(hass) is None, value


async def test_bootstrap_url_is_none_when_options_cannot_be_read(hass, monkeypatch):
    client = _supervisor()
    client.addons.addon_info = AsyncMock(side_effect=SupervisorError("boom"))
    _patch(monkeypatch, client)
    assert await addon_control.async_bootstrap_url(hass) is None


def _install_manager(*states):
    manager = MagicMock()
    manager.async_get_addon_info = AsyncMock(side_effect=[SimpleNamespace(state=state) for state in states])
    manager.async_install_addon = AsyncMock()
    manager.async_start_addon = AsyncMock()
    manager.async_set_addon_options = AsyncMock()
    return manager


async def test_install_and_start_never_writes_options(hass, monkeypatch):
    manager = _install_manager(AddonState.NOT_INSTALLED, AddonState.NOT_RUNNING)
    supervision = AsyncMock(return_value=[])
    monkeypatch.setattr(addon_control, "_manager", lambda hass, slug: manager)
    monkeypatch.setattr(addon_control, "async_bridge_slug", AsyncMock(return_value=None))
    monkeypatch.setattr(addon_control, "_repository_hash", AsyncMock(return_value="abc"))
    monkeypatch.setattr(addon_control, "async_set_supervision", supervision)
    monkeypatch.setattr(addon_control, "is_hassio", lambda hass: True)
    assert await addon_control.async_install_and_start(hass) is manager
    manager.async_install_addon.assert_awaited_once()
    manager.async_start_addon.assert_awaited_once()
    manager.async_set_addon_options.assert_not_called()
    supervision.assert_awaited_once_with(hass, ["abc_heizungsbruecke"], True)


async def test_install_and_start_leaves_a_running_addon_alone(hass, monkeypatch):
    manager = _install_manager(AddonState.RUNNING)
    monkeypatch.setattr(addon_control, "_manager", lambda hass, slug: manager)
    monkeypatch.setattr(addon_control, "async_bridge_slug", AsyncMock(return_value=SLUG))
    monkeypatch.setattr(addon_control, "async_set_supervision", AsyncMock(return_value=[]))
    monkeypatch.setattr(addon_control, "is_hassio", lambda hass: True)
    await addon_control.async_install_and_start(hass)
    manager.async_install_addon.assert_not_called()
    manager.async_start_addon.assert_not_called()


async def test_install_and_start_needs_the_supervisor(hass, monkeypatch):
    monkeypatch.setattr(addon_control, "is_hassio", lambda hass: False)
    with pytest.raises(AddonError):
        await addon_control.async_install_and_start(hass)


def _repository(slug, url=ADDON_REPOSITORY_URL, source=None):
    return SimpleNamespace(slug=slug, source=source or url, url=url, name="SmartHeat", maintainer="x")


async def test_repository_hash_ignores_a_repository_that_only_claims_our_url(hass, monkeypatch):
    """`url` stammt aus der repository.yaml des Autors; nur `source` (die eingetragene Adresse) zaehlt."""
    fake = _repository("0badc0de", source="https://github.com/fremd/addons")
    client = _supervisor(repositories=[[fake], [fake, _repository("f5f6325b")]])
    _patch(monkeypatch, client)
    assert await addon_control._repository_hash(hass) == "f5f6325b"
    client.store.add_repository.assert_awaited_once()


_FAKES = [
    SimpleNamespace(slug="local_heizungsbruecke", repository="local", url=ADDON_REPOSITORY_URL, version="1.0.0"),
    SimpleNamespace(slug="0badc0de_heizungsbruecke", repository="0badc0de", url=ADDON_REPOSITORY_URL, version="1.0.0"),
]


@pytest.mark.parametrize("fake", _FAKES)
async def test_an_addon_imitating_the_bridge_is_never_used(hass, monkeypatch, fake):
    """Lokales oder fremdes Add-on mit unserer url und Slug *_heizungsbruecke: weder Slug noch Discovery noch seine
    Option bootstrap_url (sonst gingen die Zugangsdaten an eine fremde Adresse)."""
    message = SimpleNamespace(service="smartheat", addon=fake.slug,
                              config={"geraet_id": "shg-aaaaaaaaaaaaaaaa", "claim_code": "C"})
    client = _supervisor(discovery=[message], options={"bootstrap_url": "https://angreifer.example"},
                         installed_addons=[fake],
                         repositories=None)
    client.store.repositories_list = AsyncMock(return_value=[
        _repository("abc"), _repository("0badc0de", source="https://github.com/fremd/addons")])
    _patch(monkeypatch, client)

    assert await addon_control.async_bridge_slug(hass) is None
    assert await addon_control.async_discovery(hass) is None
    assert await addon_control.async_bootstrap_url(hass) == DEFAULT_BASE_URL
    client.addons.addon_info.assert_not_called()


async def test_without_our_repository_no_addon_is_the_bridge(hass, monkeypatch):
    client = _supervisor(options={"bootstrap_url": "https://angreifer.example"})
    client.store.repositories_list = AsyncMock(return_value=[])
    _patch(monkeypatch, client)

    assert await addon_control.async_bridge_slug(hass) is None
    assert await addon_control.async_bootstrap_url(hass) == DEFAULT_BASE_URL
    client.addons.addon_info.assert_not_called()


async def test_the_genuine_bridge_is_found_next_to_an_imitation(hass, monkeypatch):
    genuine = SimpleNamespace(slug=SLUG, repository="abc", url=ADDON_REPOSITORY_URL, version="1.0.0")
    client = _supervisor(options={"bootstrap_url": "https://labor.example.test"},
                         installed_addons=[*_FAKES, genuine])
    _patch(monkeypatch, client)

    assert await addon_control.async_bridge_slug(hass) == SLUG
    assert await addon_control.async_bootstrap_url(hass) == "https://labor.example.test"
    client.addons.addon_info.assert_awaited_once_with(SLUG)


async def test_repository_hash_uses_the_repository_already_in_the_store(hass, monkeypatch):
    client = _supervisor(repositories=[[_repository("other", "https://example.test/x"), _repository("f5f6325b")]])
    _patch(monkeypatch, client)
    assert await addon_control._repository_hash(hass) == "f5f6325b"
    client.store.add_repository.assert_not_called()


async def test_repository_hash_adds_a_missing_repository(hass, monkeypatch):
    client = _supervisor(repositories=[[], [_repository("f5f6325b")]])
    _patch(monkeypatch, client)
    assert await addon_control._repository_hash(hass) == "f5f6325b"
    client.store.add_repository.assert_awaited_once()
    assert client.store.add_repository.await_args.args[0].repository == ADDON_REPOSITORY_URL


async def test_repository_hash_fails_with_a_clear_text_when_adding_does_not_help(hass, monkeypatch):
    _patch(monkeypatch, _supervisor(repositories=[[], []]))
    with pytest.raises(AddonError, match="Repository"):
        await addon_control._repository_hash(hass)
    client = _supervisor(repositories=[[]])
    client.store.add_repository = AsyncMock(side_effect=SupervisorError("secret detail"))
    _patch(monkeypatch, client)
    with pytest.raises(AddonError) as error:
        await addon_control._repository_hash(hass)
    assert "secret detail" not in str(error.value)
