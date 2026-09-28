"""Add-on-Steuerung der Integration (Spec TP7 2.5-2.7)."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from aiohasupervisor.exceptions import SupervisorError
from homeassistant.components.hassio import AddonError

from custom_components.smartheat import addon_control
from custom_components.smartheat.addon_control import WAIT_DONE, WAIT_FAILED, WAIT_TIMEOUT, StatusListener

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


def _addons(hass, monkeypatch, log, *, answer=True, bridge_error=None):
    def _answer(addon):
        if answer:
            hass.bus.async_fire("smartheat_status", status_event(TENANT, "abgemeldet"))

    bridge = FakeAddon(
        "a_heizungsbruecke", log, on_restart=_answer, error=bridge_error,
        options={"tenant_id": TENANT, "mqtt_username": "u", "mqtt_password": "p"},
    )
    cloudflared = FakeAddon("a_cloudflared_access_mqtt", log, options={
        "hostname": "h", "local_port": 18830, "service_token_id": "i", "service_token_secret": "s",
    })
    monkeypatch.setattr(f"{AC}.async_find_addon_managers", AsyncMock(
        return_value={"heizungsbruecke": bridge, "cloudflared_access_mqtt": cloudflared},
    ))
    monkeypatch.setattr(f"{AC}.get_supervisor_client", lambda hass: FakeSupervisor(log))
    return bridge, cloudflared


async def test_sign_off_order(hass, monkeypatch, dismissed):
    log = []
    bridge, cloudflared = _addons(hass, monkeypatch, log)

    await addon_control.async_sign_off(hass, TENANT)

    assert [entry[:2] for entry in log] == [
        ("options", "a_heizungsbruecke"), ("restart", "a_heizungsbruecke"),
        ("stop", "a_heizungsbruecke"), ("stop", "a_cloudflared_access_mqtt"),
        ("supervision", "a_heizungsbruecke"), ("supervision", "a_cloudflared_access_mqtt"),
        ("options", "a_heizungsbruecke"), ("options", "a_cloudflared_access_mqtt"),
    ]
    assert log[0][2]["abgemeldet"] is True
    assert log[4][2] == {"boot": "manual", "watchdog": False}
    assert bridge.options == {"tenant_id": TENANT, "mqtt_username": "", "mqtt_password": "", "abgemeldet": True}
    assert cloudflared.options == {"hostname": "h", "local_port": 18830, "service_token_id": "", "service_token_secret": ""}
    assert dismissed == ["smartheat_wohnung1_addon"]


async def test_remove_succeeds_when_the_addon_never_answers(hass, monkeypatch, dismissed, caplog):
    monkeypatch.setattr(f"{AC}.SIGN_OFF_WAIT_SECONDS", 0.05)
    log = []
    bridge, cloudflared = _addons(hass, monkeypatch, log, answer=False)

    await addon_control.async_sign_off(hass, TENANT)

    assert ("stop", "a_heizungsbruecke") in [entry[:2] for entry in log]
    assert bridge.options["mqtt_password"] == "" and cloudflared.options["service_token_secret"] == ""
    assert "keine Abmeldung" in caplog.text


async def test_remove_succeeds_when_the_bridge_fails_everywhere(hass, monkeypatch, dismissed):
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
