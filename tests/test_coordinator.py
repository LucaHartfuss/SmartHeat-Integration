"""Coordinator, Entities und zweiter Waechter (Schema 3, Zuordnung ueber die
Geraete-ID, Waechter nur fuer die Heizungsbruecke, Meldung nur per persistent_notification)."""
import asyncio
from datetime import timedelta

import pytest
from aiohasupervisor.exceptions import SupervisorError
from aiohasupervisor.models import AddonState
from homeassistant.core import State
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
    mock_restore_cache,
)

from custom_components.smartheat.const import STATUS_EVENT, WATCHDOG_MESSAGES
from custom_components.smartheat.coordinator import Watchdog

from .addon_fakes import make_device_entry, status_event
from .supervisor_fakes import BRIDGE_SLUG, installed_addon, repository

CO = "custom_components.smartheat.coordinator"
TENANT = "wohnung1"
STATUS = "sensor.smartheat_wohnung1_status"
RUNNING = True  # laeuft laut Supervisor; False: laeuft nicht; None: Bruecke nicht aufloesbar


# --- reine Waechter-Logik ---

def test_watchdog_needs_two_checks_before_an_addon_counts_as_stopped():
    watchdog = Watchdog(started_at=0)
    watchdog.event_received(0)
    stopped = False

    assert watchdog.check(300, stopped) == (None, False)
    assert watchdog.check(600, stopped) == ("addon_gestoppt", True)


def test_watchdog_running_again_resets_the_count():
    watchdog = Watchdog(started_at=0)
    watchdog.event_received(0)
    stopped = False

    watchdog.check(300, stopped)
    watchdog.check(400, RUNNING)

    assert watchdog.check(500, stopped) == (None, False)


def test_watchdog_restarts_at_most_three_times_per_hour():
    watchdog = Watchdog(started_at=0)
    stopped = False

    restarts = [watchdog.check(300 * n, stopped)[1] for n in range(1, 8)]

    assert restarts == [False, True, True, True, False, False, False]
    assert watchdog.check(600 + 3600, stopped) == ("addon_gestoppt", True)


def test_watchdog_no_silence_alarm_within_900_s_after_setup():
    watchdog = Watchdog(started_at=1000)

    assert watchdog.check(1300, RUNNING) == (None, False)
    assert watchdog.check(1600, RUNNING) == (None, False)
    assert watchdog.check(1900, RUNNING) == ("reagiert_nicht", True)


def test_watchdog_silence_counts_from_the_last_event():
    watchdog = Watchdog(started_at=0)
    watchdog.event_received(500)

    assert watchdog.check(1200, RUNNING) == (None, False)
    assert watchdog.check(1400, RUNNING) == ("reagiert_nicht", True)


def test_watchdog_silence_does_not_count_while_the_bridge_is_stopped():
    watchdog = Watchdog(started_at=0)

    assert watchdog.check(1000, False) == (None, False)


def test_watchdog_an_unresolvable_bridge_is_no_stopped_count_and_never_restarted():
    """`None` (Bruecke nicht aufloesbar) zaehlt nie als „laeuft nicht“ und startet nichts; erst die Stille
    meldet „reagiert nicht“, ohne Neustart."""
    watchdog = Watchdog(started_at=0)

    ergebnisse = [watchdog.check(300 * n, None) for n in (1, 2, 3)]
    assert ergebnisse == [(None, False), (None, False), ("reagiert_nicht", False)]
    assert watchdog.not_running == 0 and not watchdog.restarts


def test_watchdog_an_unresolvable_bridge_with_events_stays_silent_and_clears_an_incident():
    watchdog = Watchdog(started_at=0)
    for n in range(1, 4):
        watchdog.event_received(300 * n)
        assert watchdog.check(300 * n + 1, None) == (None, False)
    watchdog.check(300 * 3 + 1000, None)  # Stille: Vorfall
    assert watchdog.incident_status == "reagiert_nicht"
    watchdog.event_received(5000)

    assert watchdog.check(5001, None) == (None, False)


# --- Vorfall bleibt aktiv bis Event + alle Add-ons laufen (Review-Fund 1, Fix-Runde 1) ---

def test_incident_continues_when_a_responding_bridge_then_stops():
    """reagiert_nicht -> naechste Pruefung: Bruecke gestoppt -> keine Entwarnung, kein 2. Push
    (auf Watchdog-Ebene: Status bleibt reagiert_nicht, kein neuer Startversuch vor dem 2.
    bestaetigten Ausfall)."""
    watchdog = Watchdog(started_at=0)
    assert watchdog.check(900, RUNNING) == ("reagiert_nicht", True)

    stopped = False
    assert watchdog.check(1200, stopped) == ("reagiert_nicht", False)


def test_incident_continues_when_a_stopped_addon_starts_without_an_event():
    """gestoppt -> gestartet -> naechste Pruefung laeuft, aber kein Event -> weiterhin gestoppt,
    keine Entwarnung."""
    watchdog = Watchdog(started_at=0)
    stopped = False
    watchdog.check(300, stopped)
    assert watchdog.check(600, stopped) == ("addon_gestoppt", True)

    assert watchdog.check(900, RUNNING) == ("addon_gestoppt", False)


def test_incident_continues_when_an_event_arrives_but_an_addon_is_still_stopped():
    """Ein Event kommt, aber ein Add-on ist noch gestoppt -> keine Entwarnung."""
    watchdog = Watchdog(started_at=0)
    stopped = False
    watchdog.check(300, stopped)
    watchdog.check(600, stopped)
    watchdog.event_received(700)

    assert watchdog.check(900, stopped) == ("addon_gestoppt", True)


def test_incident_clears_once_an_event_arrives_with_everything_running():
    """Event nach Vorfallsbeginn + alle Add-ons laufen -> genau eine Entwarnung."""
    watchdog = Watchdog(started_at=0)
    stopped = False
    watchdog.check(300, stopped)
    watchdog.check(600, stopped)
    watchdog.event_received(700)

    assert watchdog.check(800, RUNNING) == (None, False)


def test_a_fresh_restart_does_not_immediately_trigger_reagiert_nicht():
    """Die Stille zaehlt ab dem eigenen (Neu-)Start (Praezisierung aus Fix-Runde 1): ein gerade
    gestartetes Add-on bekommt SILENCE_SECONDS Schonfrist, bevor es erneut als "reagiert nicht"
    gemeldet wird -- sonst waere Szenario B aus der Review sofort ein zweiter kritischer
    Vorfall."""
    watchdog = Watchdog(started_at=0)
    stopped = False
    watchdog.check(300, stopped)
    watchdog.check(600, stopped)  # addon_gestoppt, Start bei t=600 vermerkt

    assert watchdog.check(900, RUNNING) == ("addon_gestoppt", False)  # 300s seit Start: keine Eskalation


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


async def _setup(hass, **entry_kwargs):
    """Eintrag einrichten. Die Waechter-Tests nehmen dazu die Fixture `supervisor` (Supervisor-Client an der Grenze,
    die Heizungsbruecke `abc_heizungsbruecke` laeuft); der Waechter findet sie ueber die echte Aufloesung."""
    entry = make_device_entry(hass, **entry_kwargs)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _fire(hass, event):
    hass.bus.async_fire(STATUS_EVENT, event)


def _bridge(supervisor):
    """Die installierte Heizungsbruecke im FakeSupervisor (Zustand `state` wie aiohasupervisor)."""
    return next(addon for addon in supervisor.installed if addon.slug == BRIDGE_SLUG)


async def test_an_event_updates_every_entity(hass, clock):
    await _setup(hass)

    _fire(hass, status_event(
        TENANT, "notbetrieb", notbetrieb=True, boost="notfall", datenfehler={"art": "lokal", "rollen": ["dat"]},
        hebel={"curve": 1.1, "room_setpoint": 24.0, "min_flow": 20.5, "heat_limit": 16.0},
        gelernt={"curve": 1.08, "heat_limit": 16.4},
        letzte_serverantwort="2026-10-01T12:00:05+02:00",
        abo="inaktiv",
        abo_frist_ende="2026-10-31",
        hinweise={"raumfuehler_ausgefallen": ["sensor.a"], "batterie_niedrig": [],
                  "manueller_eingriff": {"hebel": {"curve": 1.4}, "erkannt": "2026-10-01T04:00:00+02:00"}},
    ))
    await hass.async_block_till_done()

    status = hass.states.get(STATUS)
    assert status.state == "notbetrieb"
    assert status.attributes["raumfuehler_ausgefallen"] == ["sensor.a"]
    assert "waerme_fehlt" not in status.attributes
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


async def test_the_coordinator_keeps_only_the_fields_of_the_status_event(hass, clock):
    """Der Coordinator filtert das Ereignis auf STATUS_EVENT_FIELDS; ein umbenanntes Feld dort faellt hier
    auf."""
    entry = await _setup(hass)
    event = status_event(TENANT, "regelt", raum_soll=21.0)
    _fire(hass, {**event, "fremd": 1})
    await hass.async_block_till_done()
    assert entry.runtime_data.data == event


@pytest.mark.parametrize("event", [
    status_event(TENANT, "regelt", geraet_id="shg-b"),
    {key: value for key, value in status_event(TENANT, "regelt").items() if key != "geraet_id"},
    {**status_event(TENANT, "regelt"), "schema": 2},
    {**status_event(TENANT, "regelt"), "schema": 1},
    status_event(TENANT, "bereit"),
])
async def test_foreign_or_unknown_events_are_ignored(hass, clock, event):
    entry = await _setup(hass)

    _fire(hass, event)
    await hass.async_block_till_done()

    assert hass.states.get(STATUS).state == "unknown"
    assert entry.runtime_data.data is None


async def test_event_of_another_device_is_ignored(hass, clock):
    """Zuordnung ueber die Geraete-ID, nicht den Tenant; nur Schema 3."""
    entry = await _setup(hass)
    event = status_event(TENANT, "regelt", raum_soll=21.0)

    _fire(hass, {**event, "geraet_id": "shg-b", "status": "notbetrieb"})
    await hass.async_block_till_done()
    assert entry.runtime_data.data is None
    _fire(hass, {**event, "schema": 2})
    await hass.async_block_till_done()
    assert entry.runtime_data.data is None
    _fire(hass, event)
    await hass.async_block_till_done()
    assert entry.runtime_data.data["status"] == "regelt"


async def test_event_of_an_unconfigured_device_is_taken_by_its_id(hass, clock):
    """Vor der Einrichtung kennt das Add-on keinen Tenant (Laufzeit-Teil leer, status startet): die Geraete-ID
    reicht; den Geraetezustand traegt geraet_zustand."""
    entry = await _setup(hass)

    _fire(hass, status_event(None, "startet", geraet_zustand="wartet_auf_einrichtung"))
    await hass.async_block_till_done()

    assert hass.states.get(STATUS).state == "startet"
    assert entry.runtime_data.data["geraet_zustand"] == "wartet_auf_einrichtung"


async def test_entities_restore_the_last_state_after_a_restart(hass, clock):
    """Review Focus 3."""
    mock_restore_cache(hass, [
        State(STATUS, "notbetrieb", {"grund": None, "raumfuehler_ausgefallen": ["sensor.a"]}),
        State("binary_sensor.smartheat_wohnung1_notbetrieb", "on"),
        State("sensor.smartheat_wohnung1_heizkurve", "1.1"),
        State("sensor.smartheat_wohnung1_letzte_serverantwort", "2026-10-01T10:00:05+00:00"),
        State("sensor.smartheat_wohnung1_datenfehler", "lokal", {"rollen": ["dat"]}),
    ])

    await _setup(hass)

    assert hass.states.get(STATUS).state == "notbetrieb"
    assert hass.states.get(STATUS).attributes["raumfuehler_ausgefallen"] == ["sensor.a"]
    assert hass.states.get("binary_sensor.smartheat_wohnung1_notbetrieb").state == "on"
    assert hass.states.get("sensor.smartheat_wohnung1_heizkurve").state == "1.1"
    assert hass.states.get("sensor.smartheat_wohnung1_letzte_serverantwort").state == "2026-10-01T10:00:05+00:00"
    assert hass.states.get("sensor.smartheat_wohnung1_datenfehler").attributes["rollen"] == ["dat"]


async def test_stopped_addon_is_reported_once_restarted_and_cleared_by_an_event(hass, supervisor, clock, notes):
    """Meldung nur per persistent_notification, kein Push
    an die Handys (das ist Sache des Add-ons)."""
    created, dismissed = notes
    pushes = async_mock_service(hass, "notify", "mobile_app_pixel")
    _bridge(supervisor).state = AddonState.STOPPED
    entry = await _setup(hass)
    # Eine Push-Liste aus einem frueheren Optionen-Flow bleibt ohne Wirkung.
    hass.config_entries.async_update_entry(entry, options={"notify_services": ["notify.mobile_app_pixel"]})
    coordinator = entry.runtime_data

    await coordinator.async_check()
    clock["t"] += 300
    await coordinator.async_check()
    await hass.async_block_till_done()

    assert hass.states.get(STATUS).state == "addon_gestoppt"
    assert "Heizungsbrücke" in hass.states.get(STATUS).attributes["grund"]
    assert supervisor.actions == [("start", BRIDGE_SLUG)]
    assert created == [("smartheat_wohnung1_addon", WATCHDOG_MESSAGES["addon_gestoppt"])]

    _bridge(supervisor).state = AddonState.STOPPED  # der Start hilft nicht: das Add-on bleibt gestoppt
    clock["t"] += 300
    await coordinator.async_check()  # weiter gestoppt: neuer Startversuch, keine zweite Meldung
    await hass.async_block_till_done()
    assert supervisor.actions == [("start", BRIDGE_SLUG), ("start", BRIDGE_SLUG)]
    assert len(created) == 1

    assert _bridge(supervisor).state == AddonState.STARTED  # der zweite Start hat gewirkt
    _fire(hass, status_event(TENANT, "regelt"))
    await hass.async_block_till_done()

    assert hass.states.get(STATUS).state == "regelt"
    assert dismissed == ["smartheat_wohnung1_addon"]
    assert pushes == []


async def test_watchdog_looks_only_for_the_bridge(hass, supervisor, clock, notes):
    """cloudflared_access_mqtt gehoert nicht zu dieser Integration: ein gestopptes
    cloudflared aus demselben Repository wird weder gemeldet noch gestartet."""
    supervisor.installed.append(installed_addon("abc_cloudflared_access_mqtt", state=AddonState.STOPPED))
    entry = await _setup(hass)

    for _ in range(3):
        await entry.runtime_data.async_check()
        clock["t"] += 300

    assert entry.runtime_data.watchdog_status is None
    assert supervisor.actions == [] and notes[0] == []


async def test_silent_bridge_is_restarted_and_reported(hass, supervisor, clock, notes):
    pushes = async_mock_service(hass, "notify", "mobile_app_pixel")
    entry = await _setup(hass)

    clock["t"] += 900
    await entry.runtime_data.async_check()
    await hass.async_block_till_done()

    assert hass.states.get(STATUS).state == "reagiert_nicht"
    assert supervisor.actions == [("restart", BRIDGE_SLUG)]
    assert notes[0] == [("smartheat_wohnung1_addon", WATCHDOG_MESSAGES["reagiert_nicht"])]
    assert pushes == []


async def test_unreachable_supervisor_skips_the_check(hass, supervisor, clock, notes):
    """Die Bruecke ist gestoppt (zwei gezaehlte Pruefungen ergaeben einen Alarm), aber der Supervisor antwortet
    nicht: jede Pruefung faellt aus, kein Alarm, kein Startversuch."""
    _bridge(supervisor).state = AddonState.STOPPED
    supervisor.errors["addons.list"] = SupervisorError("weg")
    entry = await _setup(hass)

    for _ in range(4):
        clock["t"] += 300
        await entry.runtime_data.async_check()

    assert entry.runtime_data.watchdog_status is None
    assert notes[0] == [] and supervisor.actions == []


async def test_zugang_abgelehnt_starts_no_reauth(hass, clock):
    """Kein Reauth (das Geraet meldet sich mit Zertifikat an); der Status zeigt es nur an."""
    entry = await _setup(hass)

    _fire(hass, status_event(TENANT, "zugang_abgelehnt"))
    await hass.async_block_till_done()

    assert hass.states.get(STATUS).state == "zugang_abgelehnt"
    assert not list(entry.async_get_active_flows(hass, {"reauth"}))


async def test_check_is_skipped_when_the_addon_info_cannot_be_read(hass, supervisor, clock, notes):
    """M4: eine fehlschlagende async_get_addon_info() darf die gesamte Pruefung nur
    ueberspringen (kein Zaehler, kein Alarm), nicht abbrechen mit einer Ausnahme. Die Bruecke ist gestoppt: zwei
    gezaehlte Pruefungen ergaeben einen Alarm."""
    _bridge(supervisor).state = AddonState.STOPPED
    supervisor.errors[("addons.addon_info", BRIDGE_SLUG)] = SupervisorError("kaputt")
    entry = await _setup(hass)

    for _ in range(2):
        clock["t"] += 300
        await entry.runtime_data.async_check()

    assert entry.runtime_data.watchdog_status is None
    assert notes[0] == [] and supervisor.actions == []


async def test_supervisor_outage_between_stopped_checks_does_not_reset_the_counter(hass, supervisor, clock):
    """M4: ein Ausfall des Supervisors zwischen zwei "gestoppt"-Pruefungen darf den Zaehler nicht
    zuruecksetzen -- die uebersprungene Pruefung zaehlt einfach nicht mit."""
    _bridge(supervisor).state = AddonState.STOPPED
    entry = await _setup(hass)
    coordinator = entry.runtime_data

    await coordinator.async_check()  # 1. Pruefung: gestoppt gezaehlt (1/2)
    assert hass.states.get(STATUS).state == "unknown"

    supervisor.errors["addons.list"] = SupervisorError("weg")
    clock["t"] += 300
    await coordinator.async_check()  # Ausfall: uebersprungen, zaehlt nicht
    assert hass.states.get(STATUS).state == "unknown"

    del supervisor.errors["addons.list"]
    clock["t"] += 300
    await coordinator.async_check()  # 2. echte Pruefung: jetzt gestoppt

    assert hass.states.get(STATUS).state == "addon_gestoppt"


async def test_escalation_within_an_incident_keeps_one_notification(hass, supervisor, clock, notes):
    """Review-Fund 1: eskaliert ein Vorfall (hier reagiert_nicht -> addon_gestoppt, weil der
    Neustart die Bruecke nicht wieder zum Laufen bringt), bleibt es ein Vorfall: derselbe Eintrag
    mit neuem Text, keine Entwarnung dazwischen."""
    created, dismissed = notes
    entry = await _setup(hass)
    coordinator = entry.runtime_data

    clock["t"] += 900
    await coordinator.async_check()  # reagiert_nicht: Neustart versucht
    assert hass.states.get(STATUS).state == "reagiert_nicht"
    assert supervisor.actions == [("restart", BRIDGE_SLUG)]

    _bridge(supervisor).state = AddonState.STOPPED  # der Neustart hat nicht geholfen
    clock["t"] += 300
    await coordinator.async_check()  # 1/2 gestoppt
    clock["t"] += 300
    await coordinator.async_check()  # 2/2 -> Eskalation auf addon_gestoppt

    assert hass.states.get(STATUS).state == "addon_gestoppt"
    assert dismissed == []
    # Die persistent_notification wird bei der Eskalation neu geschrieben (aktualisierter Text),
    # aber mit derselben notification_id -- in HA selbst ueberschreibt das den bestehenden Eintrag
    # statt einen zweiten anzulegen.
    assert [message for _notification_id, message in created] == [
        WATCHDOG_MESSAGES["reagiert_nicht"], WATCHDOG_MESSAGES["addon_gestoppt"],
    ]
    assert {notification_id for notification_id, _message in created} == {"smartheat_wohnung1_addon"}


async def test_event_with_invalid_field_values_does_not_crash_entities(hass, clock):
    """M2: ein unbekannter Enum-Wert, ein fehlender Schluessel oder ein naiver Zeitstempel im
    Event wuerden HA sonst beim Schreiben des States mit einer ValueError abbrechen lassen
    (SensorEntity.state validiert `options`/ENUM und TIMESTAMP-Zeitzone). Muss auf None
    abgebildet werden statt die Entity haengen zu lassen."""
    await _setup(hass)

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


async def test_event_without_notbetrieb_and_with_broken_hints_does_not_crash_entities(hass, clock):
    """Ein fehlendes `notbetrieb` (KeyError im Binaersensor) und ein `hinweise`,
    das kein Objekt ist (AttributeError im Status-Sensor), duerfen die Entities nicht haengen
    lassen -- wie EventSensor._safe_value: unbekannt bzw. leer."""
    await _setup(hass)
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


async def test_a_non_boolean_notbetrieb_is_unknown(hass, clock):
    await _setup(hass)

    _fire(hass, status_event(TENANT, "regelt", notbetrieb="false"))
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.smartheat_wohnung1_notbetrieb").state == "unknown"


async def test_restore_ignores_an_out_of_options_value(hass, clock):
    """M4: ein gespeicherter Wert, der nicht (mehr) in den Enum-Optionen ist (z. B. ein Wert aus
    einer aelteren Version), darf nicht uebernommen werden -- sonst dieselbe ValueError wie bei
    einem ungueltigen Live-Event."""
    mock_restore_cache(hass, [State("sensor.smartheat_wohnung1_boost", "veraltet")])

    await _setup(hass)

    assert hass.states.get("sensor.smartheat_wohnung1_boost").state == "unknown"


@pytest.mark.parametrize("hebel, gelernt", [
    (None, None),
    ("kaputt", ["kaputt"]),
    ({"curve": "ungueltig", "room_setpoint": float("nan"), "min_flow": float("inf"), "heat_limit": float("nan")},
     {"curve": "ungueltig", "heat_limit": float("-inf")}),
    ({}, {}),
])
async def test_event_with_broken_lever_values_does_not_crash_entities(hass, clock, hebel, gelernt):
    """Je Hebel und Lernwert: fehlende, nicht-numerische oder nicht endliche Werte
    in hebel/gelernt (Device-Class TEMPERATURE, HA validiert das) duerfen nicht zu einer haengenden bzw. kaputten
    Entity fuehren, sondern zeigen unknown."""
    await _setup(hass)

    _fire(hass, status_event(TENANT, "regelt", hebel=hebel, gelernt=gelernt))
    await hass.async_block_till_done()

    for key in ("heizkurve", "parallelverschiebung", "mindestvorlauf", "heizgrenze",
                "gelernte_steigung", "gelernte_heizgrenze"):
        assert hass.states.get(f"sensor.smartheat_wohnung1_{key}").state == "unknown", key
    assert hass.states.get(STATUS).state == "regelt"


@pytest.mark.parametrize("stored", ["inf", "-inf", "nan"])
async def test_restore_ignores_a_non_finite_stored_value(hass, clock, stored):
    """Ein gespeicherter Zustand "inf"/"nan" (z. B. nach einer Einheiten-Umrechnung) darf nicht uebernommen werden:
    wie im Live-Pfad ist der Sensor dann unknown."""
    mock_restore_cache(hass, [State("sensor.smartheat_wohnung1_heizkurve", stored),
                              State("sensor.smartheat_wohnung1_heizgrenze", stored),
                              State("sensor.smartheat_wohnung1_mindestvorlauf", "20.5")])

    await _setup(hass)

    assert hass.states.get("sensor.smartheat_wohnung1_heizkurve").state == "unknown"
    assert hass.states.get("sensor.smartheat_wohnung1_heizgrenze").state == "unknown"
    assert hass.states.get("sensor.smartheat_wohnung1_mindestvorlauf").state == "20.5"


@pytest.mark.parametrize("huge", [10**400, -10**400], ids=["plus", "minus"])
async def test_huge_integers_in_lever_values_are_unknown_without_an_error(hass, clock, caplog, huge):
    """Ein JSON-int ausserhalb des float-Bereichs (float() wirft OverflowError) ist unknown wie NaN; die uebrigen
    Entities bleiben unberuehrt."""
    await _setup(hass)

    _fire(hass, status_event(TENANT, "regelt", hebel={"curve": 1.2, "room_setpoint": huge, "heat_limit": huge,
                                                       "min_flow": 20.5}, gelernt={"curve": huge, "heat_limit": 16.2}))
    await hass.async_block_till_done()

    for key in ("parallelverschiebung", "heizgrenze", "gelernte_steigung"):
        assert hass.states.get(f"sensor.smartheat_wohnung1_{key}").state == "unknown", key
    assert hass.states.get("sensor.smartheat_wohnung1_heizkurve").state == "1.2"
    assert hass.states.get("sensor.smartheat_wohnung1_mindestvorlauf").state == "20.5"
    assert hass.states.get("sensor.smartheat_wohnung1_gelernte_heizgrenze").state == "16.2"
    assert hass.states.get(STATUS).state == "regelt"
    assert "OverflowError" not in caplog.text


async def test_parallel_shift_and_min_flow_sensors(hass, clock):
    """Die beiden Temperatur-Sensoren parallelverschiebung und mindestvorlauf kommen aus hebel; eine offset-Entity
    wird nicht erzeugt."""
    await _setup(hass)

    _fire(hass, status_event(TENANT, "regelt", hebel={"room_setpoint": 21.0, "min_flow": 20.5}))
    await hass.async_block_till_done()

    shift = hass.states.get("sensor.smartheat_wohnung1_parallelverschiebung")
    min_flow = hass.states.get("sensor.smartheat_wohnung1_mindestvorlauf")
    assert (shift.state, min_flow.state) == ("21.0", "20.5")
    assert shift.attributes["unit_of_measurement"] == min_flow.attributes["unit_of_measurement"] == "°C"
    assert hass.states.get("sensor.smartheat_wohnung1_offset") is None
    # Ein Hebel, der im Event fehlt, ist unknown, die uebrigen Sensoren bleiben davon unberuehrt.
    assert hass.states.get("sensor.smartheat_wohnung1_heizkurve").state == "unknown"


async def test_viessmann_entry_shows_level_and_room_setpoint(hass, clock):
    await _setup(hass, profile_id="viessmann_vicare", lever_set="viessmann_vicare",
                 hebel=["curve", "level", "room_setpoint"], shift_lever="level")

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


async def test_gestoppt_reason_keeps_the_addon_name_once_it_runs_again(hass, supervisor, clock, notes):
    """Fix-Runde 2, Fund 1: waehrend ein addon_gestoppt-Vorfall offen bleibt (Add-on laeuft
    wieder, aber noch kein Event), darf der Grund den Add-on-Namen nicht verlieren (der Zaehler
    fuer "gestoppt" ist dann schon zurueckgesetzt), und der unveraenderte Status darf keine neue
    Aktualisierung ausloesen."""
    _bridge(supervisor).state = AddonState.STOPPED
    entry = await _setup(hass)
    coordinator = entry.runtime_data

    await coordinator.async_check()  # 1/2 gestoppt gezaehlt
    clock["t"] += 300
    await coordinator.async_check()  # 2/2 -> addon_gestoppt
    assert "Heizungsbrücke" in hass.states.get(STATUS).attributes["grund"]

    signals = []
    async_dispatcher_connect(hass, coordinator.signal, lambda: signals.append(None))

    assert _bridge(supervisor).state == AddonState.STARTED  # der Waechter hat es gestartet, noch kein Event
    clock["t"] += 300
    await coordinator.async_check()

    assert hass.states.get(STATUS).state == "addon_gestoppt"
    assert "Heizungsbrücke" in hass.states.get(STATUS).attributes["grund"]
    assert signals == []  # unveraenderter Status/Grund: kein Update


async def test_time_tick_and_event_recheck_never_check_at_the_same_time(hass, supervisor, clock, notes):
    """Sperre `_checking`: waehrend eines Vorfalls loest ein Status-Ereignis eine Neupruefung aus; faellt im selben
    Loop-Schritt der Zeit-Takt (300 s), laeuft trotzdem genau eine Pruefung beim Supervisor. Die erste Pruefung haengt
    dazu im ersten Supervisor-Aufruf (`store.repositories_list`), bis beide Ausloeser gefeuert haben."""
    _bridge(supervisor).state = AddonState.STOPPED
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    await coordinator.async_check()
    clock["t"] += 300
    await coordinator.async_check()  # Vorfall addon_gestoppt, der Waechter startet die Bruecke
    assert coordinator.watchdog_status == "addon_gestoppt"
    repositories = supervisor.client.store.repositories_list
    assert repositories.await_count == 2  # je Pruefung genau ein Aufruf

    tor = asyncio.Event()
    sofort = repositories.side_effect

    async def wartet_am_tor():
        await tor.wait()
        return sofort()

    repositories.side_effect = wartet_am_tor
    clock["t"] += 300
    _fire(hass, status_event(TENANT, "regelt"))  # Neupruefung startet und haengt am Tor
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=300))  # Zeit-Takt im selben Schritt
    await asyncio.sleep(0)
    tor.set()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert repositories.await_count == 3
    assert coordinator.watchdog_status is None  # die eine Pruefung hat den Vorfall beendet
    assert notes[1] == ["smartheat_wohnung1_addon"]


_NICHT_AUFLOESBAR = pytest.mark.parametrize("source", [
    "http://github.com/LucaHartfuss/SmartHeat-for-HomeAssistant",
    "https://github.com/jemand/SmartHeat-for-HomeAssistant",
], ids=["http", "fork"])


@_NICHT_AUFLOESBAR
async def test_unaufloesbare_bruecke_mit_ereignissen_meldet_nichts(hass, supervisor, clock, notes, source):
    """Ist das Repository unter einer anderen Adresse eingetragen (`http://` oder ein Fork), loest der
    Waechter die laufende Heizungsbruecke nicht auf. Solange Ereignisse eintreffen, meldet er nichts (also nicht
    dauerhaft „Add-on gestoppt“) und startet nichts."""
    supervisor.repositories = [repository(source=source)]
    entry = await _setup(hass)
    coordinator = entry.runtime_data

    for _ in range(4):
        await coordinator.async_check()
        clock["t"] += 300
        _fire(hass, status_event(TENANT, "regelt"))
        await hass.async_block_till_done()

    assert _bridge(supervisor).state == AddonState.STARTED
    assert hass.states.get(STATUS).state == "regelt"
    assert coordinator.watchdog_status is None
    assert notes == ([], [])
    assert supervisor.actions == []


@_NICHT_AUFLOESBAR
async def test_unaufloesbare_bruecke_ohne_ereignisse_meldet_reagiert_nicht_ohne_neustart(
        hass, supervisor, clock, notes, source):
    """Ohne Ereignisse meldet die Stille (SILENCE_SECONDS = 900) „reagiert nicht“; ohne aufgeloeste Bruecke
    gibt es keinen Neustart und nie „Add-on gestoppt“."""
    supervisor.repositories = [repository(source=source)]
    entry = await _setup(hass)
    coordinator = entry.runtime_data

    for _ in range(2):  # 300 s und 600 s: noch keine Stille, kein „gestoppt“-Zaehler
        clock["t"] += 300
        await coordinator.async_check()
        assert coordinator.watchdog_status is None
    for _ in range(3):
        clock["t"] += 300
        await coordinator.async_check()
        await hass.async_block_till_done()

    assert hass.states.get(STATUS).state == "reagiert_nicht"
    assert notes == ([("smartheat_wohnung1_addon", WATCHDOG_MESSAGES["reagiert_nicht"])], [])
    assert supervisor.actions == []


async def test_removing_the_entry_dismisses_an_open_watchdog_notification(hass, supervisor, clock, notes):
    """Nach dem Entfernen des Eintrags bleibt keine Meldung des Waechters stehen."""
    created, dismissed = notes
    _bridge(supervisor).state = AddonState.STOPPED
    entry = await _setup(hass)
    await entry.runtime_data.async_check()
    clock["t"] += 300
    await entry.runtime_data.async_check()
    if created != [("smartheat_wohnung1_addon", WATCHDOG_MESSAGES["addon_gestoppt"])]:
        # Failed statt AssertionError: eine kaputte Vorbedingung soll als solche auffallen.
        pytest.fail(f"Vorbedingung: der Waechter meldet addon_gestoppt genau einmal, gemeldet: {created}")

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert dismissed == ["smartheat_wohnung1_addon"]


async def test_a_check_running_while_the_entry_is_removed_does_not_recreate_the_notification(
        hass, supervisor, clock, notes):
    """BR-HA-2: der erste Vorfall beginnt, der Start des Add-ons laeuft noch, der Nutzer loescht den Eintrag; die
    Pruefung darf danach keine Meldung mehr anlegen, die niemand mehr entwarnt."""
    created, dismissed = notes
    _bridge(supervisor).state = AddonState.STOPPED
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    await coordinator.async_check()
    clock["t"] += 300
    erreicht, frei = asyncio.Event(), asyncio.Event()
    start = supervisor.client.addons.start_addon.side_effect

    async def haengender_start(addon):
        erreicht.set()
        await frei.wait()
        start(addon)

    supervisor.client.addons.start_addon.side_effect = haengender_start
    pruefung = asyncio.ensure_future(coordinator.async_check())
    await asyncio.wait_for(erreicht.wait(), 5)

    await hass.config_entries.async_remove(entry.entry_id)
    frei.set()
    await asyncio.wait_for(pruefung, 5)
    await hass.async_block_till_done()

    assert dismissed == ["smartheat_wohnung1_addon"]
    assert created == []
