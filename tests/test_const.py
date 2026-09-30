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
    assert const.HINT_CATEGORIES == ("raumfuehler", "batterie", "manueller_eingriff", "quellwechsel", "therme")
    assert const.HINT_FIELDS == ("raumfuehler_ausgefallen", "batterie_niedrig", "manueller_eingriff", "waerme_fehlt")
    assert (const.WATCHDOG_INTERVAL_SECONDS, const.STOPPED_AFTER_CHECKS, const.SILENCE_SECONDS) == (300, 2, 900)
    assert (const.MAX_RESTARTS_PER_WINDOW, const.RESTART_WINDOW_SECONDS) == (3, 3600)


def test_role_domains_tp11():
    # Ruling #11b (Controller): entity_shift_current ist nur climate (Zonen-Wunschtemperatur
    # ueber die Vaillant-Climate-Entity), kein direkter number-Wert - anders als im urspruenglichen
    # Task-18-Brief.
    assert const.ROLE_DOMAINS["entity_shift_current"] == ["climate"]
    assert const.ROLE_DOMAINS["entity_min_flow"] == ["number"]
    assert const.ROLE_DOMAINS["entity_flow_setpoint"] == ["sensor"]
    assert "entity_offset_current" not in const.ROLE_DOMAINS


def test_status_fields_tp11():
    assert "parallelverschiebung" in const.STATUS_EVENT_FIELDS
    assert "mindestvorlauf" in const.STATUS_EVENT_FIELDS
    assert "offset" not in const.STATUS_EVENT_FIELDS


def test_min_addon_version():
    assert const.MIN_ADDON_VERSIONS[const.HEIZUNGSBRUECKE_ADDON_SLUG] == "0.24.0"


def test_wait_seconds_are_the_plan_values():
    assert (const.SIGN_OFF_WAIT_SECONDS, const.STATUS_WAIT_SECONDS) == (60, 180)


def test_addon_display_names_match_the_real_addon_names():
    # M3: der Kunde muss das Add-on unter diesem Namen in Einstellungen -> Add-ons wiederfinden
    # (heizungsbruecke/config.yaml: "Heizungsbruecke", cloudflared_access_mqtt/config.yaml:
    # "Cloudflared Access TCP-Bridge"); Heizungsbruecke bekommt in Kundentexten einen Umlaut (F7).
    assert const.ADDON_DISPLAY_NAMES == {
        const.HEIZUNGSBRUECKE_ADDON_SLUG: "Heizungsbrücke",
        const.CLOUDFLARED_ADDON_SLUG: "Cloudflared Access TCP-Bridge",
    }
