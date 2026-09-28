from custom_components.smartheat import const


def test_entity_and_notification_ids_use_the_tenant_slug():
    assert const.entity_id("sensor", "client1", "status") == "sensor.smartheat_client1_status"
    assert const.entity_id("binary_sensor", "Smoke-Test 01", "notbetrieb") == "binary_sensor.smartheat_smoke_test_01_notbetrieb"
    assert const.watchdog_notification_id("client1") == "smartheat_client1_addon"


def test_plausible_ranges_are_the_r4_values():
    assert const.PLAUSIBLE_RANGES == {"room": (5.0, 35.0), "outdoor": (-40.0, 45.0), "heat_limit": (5.0, 25.0)}


def test_status_event_contract_and_watchdog_values():
    # Gleiche Werte wie heizungsbruecke/status.py (Contract-Check 14).
    assert (const.STATUS_EVENT, const.STATUS_EVENT_SCHEMA) == ("smartheat_status", 1)
    assert const.ADDON_STATUS_VALUES == (
        "startet", "regelt", "konfigurationsfehler", "zugang_abgelehnt", "abo_beendet", "abo_inaktiv",
        "notbetrieb", "datenfehler", "abgemeldet",
    )
    assert const.STATUS_SENSOR_VALUES == const.ADDON_STATUS_VALUES + ("addon_gestoppt", "reagiert_nicht")
    assert const.HINT_CATEGORIES == ("raumfuehler", "batterie", "manueller_eingriff", "quellwechsel")
    assert (const.WATCHDOG_INTERVAL_SECONDS, const.STOPPED_AFTER_CHECKS, const.SILENCE_SECONDS) == (300, 2, 900)
    assert (const.MAX_RESTARTS_PER_WINDOW, const.RESTART_WINDOW_SECONDS) == (3, 3600)


def test_min_addon_version_is_0_20_0():
    assert const.MIN_ADDON_VERSIONS["heizungsbruecke"] == "0.20.0"


def test_wait_seconds_are_the_plan_values():
    assert (const.SIGN_OFF_WAIT_SECONDS, const.STATUS_WAIT_SECONDS) == (60, 180)
