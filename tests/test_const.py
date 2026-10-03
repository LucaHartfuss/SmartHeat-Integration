import pytest

from custom_components.smartheat import const


def test_entity_and_notification_ids_use_the_tenant_slug():
    assert const.entity_id("sensor", "client1", "status") == "sensor.smartheat_client1_status"
    assert const.entity_id("binary_sensor", "Smoke-Test 01", "notbetrieb") == "binary_sensor.smartheat_smoke_test_01_notbetrieb"
    assert const.watchdog_notification_id("client1") == "smartheat_client1_addon"


def test_plausible_ranges_are_the_r4_values():
    assert const.PLAUSIBLE_RANGES == {"room": (5.0, 35.0), "outdoor": (-40.0, 45.0), "heat_limit": (5.0, 25.0)}


def test_status_event_contract_and_watchdog_values():
    # Gleiche Werte wie heizungsbruecke/status.py (Contract-Check 14).
    assert (const.STATUS_EVENT, const.STATUS_EVENT_SCHEMA) == ("smartheat_status", 2)
    assert const.ADDON_STATUS_VALUES == (
        "startet", "regelt", "konfigurationsfehler", "zugang_abgelehnt", "abo_beendet", "abo_inaktiv",
        "notbetrieb", "datenfehler", "abgemeldet",
    )
    assert const.STATUS_SENSOR_VALUES == const.ADDON_STATUS_VALUES + ("addon_gestoppt", "reagiert_nicht")
    assert const.HINT_CATEGORIES == (
        "raumfuehler", "batterie", "manueller_eingriff", "quellwechsel", "therme", "schreibbudget", "schreibzaehler",
    )
    assert const.HINT_FIELDS == ("raumfuehler_ausgefallen", "batterie_niedrig", "manueller_eingriff", "waerme_fehlt")
    assert (const.WATCHDOG_INTERVAL_SECONDS, const.STOPPED_AFTER_CHECKS, const.SILENCE_SECONDS) == (300, 2, 900)
    assert (const.MAX_RESTARTS_PER_WINDOW, const.RESTART_WINDOW_SECONDS) == (3, 3600)


def test_role_domains_only_cover_fields_outside_the_lever_sets():
    assert const.ROLE_DOMAINS == {
        "entity_room_target": ["sensor", "climate"],
        "entity_outdoor_temp": ["sensor", "weather"],
        "entity_flow_setpoint": ["sensor"],
    }


def test_status_event_fields_schema_2():
    assert const.STATUS_EVENT_FIELDS == (
        "schema", "tenant_id", "setup_id", "addon_version", "status", "grund", "notbetrieb", "datenfehler",
        "boost", "letzte_serverantwort", "hebelsatz", "hebel", "gelernt", "abo", "abo_frist_ende", "hinweise",
    )


def test_heat_limit_is_a_writable_number_tp12h():
    """TP12h: SmartHeat schreibt die Heizgrenze, daher nur number (kein Sensor mehr)."""
    assert const.LEVER_SET_DOMAINS["vaillant_vrc720"]["entity_heat_limit"] == ["number"]
    assert const.LEVER_SET_DOMAINS["weishaupt_wwp"]["entity_heat_limit"] == ["number"]
    assert "entity_heat_limit" not in const.ENTITY_FILTERS


def test_min_addon_version():
    assert const.MIN_ADDON_VERSIONS[const.HEIZUNGSBRUECKE_ADDON_SLUG] == "0.33.0"


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


def test_lever_options_mirror_the_add_on_roles():
    assert const.LEVER_OPTIONS == {
        "curve": "entity_curve_current", "room_setpoint": "entity_shift_current", "level": "entity_level_current",
        "heat_limit": "entity_heat_limit", "min_flow": "entity_min_flow",
    }
    assert const.field_for_role("room_setpoint") == "entity_shift_current"
    assert const.field_for_role("mode_select") == "entity_mode_select"
    assert const.field_for_role("energy_electrical_total") == "entity_energy_electrical_total"


@pytest.mark.parametrize("lever_set, levers", [
    ("vaillant_vrc720", ("curve", "room_setpoint", "heat_limit", "min_flow")),
    ("weishaupt_wwp", ("curve", "room_setpoint", "heat_limit")),
    ("weishaupt_wwp_basis", ("room_setpoint",)),
    ("viessmann_vicare", ("curve", "room_setpoint", "level")),
])
def test_lever_set_levers(lever_set, levers):
    assert const.lever_set_levers(lever_set) == levers


def test_every_lever_set_field_has_domains():
    for lever_set in const.LEVER_SETS:
        for field in const.plant_fields(lever_set):
            assert const.field_domains(lever_set, field), (lever_set, field)
    assert const.field_domains("vaillant_vrc720", "entity_shift_current") == ["climate"]
    assert const.field_domains("weishaupt_wwp", "entity_shift_current") == ["number"]
    assert const.field_domains("weishaupt_wwp", "entity_mode_select") == ["select"]
    assert const.field_domains("viessmann_vicare", "entity_mode_select") == ["climate"]


def test_write_role_fields_exclude_the_outdoor_source():
    assert "entity_outdoor_temp" not in const.write_role_fields("weishaupt_wwp")
    assert "entity_mode_select" in const.write_role_fields("weishaupt_wwp")


def _complete(lever_set):
    return {"profile_id": "p", "lever_set": lever_set, "shift_lever": "level",
            "entities": {field: "x.y" for field in const.LEVER_SET_FIELDS[lever_set]}}


def test_entry_incomplete_per_lever_set():
    assert not const.entry_incomplete(_complete("viessmann_vicare"))
    assert const.entry_incomplete({**_complete("viessmann_vicare"), "lever_set": None})
    assert const.entry_incomplete({**_complete("viessmann_vicare"), "lever_set": "unbekannt"})
    data = _complete("viessmann_vicare")
    del data["entities"]["entity_level_current"]
    assert const.entry_incomplete(data)
    # client1 vor "Neu konfigurieren": alle Vaillant-Felder, aber kein lever_set
    assert const.entry_incomplete({**_complete("vaillant_vrc720"), "lever_set": None})
    # Schluss-Review Plan 3c: ohne gueltigen Verschiebungshebel unvollstaendig (sonst raum_soll statt
    # parallelverschiebung und die Bereinigung verwaister Entities griffe falsch)
    for shift_lever in (None, "", 7):
        assert const.entry_incomplete({**_complete("vaillant_vrc720"), "shift_lever": shift_lever})
    data = _complete("vaillant_vrc720")
    del data["shift_lever"]
    assert const.entry_incomplete(data)


def test_versions_and_contracts():
    assert const.REQUIRED_CATALOG_VERSION == 3
    assert const.STATUS_EVENT_SCHEMA == 2
    assert const.MIN_ADDON_VERSIONS["heizungsbruecke"] == "0.33.0"
    assert const.HINT_CATEGORIES[-2:] == ("schreibbudget", "schreibzaehler")
    assert const.KPI_ENERGY_CHANNELS[-1] == "electrical_total"
    assert (const.OPTION_LEVER_SET, const.OPTION_POLL_INTERVAL) == ("lever_set", "poll_interval_seconds")
    assert const.POLL_INTERVAL_OPTION_RANGE == (10, 3600)
    assert const.CLIENT_TYPE_HA == "ha"
    assert const.PROVISION_CLIENT_KEYS == ("client_type", "client_version")
