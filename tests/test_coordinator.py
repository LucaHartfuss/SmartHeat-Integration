"""Coordinator, Entities und zweiter Waechter (Spec TP7 1.2, 1.3)."""
import json
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.hassio import AddonError, AddonState
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import State
from homeassistant.helpers.dispatcher import async_dispatcher_connect
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


# --- Vorfall bleibt aktiv bis Event + alle Add-ons laufen (Review-Fund 1, Fix-Runde 1) ---

def test_incident_continues_when_a_responding_bridge_then_stops():
    """reagiert_nicht -> naechste Pruefung: Bruecke gestoppt -> keine Entwarnung, kein 2. Push
    (auf Watchdog-Ebene: Status bleibt reagiert_nicht, kein neuer Startversuch vor dem 2.
    bestaetigten Ausfall)."""
    watchdog = Watchdog(started_at=0)
    assert watchdog.check(900, RUNNING) == ("reagiert_nicht", ["heizungsbruecke"])

    stopped = {**RUNNING, "heizungsbruecke": False}
    assert watchdog.check(1200, stopped) == ("reagiert_nicht", [])


def test_incident_continues_when_a_stopped_addon_starts_without_an_event():
    """gestoppt -> gestartet -> naechste Pruefung laeuft, aber kein Event -> weiterhin gestoppt,
    keine Entwarnung."""
    watchdog = Watchdog(started_at=0)
    stopped = {**RUNNING, "heizungsbruecke": False}
    watchdog.check(300, stopped)
    assert watchdog.check(600, stopped) == ("addon_gestoppt", ["heizungsbruecke"])

    assert watchdog.check(900, RUNNING) == ("addon_gestoppt", [])


def test_incident_continues_when_an_event_arrives_but_an_addon_is_still_stopped():
    """Ein Event kommt, aber ein Add-on ist noch gestoppt -> keine Entwarnung."""
    watchdog = Watchdog(started_at=0)
    stopped = {**RUNNING, "heizungsbruecke": False}
    watchdog.check(300, stopped)
    watchdog.check(600, stopped)
    watchdog.event_received(700)

    assert watchdog.check(900, stopped) == ("addon_gestoppt", ["heizungsbruecke"])


def test_incident_clears_once_an_event_arrives_with_everything_running():
    """Event nach Vorfallsbeginn + alle Add-ons laufen -> genau eine Entwarnung."""
    watchdog = Watchdog(started_at=0)
    stopped = {**RUNNING, "heizungsbruecke": False}
    watchdog.check(300, stopped)
    watchdog.check(600, stopped)
    watchdog.event_received(700)

    assert watchdog.check(800, RUNNING) == (None, [])


def test_a_fresh_restart_does_not_immediately_trigger_reagiert_nicht():
    """Die Stille zaehlt ab dem eigenen (Neu-)Start (Praezisierung aus Fix-Runde 1): ein gerade
    gestartetes Add-on bekommt SILENCE_SECONDS Schonfrist, bevor es erneut als "reagiert nicht"
    gemeldet wird -- sonst waere Szenario B aus der Review sofort ein zweiter kritischer
    Vorfall."""
    watchdog = Watchdog(started_at=0)
    stopped = {**RUNNING, "heizungsbruecke": False}
    watchdog.check(300, stopped)
    watchdog.check(600, stopped)  # addon_gestoppt, Start bei t=600 vermerkt

    assert watchdog.check(900, RUNNING) == ("addon_gestoppt", [])  # 300s seit Start: keine Eskalation


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
        hebel={"curve": 1.1, "room_setpoint": 24.0, "min_flow": 20.5, "heat_limit": 16.0},
        gelernt={"curve": 1.08, "heat_limit": 16.4},
        letzte_serverantwort="2026-10-01T12:00:05+02:00",
        abo="inaktiv",
        abo_frist_ende="2026-10-31",
        hinweise={"raumfuehler_ausgefallen": ["sensor.a"], "batterie_niedrig": [],
                  "manueller_eingriff": {"hebel": {"curve": 1.4}, "erkannt": "2026-10-01T04:00:00+02:00"},
                  "waerme_fehlt": "2026-10-01T05:11:00+02:00"},
    ))
    await hass.async_block_till_done()

    status = hass.states.get(STATUS)
    assert status.state == "notbetrieb"
    assert status.attributes["raumfuehler_ausgefallen"] == ["sensor.a"]
    assert status.attributes["waerme_fehlt"] == "2026-10-01T05:11:00+02:00"
    # Schema 2: manueller_eingriff ist ein Objekt (hebel/erkannt); die Integration reicht es unveraendert durch.
    assert status.attributes["manueller_eingriff"] == {"hebel": {"curve": 1.4}, "erkannt": "2026-10-01T04:00:00+02:00"}
    assert hass.states.get("binary_sensor.smartheat_wohnung1_notbetrieb").state == "on"
    fault = hass.states.get("sensor.smartheat_wohnung1_datenfehler")
    assert (fault.state, fault.attributes["rollen"]) == ("lokal", ["dat"])
    assert hass.states.get("sensor.smartheat_wohnung1_boost").state == "notfall"
    assert hass.states.get("sensor.smartheat_wohnung1_heizkurve").state == "1.1"
    assert hass.states.get("sensor.smartheat_wohnung1_parallelverschiebung").state == "24.0"
    assert hass.states.get("sensor.smartheat_wohnung1_mindestvorlauf").state == "20.5"
    assert hass.states.get("sensor.smartheat_wohnung1_heizgrenze").state == "16.0"
    assert hass.states.get("sensor.smartheat_wohnung1_gelernte_steigung").state == "1.08"
    assert hass.states.get("sensor.smartheat_wohnung1_gelernte_heizgrenze").state == "16.4"
    assert hass.states.get("sensor.smartheat_wohnung1_abo").attributes["frist_ende"] == "2026-10-31"
    assert hass.states.get("sensor.smartheat_wohnung1_letzte_serverantwort").state == "2026-10-01T10:00:05+00:00"
    assert hass.states.get("sensor.smartheat_wohnung1_addon_version").state == "0.20.0"


@pytest.mark.parametrize("event", [
    status_event("andere", "regelt"),
    {**status_event(TENANT, "regelt"), "schema": 1},
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


async def test_check_is_skipped_when_one_addons_info_cannot_be_read(hass, monkeypatch, clock, notes):
    """M4: eine einzelne fehlschlagende async_get_addon_info() darf die gesamte Pruefung nur
    ueberspringen (kein Zaehler, kein Alarm), nicht abbrechen mit einer Ausnahme."""
    log = []
    addons = {
        "heizungsbruecke": FakeAddon("a_heizungsbruecke", log),
        "cloudflared_access_mqtt": FakeAddon("a_cloudflared_access_mqtt", log, error=AddonError("kaputt")),
    }
    entry = await _setup(hass, monkeypatch, addons)

    clock["t"] += 300
    await entry.runtime_data.async_check()

    assert entry.runtime_data.watchdog_status is None
    assert notes[0] == []


async def test_supervisor_outage_between_stopped_checks_does_not_reset_the_counter(hass, monkeypatch, clock):
    """M4: ein Ausfall des Supervisors zwischen zwei "gestoppt"-Pruefungen darf den Zaehler nicht
    zuruecksetzen -- die uebersprungene Pruefung zaehlt einfach nicht mit."""
    log = []
    bridge = FakeAddon("a_heizungsbruecke", log, state=AddonState.NOT_RUNNING)
    addons = {"heizungsbruecke": bridge, "cloudflared_access_mqtt": FakeAddon("a_cloudflared_access_mqtt", log)}
    entry = await _setup(hass, monkeypatch, addons)
    coordinator = entry.runtime_data

    await coordinator.async_check()  # 1. Pruefung: gestoppt gezaehlt (1/2)
    assert hass.states.get(STATUS).state == "unknown"

    monkeypatch.setattr(f"{CO}.async_find_addon_managers", AsyncMock(side_effect=AddonError("weg")))
    clock["t"] += 300
    await coordinator.async_check()  # Ausfall: uebersprungen, zaehlt nicht
    assert hass.states.get(STATUS).state == "unknown"

    monkeypatch.setattr(f"{CO}.async_find_addon_managers", AsyncMock(return_value=addons))
    clock["t"] += 300
    await coordinator.async_check()  # 2. echte Pruefung: jetzt gestoppt

    assert hass.states.get(STATUS).state == "addon_gestoppt"


async def test_escalation_within_an_incident_sends_no_second_push(hass, monkeypatch, clock, notes):
    """Review-Fund 1: eskaliert ein Vorfall (hier reagiert_nicht -> addon_gestoppt, weil der
    Neustart die Bruecke nicht wieder zum Laufen bringt), gibt es nur die eine kritische
    Push-Meldung vom Vorfallsbeginn -- nicht eine zweite fuer die Eskalation."""
    created, _dismissed = notes
    pushes = async_mock_service(hass, "notify", "mobile_app_pixel")
    log = []
    bridge = FakeAddon("a_heizungsbruecke", log)
    addons = {"heizungsbruecke": bridge, "cloudflared_access_mqtt": FakeAddon("a_cloudflared_access_mqtt", log)}
    entry = await _setup(hass, monkeypatch, addons)
    coordinator = entry.runtime_data

    clock["t"] += 900
    await coordinator.async_check()  # reagiert_nicht: Neustart versucht
    assert hass.states.get(STATUS).state == "reagiert_nicht"
    assert len(pushes) == 1

    bridge.state = AddonState.NOT_RUNNING  # der Neustart hat nicht geholfen
    clock["t"] += 300
    await coordinator.async_check()  # 1/2 gestoppt
    clock["t"] += 300
    await coordinator.async_check()  # 2/2 -> Eskalation auf addon_gestoppt

    assert hass.states.get(STATUS).state == "addon_gestoppt"
    assert len(pushes) == 1  # weiterhin nur die eine kritische Push-Meldung
    # Die persistent_notification wird bei der Eskalation neu geschrieben (aktualisierter Text),
    # aber mit derselben notification_id -- in HA selbst ueberschreibt das den bestehenden Eintrag
    # statt einen zweiten anzulegen.
    assert [message for _notification_id, message in created] == [
        WATCHDOG_MESSAGES["reagiert_nicht"], WATCHDOG_MESSAGES["addon_gestoppt"],
    ]
    assert {notification_id for notification_id, _message in created} == {"smartheat_wohnung1_addon"}


async def test_event_with_invalid_field_values_does_not_crash_entities(hass, monkeypatch, clock):
    """M2: ein unbekannter Enum-Wert, ein fehlender Schluessel oder ein naiver Zeitstempel im
    Event wuerden HA sonst beim Schreiben des States mit einer ValueError abbrechen lassen
    (SensorEntity.state validiert `options`/ENUM und TIMESTAMP-Zeitzone). Muss auf None
    abgebildet werden statt die Entity haengen zu lassen."""
    await _setup(hass, monkeypatch, {})

    event = status_event(
        TENANT, "regelt", boost="unbekannt", datenfehler={"art": "unbekannt", "rollen": []},
        letzte_serverantwort="2026-10-01T12:00:05",  # kein Zeitzonen-Offset: naiv
    )
    assert event["hebel"] is None and event["gelernt"] is None  # fehlende Werte (kein Hebel-Objekt im Event)

    _fire(hass, event)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.smartheat_wohnung1_boost").state == "unknown"
    assert hass.states.get("sensor.smartheat_wohnung1_datenfehler").state == "unknown"
    assert hass.states.get("sensor.smartheat_wohnung1_letzte_serverantwort").state == "unknown"
    assert hass.states.get("sensor.smartheat_wohnung1_heizkurve").state == "unknown"
    assert hass.states.get("sensor.smartheat_wohnung1_gelernte_steigung").state == "unknown"
    # Der Rest der Entities bleibt unberuehrt.
    assert hass.states.get(STATUS).state == "regelt"


async def test_event_without_notbetrieb_and_with_broken_hints_does_not_crash_entities(hass, monkeypatch, clock):
    """Final-Review M4: ein fehlendes `notbetrieb` (KeyError im Binaersensor) und ein `hinweise`,
    das kein Objekt ist (AttributeError im Status-Sensor), duerfen die Entities nicht haengen
    lassen -- wie EventSensor._safe_value: unbekannt bzw. leer."""
    await _setup(hass, monkeypatch, {})
    _fire(hass, status_event(TENANT, "regelt", notbetrieb=True))
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.smartheat_wohnung1_notbetrieb").state == "on"

    event = status_event(TENANT, "regelt", hinweise=["kaputt"])
    del event["notbetrieb"]
    _fire(hass, event)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.smartheat_wohnung1_notbetrieb").state == "unknown"
    status = hass.states.get(STATUS)
    assert status.state == "regelt"
    assert status.attributes["manueller_eingriff"] is None


async def test_a_non_boolean_notbetrieb_is_unknown(hass, monkeypatch, clock):
    await _setup(hass, monkeypatch, {})

    _fire(hass, status_event(TENANT, "regelt", notbetrieb="false"))
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.smartheat_wohnung1_notbetrieb").state == "unknown"


async def test_restore_ignores_an_out_of_options_value(hass, monkeypatch, clock):
    """M4: ein gespeicherter Wert, der nicht (mehr) in den Enum-Optionen ist (z. B. ein Wert aus
    einer aelteren Version), darf nicht uebernommen werden -- sonst dieselbe ValueError wie bei
    einem ungueltigen Live-Event."""
    mock_restore_cache(hass, [State("sensor.smartheat_wohnung1_boost", "veraltet")])

    await _setup(hass, monkeypatch, {})

    assert hass.states.get("sensor.smartheat_wohnung1_boost").state == "unknown"


@pytest.mark.parametrize("hebel, gelernt", [
    (None, None),
    ("kaputt", ["kaputt"]),
    ({"curve": "ungueltig", "room_setpoint": float("nan"), "min_flow": float("inf"), "heat_limit": float("nan")},
     {"curve": "ungueltig", "heat_limit": float("-inf")}),
    ({}, {}),
])
async def test_event_with_broken_lever_values_does_not_crash_entities(hass, monkeypatch, clock, hebel, gelernt):
    """Fix-Runde 2, Fund 2 (Plan 3c: je Hebel und Lernwert): fehlende, nicht-numerische oder nicht endliche Werte
    in hebel/gelernt (Device-Class TEMPERATURE, HA validiert das) duerfen nicht zu einer haengenden bzw. kaputten
    Entity fuehren, sondern zeigen unknown."""
    await _setup(hass, monkeypatch, {})

    _fire(hass, status_event(TENANT, "regelt", hebel=hebel, gelernt=gelernt))
    await hass.async_block_till_done()

    for key in ("heizkurve", "parallelverschiebung", "mindestvorlauf", "heizgrenze",
                "gelernte_steigung", "gelernte_heizgrenze"):
        assert hass.states.get(f"sensor.smartheat_wohnung1_{key}").state == "unknown", key
    assert hass.states.get(STATUS).state == "regelt"


async def test_parallel_shift_and_min_flow_sensors(hass, monkeypatch, clock):
    """Task 20: offset entfaellt, dessen Rolle uebernehmen die beiden Temperatur-Sensoren
    parallelverschiebung und mindestvorlauf (jetzt aus hebel); die alte offset-Entity wird nicht erzeugt."""
    await _setup(hass, monkeypatch, {})

    _fire(hass, status_event(TENANT, "regelt", hebel={"room_setpoint": 21.0, "min_flow": 20.5}))
    await hass.async_block_till_done()

    shift = hass.states.get("sensor.smartheat_wohnung1_parallelverschiebung")
    min_flow = hass.states.get("sensor.smartheat_wohnung1_mindestvorlauf")
    assert (shift.state, min_flow.state) == ("21.0", "20.5")
    assert shift.attributes["unit_of_measurement"] == min_flow.attributes["unit_of_measurement"] == "°C"
    assert hass.states.get("sensor.smartheat_wohnung1_offset") is None
    # Ein Hebel, der im Event fehlt, ist unknown, die uebrigen Sensoren bleiben davon unberuehrt.
    assert hass.states.get("sensor.smartheat_wohnung1_heizkurve").state == "unknown"


async def test_viessmann_entry_shows_level_and_room_setpoint(hass, monkeypatch, clock):
    monkeypatch.setattr(f"{CO}.async_find_addon_managers", AsyncMock(return_value={}))
    entry = make_entry(hass, data={
        "tenant_id": TENANT, "profile_id": "viessmann_vicare", "integration_domain": "vicare",
        "circuit": {"config_entry_id": "vicare-entry", "system_key": "SYSTEM", "circuit": "0"},
        "entities": {
            "entity_curve_current": "number.slope", "entity_level_current": "number.level",
            "entity_shift_current": "number.comfort", "entity_mode_select": "climate.vicare",
            "entity_outdoor_temp": "sensor.aussen",
        },
        "lever_set": "viessmann_vicare", "shift_lever": "level",
    })
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    _fire(hass, status_event(
        TENANT, "regelt", hebelsatz="viessmann_vicare", hebel={"curve": 1.2, "level": 3.0, "room_setpoint": 21.0},
        gelernt={"curve": 1.2},
    ))
    await hass.async_block_till_done()

    assert hass.states.get("sensor.smartheat_wohnung1_heizkurve").state == "1.2"
    niveau = hass.states.get("sensor.smartheat_wohnung1_niveau")
    assert (niveau.state, niveau.attributes["unit_of_measurement"]) == ("3.0", "K")
    assert hass.states.get("sensor.smartheat_wohnung1_raum_soll").state == "21.0"
    assert hass.states.get("sensor.smartheat_wohnung1_parallelverschiebung") is None
    assert hass.states.get("sensor.smartheat_wohnung1_heizgrenze") is None


async def test_gestoppt_reason_keeps_the_addon_name_once_it_runs_again(hass, monkeypatch, clock, notes):
    """Fix-Runde 2, Fund 1: waehrend ein addon_gestoppt-Vorfall offen bleibt (Add-on laeuft
    wieder, aber noch kein Event), darf der Grund den Add-on-Namen nicht verlieren (der Zaehler
    fuer "gestoppt" ist dann schon zurueckgesetzt), und der unveraenderte Status darf keine neue
    Aktualisierung ausloesen."""
    log = []
    bridge = FakeAddon("a_heizungsbruecke", log, state=AddonState.NOT_RUNNING)
    addons = {"heizungsbruecke": bridge, "cloudflared_access_mqtt": FakeAddon("a_cloudflared_access_mqtt", log)}
    entry = await _setup(hass, monkeypatch, addons)
    coordinator = entry.runtime_data

    await coordinator.async_check()  # 1/2 gestoppt gezaehlt
    clock["t"] += 300
    await coordinator.async_check()  # 2/2 -> addon_gestoppt
    assert "Heizungsbrücke" in hass.states.get(STATUS).attributes["grund"]

    signals = []
    async_dispatcher_connect(hass, coordinator.signal, lambda: signals.append(None))

    bridge.state = AddonState.RUNNING  # laeuft wieder, aber noch kein Event
    clock["t"] += 300
    await coordinator.async_check()

    assert hass.states.get(STATUS).state == "addon_gestoppt"
    assert "Heizungsbrücke" in hass.states.get(STATUS).attributes["grund"]
    assert signals == []  # unveraenderter Status/Grund: kein Update


# --- Waechter und Transportart (Plan AWS-2, Praezisierung 1) ---

IOT_CORE_TRANSPORT = json.dumps({
    "alpn": "x-amzn-mqtt-ca", "ca_pem": "CA", "client_id": TENANT, "host": "iot.example.test", "kind": "iot_core", "port": 443,
}, sort_keys=True)
MOSQUITTO_TRANSPORT = json.dumps({"host": "127.0.0.1", "kind": "mosquitto_cloudflared", "port": 18830}, sort_keys=True)


async def _stopped_cloudflared_setup(hass, monkeypatch, transport):
    log = []
    bridge = FakeAddon("a_heizungsbruecke", log, options={"tenant_id": TENANT, "transport": transport})
    cloudflared = FakeAddon("a_cloudflared_access_mqtt", log, state=AddonState.NOT_RUNNING)
    entry = await _setup(hass, monkeypatch, {"heizungsbruecke": bridge, "cloudflared_access_mqtt": cloudflared})
    return entry.runtime_data, log


async def test_watchdog_ignores_a_stopped_cloudflared_on_iot_core(hass, monkeypatch, clock, notes):
    coordinator, log = await _stopped_cloudflared_setup(hass, monkeypatch, IOT_CORE_TRANSPORT)

    for _ in range(3):
        clock["t"] += 100
        await coordinator.async_check()

    assert coordinator.watchdog_status is None
    assert log == []  # cloudflared weder gestartet noch neu gestartet
    assert notes[0] == []


async def test_watchdog_still_revives_cloudflared_on_mosquitto(hass, monkeypatch, clock, notes):
    coordinator, log = await _stopped_cloudflared_setup(hass, monkeypatch, MOSQUITTO_TRANSPORT)

    for _ in range(2):
        clock["t"] += 100
        await coordinator.async_check()

    assert coordinator.watchdog_status == "addon_gestoppt"
    assert log == [("start", "a_cloudflared_access_mqtt")]


async def test_watchdog_still_revives_a_stopped_bridge_on_iot_core(hass, monkeypatch, clock, notes):
    log = []
    bridge = FakeAddon("a_heizungsbruecke", log, state=AddonState.NOT_RUNNING,
                       options={"tenant_id": TENANT, "transport": IOT_CORE_TRANSPORT})
    cloudflared = FakeAddon("a_cloudflared_access_mqtt", log, state=AddonState.NOT_RUNNING)
    entry = await _setup(hass, monkeypatch, {"heizungsbruecke": bridge, "cloudflared_access_mqtt": cloudflared})

    for _ in range(2):
        clock["t"] += 100
        await entry.runtime_data.async_check()

    assert entry.runtime_data.watchdog_status == "addon_gestoppt"
    assert log == [("start", "a_heizungsbruecke")]


async def test_watchdog_without_a_readable_transport_watches_both_addons(hass, monkeypatch, clock, notes):
    coordinator, log = await _stopped_cloudflared_setup(hass, monkeypatch, "")

    for _ in range(2):
        clock["t"] += 100
        await coordinator.async_check()

    assert coordinator.watchdog_status == "addon_gestoppt"
    assert log == [("start", "a_cloudflared_access_mqtt")]
