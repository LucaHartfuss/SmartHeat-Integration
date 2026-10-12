"""Add-on-Steuerung der Integration (Spec TP7 2.5-2.7)."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohasupervisor.exceptions import SupervisorError
from homeassistant.components.hassio import AddonError, AddonState

from custom_components.smartheat import addon_control
from custom_components.smartheat.addon_control import WAIT_DONE, WAIT_FAILED, WAIT_TIMEOUT, StatusListener
from custom_components.smartheat.const import ADDON_REPOSITORY_URL, BRIDGE_ACCESS_OPTIONS, DEFAULT_BASE_URL
from custom_components.smartheat.provisioning import CLOUDFLARED_CLEARED_OPTIONS
from custom_components.smartheat.texts import async_hint

from .addon_fakes import FakeAddon, FakeSupervisor, status_event

AC = "custom_components.smartheat.addon_control"
TENANT = "wohnung1"
REGELT = frozenset({"regelt"})
FAILED = frozenset({"konfigurationsfehler", "zugang_abgelehnt"})
MOSQUITTO = json.dumps({"host": "127.0.0.1", "kind": "mosquitto_cloudflared", "port": 18830}, sort_keys=True)
IOT_CORE = json.dumps({
    "alpn": "x-amzn-mqtt-ca", "ca_pem": "CA", "client_id": TENANT, "host": "iot.example.test", "kind": "iot_core", "port": 443,
}, sort_keys=True)
# Zugang einer Mosquitto-Anlage wie nach dem Wizard (Installations-Token in den Add-on-Optionen).
BRIDGE_ACCESS = {
    "tenant_id": TENANT, "transport": MOSQUITTO, "installation_token": "tok-1", "mqtt_username": "u", "mqtt_password": "p",
}
CLEARED = {key: "" for key in BRIDGE_ACCESS_OPTIONS}


async def test_listener_ignores_foreign_tenants_other_schemas_and_other_setup_ids(hass):
    listener = StatusListener(hass, TENANT)
    hass.bus.async_fire("smartheat_status", status_event("andere", "regelt", setup_id="a"))
    hass.bus.async_fire("smartheat_status", {**status_event(TENANT, "regelt", setup_id="a"), "schema": 1})
    hass.bus.async_fire("smartheat_status", status_event(TENANT, "regelt", setup_id="alt"))

    assert await listener.async_wait(setup_id="a", done=REGELT, failed=FAILED, timeout=0.05) == (WAIT_TIMEOUT, None)
    listener.close()


async def test_listener_reports_a_failure_with_its_reason(hass):
    listener = StatusListener(hass, TENANT)
    hass.bus.async_fire("smartheat_status", status_event(TENANT, "konfigurationsfehler", setup_id="a", grund="Entity fehlt"))

    assert await listener.async_wait(setup_id="a", done=REGELT, failed=FAILED, timeout=1) == (WAIT_FAILED, "Entity fehlt")
    listener.close()


async def test_listener_keeps_waiting_through_other_states(hass):
    listener = StatusListener(hass, TENANT)
    waiter = asyncio.ensure_future(listener.async_wait(setup_id="a", done=REGELT, failed=FAILED, timeout=5))
    await asyncio.sleep(0)

    hass.bus.async_fire("smartheat_status", status_event(TENANT, "startet", setup_id="a"))
    await asyncio.sleep(0)
    assert not waiter.done()

    hass.bus.async_fire("smartheat_status", status_event(TENANT, "regelt", setup_id="a"))
    assert await waiter == (WAIT_DONE, None)
    listener.close()


async def test_a_later_wait_sees_an_event_that_came_in_between(hass):
    # Praezisierung 12: "Erneut pruefen" nach einem Timeout.
    listener = StatusListener(hass, TENANT)
    assert (await listener.async_wait(setup_id="a", done=REGELT, failed=FAILED, timeout=0.01))[0] == WAIT_TIMEOUT

    hass.bus.async_fire("smartheat_status", status_event(TENANT, "regelt", setup_id="a"))

    assert await listener.async_wait(setup_id="a", done=REGELT, failed=FAILED, timeout=0.01) == (WAIT_DONE, None)
    listener.close()


async def test_closed_listener_no_longer_listens(hass):
    listener = StatusListener(hass, TENANT)
    listener.close()

    hass.bus.async_fire("smartheat_status", status_event(TENANT, "regelt", setup_id="a"))

    assert (await listener.async_wait(setup_id="a", done=REGELT, failed=FAILED, timeout=0.01))[0] == WAIT_TIMEOUT


async def test_update_addon_options_merges_and_keeps_everything_else():
    log = []
    addon = FakeAddon("a_heizungsbruecke", log, options={"tenant_id": "t", "mqtt_password": "geheim", "room_sensors": ["a"]})

    merged = await addon_control.async_update_addon_options(addon, {"room_sensors": ["b"], "setup_id": "x"})

    assert merged == {"tenant_id": "t", "mqtt_password": "geheim", "room_sensors": ["b"], "setup_id": "x"}
    assert log == [("options", "a_heizungsbruecke", merged)]


async def test_set_supervision_turns_watchdog_and_boot_on_or_off(hass, monkeypatch):
    log = []
    monkeypatch.setattr(f"{AC}.get_supervisor_client", lambda hass: FakeSupervisor(log))

    assert await addon_control.async_set_supervision(hass, ["a_heizungsbruecke", "a_cloudflared"], True) == []
    assert await addon_control.async_set_supervision(hass, ["a_heizungsbruecke"], False) == []

    assert log == [
        ("supervision", "a_heizungsbruecke", {"boot": "auto", "watchdog": True}),
        ("supervision", "a_cloudflared", {"boot": "auto", "watchdog": True}),
        ("supervision", "a_heizungsbruecke", {"boot": "manual", "watchdog": False}),
    ]


async def test_set_supervision_failure_is_only_reported(hass, monkeypatch, caplog):
    monkeypatch.setattr(f"{AC}.get_supervisor_client", lambda hass: FakeSupervisor([], error=SupervisorError("weg")))

    assert await addon_control.async_set_supervision(hass, ["a_heizungsbruecke"], True) == ["a_heizungsbruecke"]
    assert "Watchdog/Boot" in caplog.text


# --- Entfernen (Spec TP7 2.7, Review Focus 4) ---

@pytest.fixture
def dismissed(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "homeassistant.components.persistent_notification.async_dismiss",
        lambda hass, notification_id: calls.append(notification_id),
    )
    return calls


@pytest.fixture
def created(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "homeassistant.components.persistent_notification.async_create",
        lambda hass, message, title=None, notification_id=None: calls.append((notification_id, message)),
    )
    return calls


class FakeServer:
    def __init__(self, log, status=204):
        self.log = log
        self.status = status

    def client(self, session, base_url):
        server = self

        class _Client:
            async def delete_installation(self, tenant_id, token):
                server.log.append(("revoke", tenant_id, {"base_url": base_url, "token": token}))
                return server.status

        return _Client()


def _addons(
    hass, monkeypatch, log, *, answer=True, answer_boost="keiner", bridge_error=None, server_status=204,
    bridge_options=None,
):
    def _answer(addon):
        if answer:
            grund = None if answer_boost == "keiner" else "Zurücksetzen der Anlage scheitert, neuer Versuch in 300 s"
            hass.bus.async_fire("smartheat_status", status_event(TENANT, "abgemeldet", grund=grund, boost=answer_boost))

    bridge = FakeAddon(
        "a_heizungsbruecke", log, on_restart=_answer, error=bridge_error,
        options=bridge_options if bridge_options is not None else dict(BRIDGE_ACCESS),
    )
    cloudflared = FakeAddon("a_cloudflared_access_mqtt", log, options={
        "hostname": "h", "local_port": 18830, "service_token_id": "i", "service_token_secret": "s",
    })
    monkeypatch.setattr(f"{AC}.async_find_addon_managers", AsyncMock(
        return_value={"heizungsbruecke": bridge, "cloudflared_access_mqtt": cloudflared},
    ))
    monkeypatch.setattr(f"{AC}.get_supervisor_client", lambda hass: FakeSupervisor(log))
    server = FakeServer(log, server_status)
    monkeypatch.setattr(f"{AC}.HeizungsserverClient", server.client)
    monkeypatch.setattr(f"{AC}.async_get_clientsession", lambda hass: None)
    return bridge, cloudflared


async def test_sign_off_order(hass, monkeypatch, dismissed):
    log = []
    bridge, cloudflared = _addons(hass, monkeypatch, log)

    await addon_control.async_sign_off(hass, TENANT)

    assert [entry[:2] for entry in log] == [
        ("options", "a_heizungsbruecke"), ("restart", "a_heizungsbruecke"),
        ("stop", "a_heizungsbruecke"), ("stop", "a_cloudflared_access_mqtt"),
        ("supervision", "a_heizungsbruecke"), ("supervision", "a_cloudflared_access_mqtt"),
        ("revoke", TENANT),
        ("options", "a_heizungsbruecke"), ("options", "a_cloudflared_access_mqtt"),
    ]
    assert log[6][2] == {"base_url": "https://accounts.hartfussha.org", "token": "tok-1"}
    assert log[0][2]["abgemeldet"] is True
    assert log[4][2] == {"boot": "manual", "watchdog": False}
    assert bridge.options == {"tenant_id": TENANT, **CLEARED, "abgemeldet": True}
    assert cloudflared.options == {"local_port": 18830, **CLOUDFLARED_CLEARED_OPTIONS}
    assert dismissed == ["smartheat_wohnung1_addon"]


@pytest.mark.parametrize("boost", ["komfort", "notfall"])
async def test_sign_off_keeps_the_bridge_running_while_its_boost_is_not_reset(hass, monkeypatch, dismissed, caplog, boost):
    """TP7-Gates: scheitert das Zuruecksetzen, bleibt die Heizungsbruecke samt Watchdog/Boot
    laufen und wiederholt es selbst; Cloudflared, Widerruf und Leeren der Zugangsdaten laufen
    trotzdem."""
    log = []
    bridge, cloudflared = _addons(hass, monkeypatch, log, answer_boost=boost)

    await addon_control.async_sign_off(hass, TENANT)

    assert [entry[:2] for entry in log] == [
        ("options", "a_heizungsbruecke"), ("restart", "a_heizungsbruecke"),
        ("stop", "a_cloudflared_access_mqtt"),
        ("supervision", "a_cloudflared_access_mqtt"),
        ("revoke", TENANT),
        ("options", "a_heizungsbruecke"), ("options", "a_cloudflared_access_mqtt"),
    ]
    assert bridge.options == {"tenant_id": TENANT, **CLEARED, "abgemeldet": True}
    assert "laeuft weiter" in caplog.text


async def test_revoke_uses_the_base_url_from_the_bridge_options(hass, monkeypatch, dismissed):
    log = []
    _addons(hass, monkeypatch, log, bridge_options={**BRIDGE_ACCESS, "accounts_api_base_url": "https://test.example"})

    await addon_control.async_sign_off(hass, TENANT)

    revoke = [entry for entry in log if entry[0] == "revoke"]
    assert revoke == [("revoke", TENANT, {"base_url": "https://test.example", "token": "tok-1"})]


@pytest.mark.parametrize("server_status,message", [
    (401, "kennt die Zugangsdaten nicht"), (500, "HTTP 500"), (None, "nicht erreichbar"),
])
async def test_remove_completes_when_the_server_refuses_or_is_away(hass, monkeypatch, dismissed, caplog, server_status, message):
    log = []
    bridge, cloudflared = _addons(hass, monkeypatch, log, server_status=server_status, bridge_options={
        **BRIDGE_ACCESS, "installation_token": "geheim-tok-123", "mqtt_password": "geheim-pw-123",
    })

    await addon_control.async_sign_off(hass, TENANT)

    assert bridge.options["mqtt_password"] == "" and cloudflared.options["service_token_secret"] == ""
    assert dismissed == ["smartheat_wohnung1_addon"]
    assert message in caplog.text
    assert "geheim-pw-123" not in caplog.text and "geheim-tok-123" not in caplog.text


async def test_remove_completes_when_the_server_call_raises_unexpectedly(hass, monkeypatch, dismissed, caplog):
    """F8 (finale Review, TP8): api_client faengt ClientError/TimeoutError selbst ab, aber ein
    unerwarteter Fehler (z. B. RuntimeError durch einen Bug) darf async_sign_off nicht mit einer
    Exception abbrechen lassen -- das Leeren der Add-on-Optionen und das Entfernen der
    Benachrichtigung muessen trotzdem laufen."""
    class _BrokenClient:
        async def delete_installation(self, tenant_id, token):
            raise RuntimeError("unerwarteter Bug")

    log = []
    bridge, cloudflared = _addons(hass, monkeypatch, log, bridge_options={
        **BRIDGE_ACCESS, "installation_token": "geheim-tok-123", "mqtt_password": "geheim-pw-123",
    })
    monkeypatch.setattr(f"{AC}.HeizungsserverClient", lambda session, base_url: _BrokenClient())

    await addon_control.async_sign_off(hass, TENANT)  # darf nicht werfen

    assert bridge.options["mqtt_password"] == "" and cloudflared.options["service_token_secret"] == ""
    assert dismissed == ["smartheat_wohnung1_addon"]
    assert "unerwartet" in caplog.text
    assert "geheim-pw-123" not in caplog.text and "geheim-tok-123" not in caplog.text


async def test_no_revoke_without_credentials(hass, monkeypatch, dismissed, caplog):
    log = []
    _addons(hass, monkeypatch, log, bridge_options={**BRIDGE_ACCESS, "installation_token": ""})

    await addon_control.async_sign_off(hass, TENANT)

    assert "revoke" not in [entry[0] for entry in log]


@pytest.mark.parametrize("transport, credentials", [
    (MOSQUITTO, {"mqtt_username": "u", "mqtt_password": "p", "tls_certificate": "", "tls_private_key": ""}),
    (IOT_CORE, {"mqtt_username": "", "mqtt_password": "", "tls_certificate": "CERT", "tls_private_key": "KEY"}),
])
async def test_removal_revokes_with_the_installation_token_and_clears_every_access_option(
    hass, monkeypatch, dismissed, transport, credentials,
):
    log = []
    bridge, cloudflared = _addons(hass, monkeypatch, log, bridge_options={
        **BRIDGE_ACCESS, "transport": transport, **credentials, "accounts_api_base_url": "https://a.example.test",
    })

    problems = await addon_control.async_sign_off(hass, TENANT)

    assert problems == []
    assert [entry for entry in log if entry[0] == "revoke"] == [
        ("revoke", TENANT, {"base_url": "https://a.example.test", "token": "tok-1"}),
    ]
    assert all(bridge.options[key] == "" for key in BRIDGE_ACCESS_OPTIONS)
    assert cloudflared.options == {"local_port": 18830, **CLOUDFLARED_CLEARED_OPTIONS}


async def test_removal_without_a_token_skips_the_revocation(hass, monkeypatch, dismissed):
    log = []
    _addons(hass, monkeypatch, log, bridge_options={**BRIDGE_ACCESS, "installation_token": ""})

    problems = await addon_control.async_sign_off(hass, TENANT)

    assert problems == []  # kein PROBLEM_REVOKE
    assert "revoke" not in [entry[0] for entry in log]


async def test_remove_succeeds_when_the_addon_never_answers(hass, monkeypatch, dismissed, created, caplog):
    monkeypatch.setattr(f"{AC}.SIGN_OFF_WAIT_SECONDS", 0.05)
    log = []
    bridge, cloudflared = _addons(hass, monkeypatch, log, answer=False)

    await addon_control.async_sign_off(hass, TENANT)

    assert ("stop", "a_heizungsbruecke") in [entry[:2] for entry in log]
    assert bridge.options["mqtt_password"] == "" and cloudflared.options["service_token_secret"] == ""
    assert "keine Abmeldung" in caplog.text


async def test_remove_succeeds_when_the_bridge_fails_everywhere(hass, monkeypatch, dismissed, created):
    log = []
    _, cloudflared = _addons(hass, monkeypatch, log, bridge_error=AddonError("kaputt"))

    await addon_control.async_sign_off(hass, TENANT)  # darf nicht werfen

    assert ("stop", "a_cloudflared_access_mqtt") in [entry[:2] for entry in log]
    assert cloudflared.options["service_token_id"] == ""
    # Watchdog/Boot wird trotz der defekten Bruecke fuer beide (noch vorhandenen) Add-ons
    # versucht -- der Fehler betrifft nur die AddonManager-Aufrufe der Bruecke, nicht den
    # separaten Supervisor-Aufruf fuer Watchdog/Boot.
    assert ("supervision", "a_heizungsbruecke") in [entry[:2] for entry in log]
    assert ("supervision", "a_cloudflared_access_mqtt") in [entry[:2] for entry in log]
    assert "revoke" not in [entry[0] for entry in log]
    assert dismissed == ["smartheat_wohnung1_addon"]


async def test_sign_off_never_logs_the_supervisor_text(hass, monkeypatch, dismissed, created, caplog):
    # Audit 4, A4-40 (IT-5): der Supervisor kann Optionswerte zitieren
    secret = "geheim-pw-4711"
    _addons(hass, monkeypatch, [], bridge_error=AddonError(f"invalid option mqtt_password={secret}"))

    await addon_control.async_sign_off(hass, TENANT)

    assert secret not in caplog.text
    assert "AddonError" in caplog.text


async def test_supervision_failure_never_logs_the_supervisor_text(hass, monkeypatch, caplog):
    # Audit 4, A4-40: auch der Watchdog/Boot-Aufruf loggt nur den Typnamen des Supervisor-Fehlers
    secret = "geheim-pw-4711"
    error = SupervisorError(f"invalid option mqtt_password={secret}")
    monkeypatch.setattr(f"{AC}.get_supervisor_client", lambda hass: FakeSupervisor([], error=error))

    await addon_control.async_set_supervision(hass, ["a_heizungsbruecke"], True)

    assert secret not in caplog.text
    assert "SupervisorError" in caplog.text


async def test_remove_succeeds_without_supervisor(hass, monkeypatch, dismissed):
    monkeypatch.setattr(f"{AC}.async_find_addon_managers", AsyncMock(side_effect=AddonError("weg")))

    await addon_control.async_sign_off(hass, TENANT)  # darf nicht werfen

    assert dismissed == ["smartheat_wohnung1_addon"]


async def test_sign_off_without_hassio_still_dismisses_the_notification(hass, dismissed):
    # Kein Hass.io geladen (Core/Container-Installation, aus einem Backup wiederhergestellt):
    # der rohe get_supervisor_client() wuerde mit einem KeyError abbrechen. Das faengt jetzt
    # async_find_addon_managers als AddonError ab (Fix Runde 1, hier bewusst NICHT gemockt,
    # damit der echte Codepfad durchlaeuft), sodass das Entfernen trotzdem zu Ende laeuft.
    await addon_control.async_sign_off(hass, TENANT)  # darf nicht werfen

    assert dismissed == ["smartheat_wohnung1_addon"]


async def test_listener_ignores_an_unknown_status(hass):
    # Praezisierung 15: ein (kuenftiger, hier noch unbekannter) Status darf kein Warten
    # faelschlich abschliessen, selbst wenn er zufaellig im uebergebenen done-Set steht.
    listener = StatusListener(hass, TENANT)
    hass.bus.async_fire("smartheat_status", status_event(TENANT, "voellig_neuer_status", setup_id="a"))

    outcome = await listener.async_wait(
        setup_id="a", done=frozenset({"voellig_neuer_status"}), failed=FAILED, timeout=0.05,
    )

    assert outcome == (WAIT_TIMEOUT, None)
    listener.close()


# --- Ein Eintrag je HA, Meldung offener Schritte (TP12c, AU-011/AU-021) ---

async def test_sign_off_leaves_an_addon_of_another_tenant_alone(hass, monkeypatch, dismissed, created):
    log = []
    _addons(hass, monkeypatch, log, bridge_options={**BRIDGE_ACCESS, "tenant_id": "haus2"})

    problems = await addon_control.async_sign_off(hass, TENANT)

    assert problems == ["foreign_tenant"]
    assert log == []  # nichts abgemeldet, gestoppt, widerrufen oder geleert
    notification_id, message = created[0]
    assert notification_id == "smartheat_wohnung1_removal"
    assert message.splitlines() == [
        await async_hint(hass, "removal_incomplete"), f"- {await async_hint(hass, 'open_step_foreign_tenant')}",
    ]
    assert dismissed == ["smartheat_wohnung1_addon"]


async def test_sign_off_without_tenant_id_in_the_addon_is_not_foreign(hass, monkeypatch, dismissed, created):
    """Review Focus 5."""
    log = []
    _addons(hass, monkeypatch, log, bridge_options={key: value for key, value in BRIDGE_ACCESS.items() if key != "tenant_id"})

    problems = await addon_control.async_sign_off(hass, TENANT)

    assert problems == []
    assert ("stop", "a_heizungsbruecke") in [entry[:2] for entry in log]
    assert created == []


async def test_successful_sign_off_creates_no_notification(hass, monkeypatch, dismissed, created):
    _addons(hass, monkeypatch, [])

    assert await addon_control.async_sign_off(hass, TENANT) == []
    assert created == []


async def test_sign_off_without_supervisor_reports_it(hass, monkeypatch, dismissed, created):
    monkeypatch.setattr(f"{AC}.async_find_addon_managers", AsyncMock(side_effect=AddonError("weg")))

    assert await addon_control.async_sign_off(hass, TENANT) == ["supervisor"]
    assert created[0][0] == "smartheat_wohnung1_removal"


async def test_unconfirmed_sign_off_warns_about_boost_values(hass, monkeypatch, dismissed, created):
    monkeypatch.setattr(f"{AC}.SIGN_OFF_WAIT_SECONDS", 0.05)
    _addons(hass, monkeypatch, [], answer=False)

    assert await addon_control.async_sign_off(hass, TENANT) == ["no_sign_off"]
    text = created[0][1]
    assert "boost values" in text and "local maximum values" in text
    assert "1.5" not in text and "1,5" not in text


async def test_unreadable_bridge_options_report_the_revoke(hass, monkeypatch, dismissed, created):
    """Ohne lesbare Optionen kennt die Integration die Zugangsdaten nicht: der Widerruf bleibt offen."""
    log = []
    bridge, _ = _addons(hass, monkeypatch, log)

    async def unreadable():
        raise AddonError("kaputt")

    bridge.async_get_addon_info = unreadable

    problems = await addon_control.async_sign_off(hass, TENANT, notify=False)

    assert "revoke" not in [entry[0] for entry in log]
    assert "revoke" in problems


@pytest.mark.parametrize("server_status, problems", [(204, []), (401, []), (500, ["revoke"]), (None, ["revoke"])])
async def test_revoke_outcome_is_reported(hass, monkeypatch, dismissed, created, server_status, problems):
    _addons(hass, monkeypatch, [], server_status=server_status)

    assert await addon_control.async_sign_off(hass, TENANT) == problems


async def test_sign_off_can_leave_the_notification_to_the_caller(hass, monkeypatch, dismissed, created):
    monkeypatch.setattr(f"{AC}.async_find_addon_managers", AsyncMock(side_effect=AddonError("weg")))

    assert await addon_control.async_sign_off(hass, TENANT, notify=False) == ["supervisor"]
    assert created == []


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


async def test_bootstrap_url_uses_valid_http_and_https_values(hass, monkeypatch):
    for value, expected in (("https://labor.example.test/", "https://labor.example.test"),
                            ("http://192.168.1.5:8080", "http://192.168.1.5:8080")):
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


async def test_bridge_manager_is_none_when_not_installed_or_ambiguous(hass, monkeypatch):
    monkeypatch.setattr(addon_control, "_manager", lambda hass, slug: SimpleNamespace(addon_slug=slug))
    _patch(monkeypatch, _supervisor())
    assert (await addon_control.async_bridge_manager(hass)).addon_slug == SLUG
    _patch(monkeypatch, _supervisor(installed=False))
    assert await addon_control.async_bridge_manager(hass) is None
    twice = [SimpleNamespace(slug=SLUG, repository="abc", url=ADDON_REPOSITORY_URL, version="1.0.0")] * 2
    _patch(monkeypatch, _supervisor(installed_addons=twice))
    assert await addon_control.async_bridge_manager(hass) is None
    _patch(monkeypatch, _supervisor(), hassio=False)
    assert await addon_control.async_bridge_manager(hass) is None


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
