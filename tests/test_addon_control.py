"""Add-on-Steuerung der Integration (Spec TP7 2.5-2.7)."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from aiohasupervisor.exceptions import SupervisorError
from homeassistant.components.hassio import AddonError

from custom_components.smartheat import addon_control
from custom_components.smartheat.addon_control import WAIT_DONE, WAIT_FAILED, WAIT_TIMEOUT, StatusListener
from custom_components.smartheat.texts import async_hint

from .addon_fakes import FakeAddon, FakeSupervisor, status_event

AC = "custom_components.smartheat.addon_control"
TENANT = "wohnung1"
REGELT = frozenset({"regelt"})
FAILED = frozenset({"konfigurationsfehler", "zugang_abgelehnt"})


async def test_listener_ignores_foreign_tenants_other_schemas_and_other_setup_ids(hass):
    listener = StatusListener(hass, TENANT)
    hass.bus.async_fire("smartheat_status", status_event("andere", "regelt", setup_id="a"))
    hass.bus.async_fire("smartheat_status", {**status_event(TENANT, "regelt", setup_id="a"), "schema": 2})
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
            async def delete_installation(self, tenant_id, username, password):
                server.log.append(("revoke", tenant_id, {"base_url": base_url, "username": username, "password": password}))
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
        options=bridge_options if bridge_options is not None else {"tenant_id": TENANT, "mqtt_username": "u", "mqtt_password": "p"},
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
    assert log[6][2] == {"base_url": "https://accounts.hartfussha.org", "username": "u", "password": "p"}
    assert log[0][2]["abgemeldet"] is True
    assert log[4][2] == {"boot": "manual", "watchdog": False}
    assert bridge.options == {"tenant_id": TENANT, "mqtt_username": "", "mqtt_password": "", "abgemeldet": True}
    assert cloudflared.options == {"hostname": "h", "local_port": 18830, "service_token_id": "", "service_token_secret": ""}
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
    assert bridge.options == {"tenant_id": TENANT, "mqtt_username": "", "mqtt_password": "", "abgemeldet": True}
    assert "laeuft weiter" in caplog.text


async def test_revoke_uses_the_base_url_from_the_bridge_options(hass, monkeypatch, dismissed):
    log = []
    _addons(hass, monkeypatch, log, bridge_options={
        "tenant_id": TENANT, "mqtt_username": "u", "mqtt_password": "p", "accounts_api_base_url": "https://test.example",
    })

    await addon_control.async_sign_off(hass, TENANT)

    revoke = [entry for entry in log if entry[0] == "revoke"]
    assert revoke == [("revoke", TENANT, {"base_url": "https://test.example", "username": "u", "password": "p"})]


@pytest.mark.parametrize("server_status,message", [
    (401, "kennt die Zugangsdaten nicht"), (500, "HTTP 500"), (None, "nicht erreichbar"),
])
async def test_remove_completes_when_the_server_refuses_or_is_away(hass, monkeypatch, dismissed, caplog, server_status, message):
    log = []
    bridge, cloudflared = _addons(hass, monkeypatch, log, server_status=server_status, bridge_options={
        "tenant_id": TENANT, "mqtt_username": "wohnung1_abc", "mqtt_password": "geheim-pw-123",
    })

    await addon_control.async_sign_off(hass, TENANT)

    assert bridge.options["mqtt_password"] == "" and cloudflared.options["service_token_secret"] == ""
    assert dismissed == ["smartheat_wohnung1_addon"]
    assert message in caplog.text
    assert "geheim-pw-123" not in caplog.text


async def test_remove_completes_when_the_server_call_raises_unexpectedly(hass, monkeypatch, dismissed, caplog):
    """F8 (finale Review, TP8): api_client faengt ClientError/TimeoutError selbst ab, aber ein
    unerwarteter Fehler (z. B. RuntimeError durch einen Bug) darf async_sign_off nicht mit einer
    Exception abbrechen lassen -- das Leeren der Add-on-Optionen und das Entfernen der
    Benachrichtigung muessen trotzdem laufen."""
    class _BrokenClient:
        async def delete_installation(self, tenant_id, username, password):
            raise RuntimeError("unerwarteter Bug")

    log = []
    bridge, cloudflared = _addons(hass, monkeypatch, log, bridge_options={
        "tenant_id": TENANT, "mqtt_username": "wohnung1_abc", "mqtt_password": "geheim-pw-123",
    })
    monkeypatch.setattr(f"{AC}.HeizungsserverClient", lambda session, base_url: _BrokenClient())

    await addon_control.async_sign_off(hass, TENANT)  # darf nicht werfen

    assert bridge.options["mqtt_password"] == "" and cloudflared.options["service_token_secret"] == ""
    assert dismissed == ["smartheat_wohnung1_addon"]
    assert "unerwartet" in caplog.text
    assert "geheim-pw-123" not in caplog.text


async def test_no_revoke_without_credentials(hass, monkeypatch, dismissed, caplog):
    log = []
    _addons(hass, monkeypatch, log, bridge_options={"tenant_id": TENANT, "mqtt_username": "", "mqtt_password": ""})

    await addon_control.async_sign_off(hass, TENANT)

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
    _addons(hass, monkeypatch, log, bridge_options={"tenant_id": "haus2", "mqtt_username": "u", "mqtt_password": "p"})

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
    _addons(hass, monkeypatch, log, bridge_options={"mqtt_username": "u", "mqtt_password": "p"})

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
    assert ("1,5" in text or "1.5" in text) and "25" in text


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
