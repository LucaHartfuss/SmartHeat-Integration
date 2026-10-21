"""Add-on-Steuerung der Integration ab 1.0.0 (Spec 5c 2.1): Add-on finden, installieren und starten, Discovery und
bootstrap_url lesen. Der Supervisor ist die Attrappe `supervisor` (supervisor_fakes.py), keine eigene Funktion wird
ersetzt (Testoffensive T5', A5-25)."""
import json
import logging
from pathlib import Path

import pytest
from aiohasupervisor.exceptions import SupervisorError, SupervisorTimeoutError
from aiohasupervisor.models import AddonBoot, AddonsOptions, AddonState
from homeassistant.components.hassio import AddonError, AddonManager
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.smartheat import addon_control
from custom_components.smartheat.addon_control import BootstrapUrl
from custom_components.smartheat.const import ADDON_REPOSITORY_URL, DEFAULT_BASE_URL, DOMAIN

from .supervisor_fakes import BRIDGE_SLUG, discovery_message, installed_addon, repository

# --- Spec 5c 2.1: Add-on finden, installieren und starten (ohne Optionen), Discovery und bootstrap_url lesen ---

SLUG = BRIDGE_SLUG
_COMPONENT = Path(__file__).parents[1] / "custom_components" / "smartheat"


def _message(config, *, service="smartheat", addon=SLUG):
    return discovery_message(config, service=service, addon=addon)


async def test_discovery_returns_only_contract_fields(hass, supervisor):
    config = {"geraet_id": "shg-a", "zustand": "nicht_uebernommen", "claim_code": "C", "fremd": 1}
    supervisor.discovery = [_message(config)]
    assert await addon_control.async_discovery(hass) == {
        "geraet_id": "shg-a", "zustand": "nicht_uebernommen", "claim_code": "C"}


async def test_discovery_prefers_the_message_with_a_claim_code_else_the_latest(hass, supervisor):
    with_code = {"geraet_id": "shg-a", "zustand": "nicht_uebernommen", "claim_code": "C"}
    starting = {"geraet_id": "shg-a", "zustand": "startet"}
    supervisor.discovery = [_message(with_code), _message(starting)]
    assert await addon_control.async_discovery(hass) == with_code
    supervisor.discovery = [_message({"geraet_id": "shg-a", "zustand": "startet"}),
                            _message(starting | {"geraet_id": "shg-b"})]
    assert await addon_control.async_discovery(hass) == {"geraet_id": "shg-b", "zustand": "startet"}


async def test_discovery_latest_is_the_last_message_of_the_supervisor_list(hass, supervisor):
    """5c-I-6 (hingenommen): eine Zeitangabe liefert der Supervisor nicht; „die juengste“ ist die letzte der Liste
    ohne Code, und eine Meldung mit Code gewinnt gegen spaetere ohne (unter mehreren mit Code die letzte)."""
    first = {"geraet_id": "shg-a", "zustand": "startet"}
    second = {"geraet_id": "shg-b", "zustand": "startet"}
    supervisor.discovery = [_message(first), _message(second)]
    assert await addon_control.async_discovery(hass) == second
    supervisor.discovery = [_message(second), _message(first)]
    assert await addon_control.async_discovery(hass) == first
    code_1 = first | {"zustand": "nicht_uebernommen", "claim_code": "C1"}
    code_2 = second | {"zustand": "nicht_uebernommen", "claim_code": "C2"}
    supervisor.discovery = [_message(code_1), _message(second), _message(first)]
    assert await addon_control.async_discovery(hass) == code_1
    supervisor.discovery = [_message(code_1), _message(first), _message(code_2), _message(second)]
    assert await addon_control.async_discovery(hass) == code_2


async def test_discovery_ignores_other_services_and_other_addons(hass, supervisor):
    foreign = {"geraet_id": "x", "zustand": "nicht_uebernommen", "claim_code": "X"}
    own = {"geraet_id": "shg-a", "zustand": "startet"}
    supervisor.discovery = [_message(own), _message(foreign, service="mqtt"),
                            _message(foreign, addon="core_mosquitto")]
    assert await addon_control.async_discovery(hass) == own
    supervisor.discovery = [_message(foreign, service="mqtt")]
    assert await addon_control.async_discovery(hass) is None


async def test_discovery_is_none_without_supervisor_addon_or_on_errors(hass, supervisor):
    supervisor.discovery = [_message({"geraet_id": "a"})]
    supervisor.hassio = False
    assert await addon_control.async_discovery(hass) is None
    supervisor.hassio = True
    supervisor.installed = []
    assert await addon_control.async_discovery(hass) is None
    supervisor.installed = [installed_addon(SLUG)]
    supervisor.errors["discovery.list"] = SupervisorError("boom")
    assert await addon_control.async_discovery(hass) is None


async def test_discovery_resolves_the_bridge_on_every_call(hass, supervisor):
    """5c-I-13 (hingenommen): jede Abfrage liest Store und Add-on-Liste neu; ein inzwischen entferntes Add-on
    liefert keine Discovery mehr."""
    own = {"geraet_id": "shg-a", "zustand": "startet"}
    supervisor.discovery = [_message(own)]
    assert await addon_control.async_discovery(hass) == own
    assert supervisor.client.addons.list.await_count == 1
    assert supervisor.client.store.repositories_list.await_count == 1
    supervisor.installed = []
    assert await addon_control.async_discovery(hass) is None
    assert supervisor.client.addons.list.await_count == 2
    assert supervisor.client.store.repositories_list.await_count == 2


async def test_bootstrap_url_falls_back_to_production_only_when_missing(hass, supervisor):
    for options in ({}, {"bootstrap_url": None}, {"log_level": "info"}):
        supervisor.addon_options[SLUG] = options
        assert await addon_control.async_bootstrap_url(hass) == BootstrapUrl(DEFAULT_BASE_URL, None)
    supervisor.installed = []
    assert await addon_control.async_bootstrap_url(hass) == BootstrapUrl(DEFAULT_BASE_URL, None)


async def test_bootstrap_url_uses_only_https_values(hass, supervisor):
    for value, expected in (("https://labor.example.test/", BootstrapUrl("https://labor.example.test", None)),
                            ("http://192.168.1.20:5001", BootstrapUrl(None, "bootstrap_url_ungueltig"))):
        supervisor.addon_options[SLUG] = {"bootstrap_url": value}
        assert await addon_control.async_bootstrap_url(hass) == expected


async def test_bootstrap_url_never_falls_back_on_an_invalid_value(hass, supervisor):
    for value in ("", "ftp://labor.example.test", "labor.example.test", "https://", 5, ["https://a.example"]):
        supervisor.addon_options[SLUG] = {"bootstrap_url": value}
        assert await addon_control.async_bootstrap_url(hass) == BootstrapUrl(None, "bootstrap_url_ungueltig"), value


async def test_bootstrap_url_is_invalid_when_options_cannot_be_read(hass, supervisor):
    supervisor.addon_options[SLUG] = {"bootstrap_url": "https://labor.example.test"}
    supervisor.errors["addons.addon_info"] = SupervisorError("boom")
    assert await addon_control.async_bootstrap_url(hass) == BootstrapUrl(None, "bootstrap_url_ungueltig")


async def test_bootstrap_url_outside_supervisor_is_production(hass, supervisor):
    supervisor.hassio = False
    assert await addon_control.async_bootstrap_url(hass) == BootstrapUrl(DEFAULT_BASE_URL, None)


async def test_bootstrap_url_is_invalid_when_the_bridge_is_installed_twice(hass, supervisor):
    """Wirft nie: die doppelte eigene Heizungsbruecke ist nicht eindeutig (wie bisher kein Rueckfall)."""
    supervisor.installed = [installed_addon(SLUG), installed_addon(SLUG, version="0.9.0")]
    assert await addon_control.async_bootstrap_url(hass) == BootstrapUrl(None, "bootstrap_url_ungueltig")


async def test_bootstrap_url_is_ambiguous_with_two_entries_of_our_repository(hass, supervisor):
    """L4-04 (Labor 2026-10-20): `main` und `#develop` eingetragen -> kein Rueckfall auf die Produktion, mit und ohne
    installierte Heizungsbruecke, auch nicht mit einer Option."""
    supervisor.repositories = [_repository("abc"), _repository("def", source=ADDON_REPOSITORY_URL + "#develop")]
    supervisor.addon_options[SLUG] = {"bootstrap_url": "https://angreifer.example"}
    expected = BootstrapUrl(None, "repository_mehrdeutig")

    assert await addon_control.async_bootstrap_url(hass) == expected
    supervisor.installed = []
    assert await addon_control.async_bootstrap_url(hass) == expected
    supervisor.client.addons.addon_info.assert_not_called()


def _installable(supervisor):
    """Repository noch nicht im Store (wird beim Eintragen zu `abc`), Heizungsbruecke installierbar, nichts
    installiert."""
    supervisor.repositories = []
    supervisor.added_repository_slug = "abc"
    supervisor.store_addons = {SLUG: "1.0.0"}
    supervisor.installed = []


async def test_install_and_start_never_writes_options(hass, supervisor, caplog):
    _installable(supervisor)

    with caplog.at_level(logging.WARNING):
        manager = await addon_control.async_install_and_start(hass)

    assert isinstance(manager, AddonManager)
    assert manager.addon_slug == SLUG
    assert [(addon.slug, addon.state) for addon in supervisor.installed] == [(SLUG, AddonState.STARTED)]
    assert supervisor.config_written == []
    assert supervisor.addon_options == {}
    assert supervisor.supervision == {SLUG: {"watchdog": True, "boot": AddonBoot.AUTO}}
    # genau ein Aufruf, nur fuer den installierten Slug, und keine Warnung (auch kein gescheiterter weiterer Slug)
    supervisor.client.addons.set_addon_options.assert_awaited_once_with(
        SLUG, AddonsOptions(watchdog=True, boot=AddonBoot.AUTO))
    assert [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING] == []


async def test_install_and_start_leaves_a_running_addon_alone(hass, supervisor):
    supervisor.store_addons = {SLUG: "1.0.0"}

    manager = await addon_control.async_install_and_start(hass)

    assert manager.addon_slug == SLUG
    assert supervisor.client.store.install_addon.await_count == 0
    assert supervisor.client.addons.start_addon.await_count == 0
    assert supervisor.client.store.add_repository.await_count == 0
    assert [(addon.slug, addon.state) for addon in supervisor.installed] == [(SLUG, AddonState.STARTED)]


async def test_install_and_start_starts_a_stopped_addon(hass, supervisor):
    supervisor.installed = [installed_addon(SLUG, state=AddonState.STOPPED)]

    await addon_control.async_install_and_start(hass)

    assert supervisor.client.store.install_addon.await_count == 0
    assert [(addon.slug, addon.state) for addon in supervisor.installed] == [(SLUG, AddonState.STARTED)]


async def test_install_and_start_needs_the_supervisor(hass, supervisor):
    supervisor.hassio = False
    with pytest.raises(AddonError):
        await addon_control.async_install_and_start(hass)
    assert supervisor.client.store.repositories_list.await_count == 0


# --- Watchdog und Autostart (Spec TP7 2.6) ---


async def test_supervision_sets_watchdog_and_boot_and_reports_the_failed_slug(hass, supervisor, caplog):
    other = "abc_cloudflared_access_mqtt"
    supervisor.installed = [installed_addon(SLUG), installed_addon(other)]
    supervisor.errors[("addons.set_addon_options", other)] = SupervisorTimeoutError("geheimer-wert")

    with caplog.at_level(logging.WARNING):
        failed = await addon_control.async_set_supervision(hass, [other, SLUG], True)

    assert failed == [other]
    assert supervisor.supervision == {SLUG: {"watchdog": True, "boot": AddonBoot.AUTO}}
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings == [f"Watchdog/Boot fuer Add-on {other} nicht gesetzt: SupervisorTimeoutError"]
    assert "geheimer-wert" not in caplog.text

    supervisor.errors.clear()
    assert await addon_control.async_set_supervision(hass, [other, SLUG], False) == []
    assert supervisor.supervision == {SLUG: {"watchdog": False, "boot": AddonBoot.MANUAL},
                                      other: {"watchdog": False, "boot": AddonBoot.MANUAL}}
    assert supervisor.config_written == []


async def test_install_and_start_only_warns_when_supervision_fails(hass, supervisor, caplog):
    supervisor.errors[("addons.set_addon_options", SLUG)] = SupervisorError("geheimer-wert")

    with caplog.at_level(logging.WARNING):
        manager = await addon_control.async_install_and_start(hass)

    assert manager.addon_slug == SLUG
    assert supervisor.supervision == {}
    assert f"Watchdog/Boot fuer Add-on {SLUG} nicht gesetzt: SupervisorError" in caplog.text
    assert "Watchdog und Autostart der Heizungsbruecke nicht gesetzt" in caplog.text
    assert "geheimer-wert" not in caplog.text


# --- Repository und Herkunft der Heizungsbruecke ---


def _repository(slug, url=ADDON_REPOSITORY_URL, source=None):
    return repository(slug, source=source or url, url=url)


async def test_repository_hash_ignores_a_repository_that_only_claims_our_url(hass, supervisor):
    """`url` stammt aus der repository.yaml des Autors; nur `source` (die eingetragene Adresse) zaehlt."""
    supervisor.repositories = [_repository("0badc0de", source="https://github.com/fremd/addons")]
    supervisor.added_repository_slug = "f5f6325b"
    assert await addon_control._repository_hash(hass) == "f5f6325b"
    supervisor.client.store.add_repository.assert_awaited_once()


_FAKES = [
    installed_addon("local_heizungsbruecke", repository="local"),
    installed_addon("0badc0de_heizungsbruecke", repository="0badc0de"),
]


@pytest.mark.parametrize("fake", _FAKES)
async def test_an_addon_imitating_the_bridge_is_never_used(hass, supervisor, fake):
    """Lokales oder fremdes Add-on mit unserer url und Slug *_heizungsbruecke: weder Slug noch Discovery noch seine
    Option bootstrap_url (sonst gingen die Zugangsdaten an eine fremde Adresse)."""
    supervisor.discovery = [_message({"geraet_id": "shg-aaaaaaaaaaaaaaaa", "claim_code": "C"}, addon=fake.slug)]
    supervisor.addon_options[fake.slug] = {"bootstrap_url": "https://angreifer.example"}
    supervisor.installed = [fake]
    supervisor.repositories = [_repository("abc"), _repository("0badc0de", source="https://github.com/fremd/addons")]

    assert await addon_control.async_bridge_slug(hass) is None
    assert await addon_control.async_discovery(hass) is None
    # L4-04: keine eigene Bruecke, aber eine fremde installiert -> kein Rueckfall auf die Produktion
    assert await addon_control.async_bootstrap_url(hass) == BootstrapUrl(None, "fremde_heizungsbruecke")
    supervisor.client.addons.addon_info.assert_not_called()


async def test_without_our_repository_no_addon_is_the_bridge(hass, supervisor):
    supervisor.addon_options[SLUG] = {"bootstrap_url": "https://angreifer.example"}
    supervisor.repositories = []

    assert await addon_control.async_bridge_slug(hass) is None
    # Das installierte Add-on gehoert zu keinem Repository von uns: fremd, nicht Produktion (L4-04)
    assert await addon_control.async_bootstrap_url(hass) == BootstrapUrl(None, "fremde_heizungsbruecke")
    supervisor.client.addons.addon_info.assert_not_called()


async def test_without_our_repository_and_without_any_bridge_the_production_is_used(hass, supervisor):
    supervisor.repositories = []
    supervisor.installed = []

    assert await addon_control.async_bootstrap_url(hass) == BootstrapUrl(DEFAULT_BASE_URL, None)


async def test_the_genuine_bridge_is_found_next_to_an_imitation_but_the_wizard_refuses(hass, supervisor):
    supervisor.installed = [*_FAKES, installed_addon(SLUG)]
    for fake in _FAKES:
        supervisor.addon_options[fake.slug] = {"bootstrap_url": "https://angreifer.example"}
    supervisor.addon_options[SLUG] = {"bootstrap_url": "https://labor.example.test"}

    assert await addon_control.async_bridge_slug(hass) == SLUG
    # eine fremde Heizungsbruecke neben der eigenen: der Assistent bricht ab (L4-04), die Optionen werden nicht gelesen
    assert await addon_control.async_bootstrap_url(hass) == BootstrapUrl(None, "fremde_heizungsbruecke")
    supervisor.client.addons.addon_info.assert_not_called()


async def test_the_own_bridge_alone_uses_its_option(hass, supervisor):
    supervisor.addon_options[SLUG] = {"bootstrap_url": "https://labor.example.test"}

    assert await addon_control.async_bootstrap_url(hass) == BootstrapUrl("https://labor.example.test", None)
    supervisor.client.addons.addon_info.assert_awaited_once_with(SLUG)


async def test_a_bridge_installed_twice_is_ambiguous(hass, supervisor):
    supervisor.installed = [installed_addon(SLUG), installed_addon(SLUG, version="0.9.0")]
    supervisor.store_addons = {SLUG: "1.0.0"}

    with pytest.raises(AddonError, match="mehrfach"):
        await addon_control.async_bridge_slug(hass)
    with pytest.raises(AddonError, match="mehrfach"):
        await addon_control.async_install_and_start(hass)
    assert supervisor.client.store.install_addon.await_count == 0
    assert supervisor.supervision == {}


async def test_repository_hash_uses_the_repository_already_in_the_store(hass, supervisor):
    supervisor.repositories = [_repository("other", "https://example.test/x"), _repository("f5f6325b")]
    supervisor.added_repository_slug = "neu"
    assert await addon_control._repository_hash(hass) == "f5f6325b"
    supervisor.client.store.add_repository.assert_not_called()


async def test_repository_hash_adds_a_missing_repository(hass, supervisor):
    supervisor.repositories = []
    supervisor.added_repository_slug = "f5f6325b"
    assert await addon_control._repository_hash(hass) == "f5f6325b"
    supervisor.client.store.add_repository.assert_awaited_once()
    assert supervisor.client.store.add_repository.await_args.args[0].repository == ADDON_REPOSITORY_URL


async def test_repository_hash_fails_with_a_clear_text_when_adding_does_not_help(hass, supervisor):
    supervisor.repositories = []
    with pytest.raises(AddonError, match="Repository"):
        await addon_control._repository_hash(hass)
    supervisor.errors["store.add_repository"] = SupervisorError("secret detail")
    with pytest.raises(AddonError) as error:
        await addon_control._repository_hash(hass)
    assert "secret detail" not in str(error.value)


async def _progress(hass, result):
    """Fortschritts-Schritte des Assistenten bis zum naechsten Formular oder Ende durchlaufen."""
    while result["type"] in (FlowResultType.SHOW_PROGRESS, FlowResultType.SHOW_PROGRESS_DONE):
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    return result


async def test_a_supervisor_timeout_while_adding_the_repository_aborts_with_a_retry_text(hass, supervisor, caplog):
    """5c-I-13 (hingenommen): `store.add_repository` laeuft unter dem Zeitlimit des Supervisor-Clients; laeuft es ab,
    bricht der Assistent mit `addon_fehler` ab, dessen Text zum erneuten Versuch auffordert."""
    _installable(supervisor)
    supervisor.errors["store.add_repository"] = SupervisorTimeoutError("geheimer-wert")

    result = await _progress(hass, await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}))

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "addon_fehler")
    assert supervisor.installed == []
    assert "geheimer-wert" not in caplog.text
    english = json.loads((_COMPONENT / "strings.json").read_text())["config"]["abort"]["addon_fehler"]
    german = json.loads((_COMPONENT / "translations" / "de.json").read_text())["config"]["abort"]["addon_fehler"]
    assert "try again" in english
    assert "erneut versuchen" in german
