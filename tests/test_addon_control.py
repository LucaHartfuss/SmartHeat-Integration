"""Add-on-Steuerung der Integration: Add-on finden, installieren und starten, Discovery und
bootstrap_url lesen. Der Supervisor ist die Attrappe `supervisor` (supervisor_fakes.py), keine eigene Funktion wird
ersetzt."""
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
from custom_components.smartheat.addon_control import BootstrapUrl, NachweisFehlt
from custom_components.smartheat.const import ADDON_REPOSITORY_URL, DEFAULT_BASE_URL, DOMAIN, NACHWEIS_EVENT

from .supervisor_fakes import BRIDGE_SLUG, discovery_message, installed_addon, repository

# --- Add-on finden, installieren und starten (ohne Optionen), Discovery und bootstrap_url lesen ---

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
    """Hingenommen: eine Zeitangabe liefert der Supervisor nicht; „die juengste“ ist die letzte der Liste
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
    """Hingenommen: jede Abfrage liest Store und Add-on-Liste neu; ein inzwischen entferntes Add-on
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
    """Wirft nie: die doppelte eigene Heizungsbruecke ist nicht eindeutig (kein Rueckfall)."""
    supervisor.installed = [installed_addon(SLUG), installed_addon(SLUG, version="0.9.0")]
    assert await addon_control.async_bootstrap_url(hass) == BootstrapUrl(None, "bootstrap_url_ungueltig")


async def test_bootstrap_url_is_ambiguous_with_two_entries_of_our_repository(hass, supervisor):
    """`main` und `#develop` eingetragen -> kein Rueckfall auf die Produktion, mit und ohne
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


# --- Watchdog und Autostart ---


async def test_supervision_sets_watchdog_and_boot_and_reports_a_failure(hass, supervisor, caplog):
    supervisor.errors[("addons.set_addon_options", SLUG)] = SupervisorTimeoutError("geheimer-wert")

    with caplog.at_level(logging.WARNING):
        assert await addon_control.async_set_supervision(hass, SLUG) is False

    assert supervisor.supervision == {}
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings == [f"Watchdog/Boot fuer Add-on {SLUG} nicht gesetzt: SupervisorTimeoutError"]
    assert "geheimer-wert" not in caplog.text

    supervisor.errors.clear()
    assert await addon_control.async_set_supervision(hass, SLUG) is True
    assert supervisor.supervision == {SLUG: {"watchdog": True, "boot": AddonBoot.AUTO}}
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
    # Keine eigene Bruecke, aber eine fremde installiert -> kein Rueckfall auf die Produktion
    assert await addon_control.async_bootstrap_url(hass) == BootstrapUrl(None, "fremde_heizungsbruecke")
    supervisor.client.addons.addon_info.assert_not_called()


async def test_without_our_repository_no_addon_is_the_bridge(hass, supervisor):
    supervisor.addon_options[SLUG] = {"bootstrap_url": "https://angreifer.example"}
    supervisor.repositories = []

    assert await addon_control.async_bridge_slug(hass) is None
    # Das installierte Add-on gehoert zu keinem Repository von uns: fremd, nicht Produktion
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
    # eine fremde Heizungsbruecke neben der eigenen: der Assistent bricht ab, die Optionen werden nicht gelesen
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
    """Hingenommen: `store.add_repository` laeuft unter dem Zeitlimit des Supervisor-Clients; laeuft es ab,
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


# --- Nachweis vom Add-on ---

NACHWEIS_ID = "shg-aaaaaaaaaaaaaaaa"
NACHWEIS_DATEN = {"schema": 1, "geraet_id": NACHWEIS_ID, "zeitstempel": 1761800000, "signatur": "MEUC-sig_geheim9"}


def _add_on_antwortet(hass, supervisor, *ereignisse):
    """Das Add-on antwortet auf jede stdin-Zeile mit diesen Ereignissen (in dieser Reihenfolge)."""
    def hook(slug, zeile):
        for daten in ereignisse:
            hass.bus.async_fire(NACHWEIS_EVENT, daten)

    supervisor.stdin_hook = hook


async def test_nachweis_schreibt_den_befehl_und_liefert_die_signatur_des_ereignisses(hass, supervisor):
    _add_on_antwortet(hass, supervisor, NACHWEIS_DATEN)

    nachweis = await addon_control.async_nachweis_anfordern(hass, NACHWEIS_ID, 5)

    # Ein Zeilenende aus STDIN_ZEILE_FORMAT, eines haengt der Supervisor selbst an.
    assert supervisor.stdin == [(SLUG, b"nachweis shg-aaaaaaaaaaaaaaaa\n\n")]
    assert nachweis == {"geraet_id": NACHWEIS_ID, "zeitstempel": 1761800000, "signatur": "MEUC-sig_geheim9"}


@pytest.mark.parametrize("falsch", [
    {**NACHWEIS_DATEN, "geraet_id": "shg-bbbbbbbbbbbbbbbb"},
    {**NACHWEIS_DATEN, "schema": 2}, {**NACHWEIS_DATEN, "schema": True}, {**NACHWEIS_DATEN, "schema": "1"},
    {k: v for k, v in NACHWEIS_DATEN.items() if k != "signatur"},
    {k: v for k, v in NACHWEIS_DATEN.items() if k != "zeitstempel"},
    {**NACHWEIS_DATEN, "signatur": ""}, {**NACHWEIS_DATEN, "signatur": 7},
    {**NACHWEIS_DATEN, "zeitstempel": "1761800000"}, {**NACHWEIS_DATEN, "zeitstempel": True},
    {**NACHWEIS_DATEN, "zeitstempel": 0}, {**NACHWEIS_DATEN, "zeitstempel": -5},
    {**NACHWEIS_DATEN, "signatur": "a" * 513},
    {**NACHWEIS_DATEN, "signatur": "abc\r\nX-Evil: 1"}, {**NACHWEIS_DATEN, "signatur": "abc def"},
    {**NACHWEIS_DATEN, "signatur": "a" * 512 + "\n"},
])
async def test_ein_unbrauchbares_ereignis_wird_uebergangen_das_naechste_gueltige_zaehlt(hass, supervisor, falsch):
    gut = {**NACHWEIS_DATEN, "signatur": "gueltig"}
    _add_on_antwortet(hass, supervisor, falsch, gut)

    nachweis = await addon_control.async_nachweis_anfordern(hass, NACHWEIS_ID, 5)

    assert nachweis["signatur"] == "gueltig"


@pytest.mark.parametrize("grenze", [{"zeitstempel": 1}, {"signatur": "a" * 512}, {"signatur": "aB0_-+/="}])
async def test_grenzwerte_des_ereignisses_gelten(hass, supervisor, grenze):
    _add_on_antwortet(hass, supervisor, {**NACHWEIS_DATEN, **grenze})

    nachweis = await addon_control.async_nachweis_anfordern(hass, NACHWEIS_ID, 5)

    assert {k: nachweis[k] for k in grenze} == grenze


async def test_nur_das_erste_gueltige_ereignis_zaehlt_und_zusatzfelder_fallen_weg(hass, supervisor, caplog):
    _add_on_antwortet(hass, supervisor, {**NACHWEIS_DATEN, "signatur": "erste", "fremd": 1},
                      {**NACHWEIS_DATEN, "signatur": "zweite"})

    assert await addon_control.async_nachweis_anfordern(hass, NACHWEIS_ID, 5) == {
        "geraet_id": NACHWEIS_ID, "zeitstempel": 1761800000, "signatur": "erste"}
    await hass.async_block_till_done()
    # das zweite Ereignis laeuft ohne Fehler ins Leere (kein set_result auf eine fertige Zukunft)
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


async def test_ein_ereignis_vor_dem_befehl_zaehlt_nicht(hass, supervisor):
    """Ein altes Ereignis (einmalig, Zeitfenster abgelaufen) darf nie als Antwort gelten: nur was nach dem Befehl kommt."""
    hass.bus.async_fire(NACHWEIS_EVENT, NACHWEIS_DATEN)
    await hass.async_block_till_done()

    with pytest.raises(NachweisFehlt):
        await addon_control.async_nachweis_anfordern(hass, NACHWEIS_ID, 0.05)


async def test_ohne_ereignis_in_der_wartefrist_fehlt_der_nachweis_und_der_lauscher_ist_weg(hass, supervisor):
    with pytest.raises(NachweisFehlt):
        await addon_control.async_nachweis_anfordern(hass, NACHWEIS_ID, 0.05)

    assert supervisor.stdin == [(SLUG, b"nachweis shg-aaaaaaaaaaaaaaaa\n\n")]
    assert NACHWEIS_EVENT not in hass.bus.async_listeners()


async def test_nach_erfolg_ist_der_lauscher_weg(hass, supervisor):
    _add_on_antwortet(hass, supervisor, NACHWEIS_DATEN)

    await addon_control.async_nachweis_anfordern(hass, NACHWEIS_ID, 5)

    assert NACHWEIS_EVENT not in hass.bus.async_listeners()


@pytest.mark.parametrize("geraet_id", [None, 7, "", "shg-a", "shg-aaaaaaaaaaaaaaaa\n", "shg-aaaaaaaaaaaaaaaa x",
                                       "SHG-AAAAAAAAAAAAAAAA"])
async def test_ohne_gueltige_geraete_id_wird_nichts_geschrieben(hass, supervisor, geraet_id):
    with pytest.raises(NachweisFehlt):
        await addon_control.async_nachweis_anfordern(hass, geraet_id, 0.05)
    assert supervisor.stdin == []


@pytest.mark.parametrize("aufbau", ["kein_supervisor", "bruecke_fehlt", "mehrfach", "gestoppt", "supervisor_fehler"])
async def test_ohne_erreichbare_bruecke_fehlt_der_nachweis_ohne_wartefrist(hass, supervisor, caplog, aufbau):
    if aufbau == "kein_supervisor":
        supervisor.hassio = False
    elif aufbau == "bruecke_fehlt":
        supervisor.installed = []
    elif aufbau == "mehrfach":
        supervisor.installed = [installed_addon(SLUG), installed_addon(SLUG)]
    elif aufbau == "gestoppt":
        supervisor.installed = [installed_addon(SLUG, state=AddonState.STOPPED)]
    else:
        supervisor.errors["addons.write_addon_stdin"] = SupervisorError("Supervisor GEHEIM")
    _add_on_antwortet(hass, supervisor, NACHWEIS_DATEN)

    with caplog.at_level(logging.DEBUG), pytest.raises(NachweisFehlt):
        await addon_control.async_nachweis_anfordern(hass, NACHWEIS_ID, 30)

    assert NACHWEIS_EVENT not in hass.bus.async_listeners()
    assert "GEHEIM" not in caplog.text and NACHWEIS_ID not in caplog.text and "sig_geheim" not in caplog.text


async def test_der_nachweis_wird_nie_geloggt(hass, supervisor, caplog):
    caplog.set_level(logging.DEBUG)
    _add_on_antwortet(hass, supervisor, NACHWEIS_DATEN)

    await addon_control.async_nachweis_anfordern(hass, NACHWEIS_ID, 5)

    assert "sig_geheim" not in caplog.text and "1761800000" not in caplog.text
