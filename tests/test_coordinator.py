"""Coordinator, Entities und zweiter Waechter (Spec TP7 1.2, 1.3)."""
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.hassio import AddonError, AddonState
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import State
from pytest_homeassistant_custom_component.common import async_mock_service, mock_restore_cache

from custom_components.smartheat.const import WATCHDOG_ALL_CLEAR_MESSAGE, WATCHDOG_MESSAGES
from custom_components.smartheat.coordinator import Watchdog

from .addon_fakes import FakeAddon, make_entry, status_event

CO = "custom_components.smartheat.coordinator"
TENANT = "wohnung1"
STATUS = "sensor.smartheat_wohnung1_status"
RUNNING = {"heizungsbruecke": True, "cloudflared_access_mqtt": True}


# --- reine Waechter-Logik ---

def test_watchdog_needs_two_checks_before_an_addon_counts_as_stopped():
    watchdog = Watchdog(started_at=0)
    watchdog.event_received(0)
    stopped = {**RUNNING, "cloudflared_access_mqtt": False}

    assert watchdog.check(300, stopped) == (None, [])
    assert watchdog.check(600, stopped) == ("addon_gestoppt", ["cloudflared_access_mqtt"])


def test_watchdog_running_again_resets_the_count():
    watchdog = Watchdog(started_at=0)
    watchdog.event_received(0)
    stopped = {**RUNNING, "heizungsbruecke": False}

    watchdog.check(300, stopped)
    watchdog.check(400, RUNNING)

    assert watchdog.check(500, stopped) == (None, [])


def test_watchdog_restarts_at_most_three_times_per_hour():
    watchdog = Watchdog(started_at=0)
    stopped = {**RUNNING, "heizungsbruecke": False}

    restarts = [watchdog.check(300 * n, stopped)[1] for n in range(1, 8)]

    assert restarts == [[], ["heizungsbruecke"], ["heizungsbruecke"], ["heizungsbruecke"], [], [], []]
    assert watchdog.check(600 + 3600, stopped) == ("addon_gestoppt", ["heizungsbruecke"])


def test_watchdog_no_silence_alarm_within_900_s_after_setup():
    watchdog = Watchdog(started_at=1000)

    assert watchdog.check(1300, RUNNING) == (None, [])
    assert watchdog.check(1600, RUNNING) == (None, [])
    assert watchdog.check(1900, RUNNING) == ("reagiert_nicht", ["heizungsbruecke"])


def test_watchdog_silence_counts_from_the_last_event():
    watchdog = Watchdog(started_at=0)
    watchdog.event_received(500)

    assert watchdog.check(1200, RUNNING) == (None, [])
    assert watchdog.check(1400, RUNNING) == ("reagiert_nicht", ["heizungsbruecke"])


def test_watchdog_silence_does_not_count_while_the_bridge_is_stopped():
    watchdog = Watchdog(started_at=0)

    assert watchdog.check(1000, {**RUNNING, "heizungsbruecke": False}) == (None, [])


# --- Coordinator in HA ---

@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(f"{CO}._now", lambda: now["t"])
    return now


@pytest.fixture
def notes(monkeypatch):
    created, dismissed = [], []
    monkeypatch.setattr(
        "homeassistant.components.persistent_notification.async_create",
        lambda hass, message, title=None, notification_id=None: created.append((notification_id, message)),
    )
    monkeypatch.setattr(
        "homeassistant.components.persistent_notification.async_dismiss",
        lambda hass, notification_id: dismissed.append(notification_id),
    )
    return created, dismissed


async def _setup(hass, monkeypatch, addons):
    monkeypatch.setattr(f"{CO}.async_find_addon_managers", AsyncMock(return_value=addons))
    entry = make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _fire(hass, event):
    hass.bus.async_fire("smartheat_status", event)


async def test_an_event_updates_every_entity(hass, monkeypatch, clock):
    await _setup(hass, monkeypatch, {})

    _fire(hass, status_event(
        TENANT, "notbetrieb", notbetrieb=True, boost="notfall", datenfehler={"art": "lokal", "rollen": ["dat"]},
        kurve=1.1, offset=24.0, letzte_serverantwort="2026-10-01T12:00:05+02:00", abo="inaktiv",
        abo_frist_ende="2026-10-31",
        hinweise={"raumfuehler_ausgefallen": ["sensor.a"], "batterie_niedrig": [], "manueller_eingriff": None},
    ))
    await hass.async_block_till_done()

    status = hass.states.get(STATUS)
    assert status.state == "notbetrieb"
    assert status.attributes["raumfuehler_ausgefallen"] == ["sensor.a"]
    assert hass.states.get("binary_sensor.smartheat_wohnung1_notbetrieb").state == "on"
    fault = hass.states.get("sensor.smartheat_wohnung1_datenfehler")
    assert (fault.state, fault.attributes["rollen"]) == ("lokal", ["dat"])
    assert hass.states.get("sensor.smartheat_wohnung1_boost").state == "notfall"
    assert hass.states.get("sensor.smartheat_wohnung1_heizkurve").state == "1.1"
    assert hass.states.get("sensor.smartheat_wohnung1_offset").state == "24.0"
    assert hass.states.get("sensor.smartheat_wohnung1_abo").attributes["frist_ende"] == "2026-10-31"
    assert hass.states.get("sensor.smartheat_wohnung1_letzte_serverantwort").state == "2026-10-01T10:00:05+00:00"
    assert hass.states.get("sensor.smartheat_wohnung1_addon_version").state == "0.20.0"


@pytest.mark.parametrize("event", [
    status_event("andere", "regelt"),
    {**status_event(TENANT, "regelt"), "schema": 2},
    status_event(TENANT, "bereit"),
])
async def test_foreign_or_unknown_events_are_ignored(hass, monkeypatch, clock, event):
    await _setup(hass, monkeypatch, {})

    _fire(hass, event)
    await hass.async_block_till_done()

    assert hass.states.get(STATUS).state == "unknown"


async def test_entities_restore_the_last_state_after_a_restart(hass, monkeypatch, clock):
    """Review Focus 3."""
    mock_restore_cache(hass, [
        State(STATUS, "notbetrieb", {"grund": None, "raumfuehler_ausgefallen": ["sensor.a"]}),
        State("binary_sensor.smartheat_wohnung1_notbetrieb", "on"),
        State("sensor.smartheat_wohnung1_heizkurve", "1.1"),
        State("sensor.smartheat_wohnung1_letzte_serverantwort", "2026-10-01T10:00:05+00:00"),
        State("sensor.smartheat_wohnung1_datenfehler", "lokal", {"rollen": ["dat"]}),
    ])

    await _setup(hass, monkeypatch, {})

    assert hass.states.get(STATUS).state == "notbetrieb"
    assert hass.states.get(STATUS).attributes["raumfuehler_ausgefallen"] == ["sensor.a"]
    assert hass.states.get("binary_sensor.smartheat_wohnung1_notbetrieb").state == "on"
    assert hass.states.get("sensor.smartheat_wohnung1_heizkurve").state == "1.1"
    assert hass.states.get("sensor.smartheat_wohnung1_letzte_serverantwort").state == "2026-10-01T10:00:05+00:00"
    assert hass.states.get("sensor.smartheat_wohnung1_datenfehler").attributes["rollen"] == ["dat"]


async def test_stopped_addon_is_reported_once_restarted_and_cleared_by_an_event(hass, monkeypatch, clock, notes):
    """Review Focus 2."""
    created, dismissed = notes
    pushes = async_mock_service(hass, "notify", "mobile_app_pixel")
    log = []
    bridge = FakeAddon("a_heizungsbruecke", log, state=AddonState.NOT_RUNNING)
    cloudflared = FakeAddon("a_cloudflared_access_mqtt", log)
    entry = await _setup(hass, monkeypatch, {"heizungsbruecke": bridge, "cloudflared_access_mqtt": cloudflared})
    coordinator = entry.runtime_data

    await coordinator.async_check()
    clock["t"] += 300
    await coordinator.async_check()
    await hass.async_block_till_done()

    assert hass.states.get(STATUS).state == "addon_gestoppt"
    assert "Heizungsbrücke" in hass.states.get(STATUS).attributes["grund"]
    assert log == [("start", "a_heizungsbruecke")]
    assert [call.data["message"] for call in pushes] == [WATCHDOG_MESSAGES["addon_gestoppt"]]
    assert created == [("smartheat_wohnung1_addon", WATCHDOG_MESSAGES["addon_gestoppt"])]

    clock["t"] += 300
    await coordinator.async_check()  # weiter gestoppt: neuer Startversuch, keine zweite Meldung
    await hass.async_block_till_done()
    assert log == [("start", "a_heizungsbruecke"), ("start", "a_heizungsbruecke")]
    assert len(pushes) == 1

    bridge.state = AddonState.RUNNING
    _fire(hass, status_event(TENANT, "regelt"))
    await hass.async_block_till_done()

    assert hass.states.get(STATUS).state == "regelt"
    assert pushes[-1].data["message"] == WATCHDOG_ALL_CLEAR_MESSAGE
    assert dismissed == ["smartheat_wohnung1_addon"]


async def test_silent_bridge_is_restarted_and_reported(hass, monkeypatch, clock, notes):
    pushes = async_mock_service(hass, "notify", "mobile_app_pixel")
    log = []
    addons = {
        "heizungsbruecke": FakeAddon("a_heizungsbruecke", log),
        "cloudflared_access_mqtt": FakeAddon("a_cloudflared_access_mqtt", log),
    }
    entry = await _setup(hass, monkeypatch, addons)

    clock["t"] += 900
    await entry.runtime_data.async_check()
    await hass.async_block_till_done()

    assert hass.states.get(STATUS).state == "reagiert_nicht"
    assert log == [("restart", "a_heizungsbruecke")]
    assert [call.data["message"] for call in pushes] == [WATCHDOG_MESSAGES["reagiert_nicht"]]


async def test_unreachable_supervisor_skips_the_check(hass, monkeypatch, clock, notes):
    entry = await _setup(hass, monkeypatch, {})
    monkeypatch.setattr(f"{CO}.async_find_addon_managers", AsyncMock(side_effect=AddonError("weg")))

    for _ in range(4):
        clock["t"] += 300
        await entry.runtime_data.async_check()

    assert entry.runtime_data.watchdog_status is None
    assert notes[0] == []


async def test_zugang_abgelehnt_starts_reauth_once_per_change(hass, monkeypatch, clock):
    started = []
    monkeypatch.setattr(ConfigEntry, "async_start_reauth", lambda self, hass, *args, **kwargs: started.append(self.entry_id))
    entry = await _setup(hass, monkeypatch, {})

    for status in ("zugang_abgelehnt", "zugang_abgelehnt", "regelt", "zugang_abgelehnt"):
        _fire(hass, status_event(TENANT, status))
    await hass.async_block_till_done()

    assert started == [entry.entry_id, entry.entry_id]
