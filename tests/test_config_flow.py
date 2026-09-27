"""Config-Flow 2.0 (Spec TP6, Tests laut Spec 6)."""
import logging
from datetime import timedelta

import json
from pathlib import Path

import pytest
from homeassistant.components.hassio import AddonError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartheat.api_client import ApiError, CannotConnect, InvalidAuth
from custom_components.smartheat.const import DOMAIN, status_entity_id
from custom_components.smartheat.supervisor_client import (
    AddonNotFoundError, AddonOutdatedError, AmbiguousAddonMatchError,
)

from .flow_helpers import (
    CATALOG, CF_SECRET, CURVE, HEAT_LIMIT, MQTT_PASSWORD, OFFSET, OUTDOOR, PLANT_INPUT, PROFILE_PARAMS,
    ROOMS_INPUT, SYSTEM_INPUT, TENANT, configure, enable_supervisor, fast_status_wait, finish_progress,
    has_default, login, marker, mock_addons, mock_server, register_phones, select_values, setup_mypyllant,
    setup_rooms, start, suggested,
)


async def _reach(hass, monkeypatch, step: str, *, phones=("mobile_app_pixel",), **server):
    """Normalfall bis zum genannten Schritt. Liefert (result, server_mocks)."""
    enable_supervisor(hass, monkeypatch)
    mocks = mock_server(monkeypatch, **server)
    setup_mypyllant(hass)
    setup_rooms(hass)
    register_phones(hass, *phones)
    fast_status_wait(monkeypatch)
    result = await login(hass, await start(hass))
    order = [("system", SYSTEM_INPUT), ("rooms", ROOMS_INPUT), ("plant_values", PLANT_INPUT),
             ("notifications", None), ("summary", None)]
    for step_id, data in order:
        assert result["step_id"] == step_id, result
        if step_id == step:
            return result, mocks
        if step_id == "notifications" and phones:
            # Das Frontend sendet die Vorbelegung (suggested_value) unveraendert mit.
            data = {"notify_services": suggested(result, "notify_services")}
        result = await configure(hass, result, data if data is not None else {})
    raise AssertionError(f"Schritt {step} nicht erreicht")


# --- A: Vorabpruefung und Login ---

async def test_aborts_when_not_supervisor(hass, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)

    result = await start(hass)

    assert (result["type"], result["reason"]) == ("abort", "not_supervisor")


@pytest.mark.parametrize("error,reason,placeholders", [
    (AddonNotFoundError("heizungsbruecke"), "addon_missing", {"addon": "heizungsbruecke"}),
    (AmbiguousAddonMatchError("heizungsbruecke", ["a_heizungsbruecke", "b_heizungsbruecke"]), "addon_ambiguous",
     {"addon": "heizungsbruecke"}),
    (AddonOutdatedError("heizungsbruecke", "0.18.0", "0.19.0"), "addon_outdated",
     {"addon": "heizungsbruecke", "installed": "0.18.0", "required": "0.19.0"}),
    (AddonError("Supervisor weg"), "supervisor_unavailable", {}),
])
async def test_addon_precheck_aborts_before_login(hass, monkeypatch, error, reason, placeholders):
    resolve = enable_supervisor(hass, monkeypatch)
    resolve.side_effect = error
    mocks = mock_server(monkeypatch)

    result = await start(hass)

    assert (result["type"], result["reason"]) == ("abort", reason)
    assert result["description_placeholders"] == placeholders
    mocks.login.assert_not_awaited()


async def test_login_form_after_a_clean_precheck(hass, monkeypatch):
    enable_supervisor(hass, monkeypatch)

    result = await start(hass)

    assert (result["type"], result["step_id"], result["errors"]) == ("form", "user", {})


@pytest.mark.parametrize("error,key", [(InvalidAuth("x"), "invalid_auth"), (CannotConnect("x"), "cannot_connect"),
                                       (ApiError("x"), "unknown")])
async def test_login_errors(hass, monkeypatch, error, key):
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch).login.side_effect = error

    result = await login(hass, await start(hass))

    assert (result["step_id"], result["errors"]) == ("user", {"base": key})


async def test_account_without_tenants(hass, monkeypatch):
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch, tenants=())

    result = await login(hass, await start(hass))

    assert result["errors"] == {"base": "no_tenants"}


# --- B: Tenant ---

async def test_single_tenant_and_single_integration_go_straight_to_system(hass, monkeypatch):
    result, _ = await _reach(hass, monkeypatch, "system")

    assert result["step_id"] == "system"


async def test_two_tenants_show_the_tenant_form(hass, monkeypatch):
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch, tenants=("wohnung1", "wohnung2"))
    setup_mypyllant(hass)

    result = await login(hass, await start(hass))
    assert result["step_id"] == "tenant"
    result = await configure(hass, result, {"tenant_id": "wohnung2"})

    assert result["step_id"] == "system"


async def test_already_configured_tenant_aborts(hass, monkeypatch):
    MockConfigEntry(domain=DOMAIN, unique_id=TENANT, data={"tenant_id": TENANT}).add_to_hass(hass)
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch)
    setup_mypyllant(hass)

    result = await login(hass, await start(hass))

    assert (result["type"], result["reason"]) == ("abort", "already_configured")


# --- C: Heizungs-Integration ---

async def test_catalog_unreachable_shows_cannot_connect_and_retry_works(hass, monkeypatch):
    enable_supervisor(hass, monkeypatch)
    mocks = mock_server(monkeypatch)
    setup_mypyllant(hass)
    mocks.get_catalog.side_effect = [CannotConnect("weg"), CATALOG]

    result = await login(hass, await start(hass))
    assert (result["step_id"], result["errors"]) == ("heating", {"base": "cannot_connect"})
    result = await configure(hass, result, {})

    assert result["step_id"] == "system"


async def test_session_expired_while_loading_the_catalog(hass, monkeypatch):
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch).get_catalog.side_effect = InvalidAuth("abgelaufen")
    setup_mypyllant(hass)

    result = await login(hass, await start(hass))

    assert (result["step_id"], result["errors"]) == ("user", {"base": "session_expired"})


async def test_no_supported_integration_lists_the_supported_ones(hass, monkeypatch):
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch)

    result = await login(hass, await start(hass))

    assert (result["type"], result["reason"]) == ("abort", "no_supported_integration")
    assert result["description_placeholders"] == {"supported": "myVAILLANT"}


async def test_two_installed_integrations_show_the_heating_form(hass, monkeypatch):
    catalog = {**CATALOG, "integrations": CATALOG["integrations"] + [
        {**CATALOG["integrations"][0], "domain": "andere", "label": "Andere Heizung"},
    ]}
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch, catalog=catalog)
    setup_mypyllant(hass)
    MockConfigEntry(domain="andere").add_to_hass(hass)

    result = await login(hass, await start(hass))

    assert result["step_id"] == "heating"
    assert set(select_values(result, "integration")) == {"mypyllant", "andere"}
    result = await configure(hass, result, {"integration": "mypyllant"})
    assert result["step_id"] == "system"


async def test_no_verified_profile_for_the_manufacturer(hass, monkeypatch):
    catalog = {**CATALOG, "profiles": [p for p in CATALOG["profiles"] if p["hersteller"] != "Vaillant"]}
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch, catalog=catalog)
    setup_mypyllant(hass)

    result = await login(hass, await start(hass))

    assert (result["type"], result["reason"]) == ("abort", "no_verified_profiles")


# --- D: System ---

async def test_verteilsystem_has_no_default_and_erzeuger_typ_is_suggested(hass, monkeypatch):
    result, _ = await _reach(hass, monkeypatch, "system")

    assert has_default(result, "verteilsystem") is False
    assert marker(result, "erzeuger_typ").default() == "gastherme"
    # Sicherheitsfrage: immer beide Karten, unabhaengig davon, welche Profile es gibt.
    assert select_values(result, "verteilsystem") == ["fussbodenheizung", "heizkoerper"]
    # Erzeugertypen aus allen Katalog-Profilen (hier auch das nicht verifizierte Weishaupt-Profil).
    assert select_values(result, "erzeuger_typ") == ["gastherme", "waermepumpe"]
    assert "circuit" not in [str(key) for key in result["data_schema"].schema]


@pytest.mark.parametrize("model", ["VRC 720", "aroTHERM und ecoTEC"])
async def test_erzeuger_typ_without_clear_hint_has_no_default(hass, monkeypatch, model):
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch)
    setup_mypyllant(hass, model=model)

    result = await login(hass, await start(hass))

    assert has_default(result, "erzeuger_typ") is False


async def test_two_circuits_offer_a_choice(hass, monkeypatch):
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch)
    setup_mypyllant(hass, circuits=("0", "1"))
    setup_rooms(hass)

    result = await login(hass, await start(hass))
    values = select_values(result, "circuit")
    result = await configure(hass, result, {**SYSTEM_INPUT, "circuit": values[1]})
    result = await configure(hass, result, ROOMS_INPUT)

    assert suggested(result, "entity_curve_current") == "number.zuhause_circuit_1_heating_curve"


async def test_fussbodenheizung_without_a_verified_profile_is_an_error(hass, monkeypatch):
    # Der Katalog hat fuer Vaillant nur Gastherme + Heizkoerper; die Karte Fussbodenheizung wird
    # trotzdem angeboten und fuehrt zum Fehler statt zu einem geratenen Profil.
    result, _ = await _reach(hass, monkeypatch, "system")

    result = await configure(hass, result, {"verteilsystem": "fussbodenheizung", "erzeuger_typ": "gastherme"})

    assert (result["step_id"], result["errors"]) == ("system", {"base": "profile_combination_unsupported"})


async def test_unsupported_combination_is_an_error(hass, monkeypatch):
    # Zweites verifiziertes Vaillant-Profil: beide Werte sind einzeln waehlbar, die Kombination
    # Heizkoerper + Waermepumpe gibt es trotzdem nicht.
    catalog = {**CATALOG, "profiles": CATALOG["profiles"] + [{
        "profile_id": "vaillant_waermepumpe_fussbodenheizung", "hersteller": "Vaillant",
        "erzeuger_typ": "Waermepumpe", "verteilsystem": "Fussbodenheizung", "verified": True,
        "telemetry_capabilities": None,
    }]}
    result, _ = await _reach(hass, monkeypatch, "system", catalog=catalog)

    result = await configure(hass, result, {"verteilsystem": "heizkoerper", "erzeuger_typ": "waermepumpe"})

    assert (result["step_id"], result["errors"]) == ("system", {"base": "profile_combination_unsupported"})


async def test_integration_without_complete_circuit_aborts(hass, monkeypatch):
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch)
    setup_mypyllant(hass, circuits=())

    result = await login(hass, await start(hass))

    assert (result["type"], result["reason"]) == ("abort", "no_heating_circuit")
    assert result["description_placeholders"] == {"integration": "myVAILLANT"}


# --- E: Raeume ---

async def test_rooms_accept_a_thermostat_as_sensor_and_target(hass, monkeypatch):
    result, _ = await _reach(hass, monkeypatch, "rooms")

    result = await configure(hass, result, {"room_sensors": ["climate.wz"], "entity_room_target": "climate.wz"})

    assert result["step_id"] == "plant_values"


@pytest.mark.parametrize("state,attributes,error", [
    ("unavailable", {}, "entity_unavailable"),
    ("warm", {"unit_of_measurement": "°C"}, "not_numeric"),
    ("70", {"unit_of_measurement": "°F"}, "unit_mismatch"),
    ("40", {"unit_of_measurement": "°C"}, "out_of_range"),
])
async def test_room_sensor_hard_checks(hass, monkeypatch, state, attributes, error):
    result, _ = await _reach(hass, monkeypatch, "rooms")
    hass.states.async_set("sensor.kz_temperatur", state, attributes)

    result = await configure(hass, result, ROOMS_INPUT)

    assert (result["step_id"], result["errors"]) == ("rooms", {"room_sensors": error})


async def test_room_target_out_of_range(hass, monkeypatch):
    result, _ = await _reach(hass, monkeypatch, "rooms")
    hass.states.async_set("climate.wz", "heat", {"current_temperature": 21.2, "temperature": 4})

    result = await configure(hass, result, ROOMS_INPUT)

    assert result["errors"] == {"entity_room_target": "out_of_range"}


async def test_empty_room_sensor_list(hass, monkeypatch):
    result, _ = await _reach(hass, monkeypatch, "rooms")

    result = await configure(hass, result, {"room_sensors": [], "entity_room_target": "climate.wz"})

    assert result["errors"] == {"room_sensors": "room_sensors_required"}


async def test_same_sensor_as_room_sensor_and_target_is_a_duplicate(hass, monkeypatch):
    result, _ = await _reach(hass, monkeypatch, "rooms")

    result = await configure(hass, result, {"room_sensors": ["sensor.wz_temperatur"], "entity_room_target": "sensor.wz_temperatur"})

    assert result["errors"] == {"room_sensors": "duplicate_entity", "entity_room_target": "duplicate_entity"}


# --- F: Anlagenwerte ---

async def test_plant_values_are_prefilled_with_origin(hass, monkeypatch):
    result, _ = await _reach(hass, monkeypatch, "plant_values")

    assert [suggested(result, field) for field in (
        "entity_curve_current", "entity_offset_current", "entity_heat_limit", "entity_outdoor_temp",
    )] == [CURVE, OFFSET, HEAT_LIMIT, OUTDOOR]
    assert "myVAILLANT" in result["description_placeholders"]["origins"]
    assert result["data_schema"].schema["advanced"].options["collapsed"] is True
    assert suggested(result, "entity_operating_mode", section="advanced") is None


async def test_weather_replaces_a_missing_outdoor_sensor(hass, monkeypatch):
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch)
    setup_mypyllant(hass, outdoor=False)
    setup_rooms(hass)
    hass.states.async_set("weather.forecast_home", "sunny", {"temperature": 3.2, "temperature_unit": "°C"})

    result = await login(hass, await start(hass))
    result = await configure(hass, result, SYSTEM_INPUT)
    result = await configure(hass, result, ROOMS_INPUT)

    assert suggested(result, "entity_outdoor_temp") == "weather.forecast_home"
    assert "weather.forecast_home" in result["description_placeholders"]["origins"]
    result = await configure(hass, result, {**PLANT_INPUT, "entity_outdoor_temp": "weather.forecast_home"})
    assert result["step_id"] == "notifications"
    result = await configure(hass, result, {})
    assert result["step_id"] == "summary"
    assert result["description_placeholders"]["outdoor_temperature"] == "3.2"


async def test_no_outdoor_source_at_all_leaves_the_field_empty(hass, monkeypatch):
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch)
    setup_mypyllant(hass, outdoor=False)
    setup_rooms(hass)

    result = await login(hass, await start(hass))
    result = await configure(hass, result, SYSTEM_INPUT)
    result = await configure(hass, result, ROOMS_INPUT)

    assert suggested(result, "entity_outdoor_temp") is None


async def test_heat_limit_out_of_range(hass, monkeypatch):
    result, _ = await _reach(hass, monkeypatch, "plant_values")
    hass.states.async_set(HEAT_LIMIT, "30", {"unit_of_measurement": "°C"})

    result = await configure(hass, result, PLANT_INPUT)

    assert result["errors"] == {"entity_heat_limit": "out_of_range"}


async def test_outdoor_equal_to_a_room_sensor_is_a_duplicate(hass, monkeypatch):
    result, _ = await _reach(hass, monkeypatch, "plant_values")

    result = await configure(hass, result, {**PLANT_INPUT, "entity_outdoor_temp": "sensor.kz_temperatur"})

    assert result["errors"] == {"entity_outdoor_temp": "duplicate_entity"}


async def test_energy_role_needs_total_increasing(hass, monkeypatch):
    result, _ = await _reach(hass, monkeypatch, "plant_values")
    hass.states.async_set("sensor.gas", "123", {"state_class": "total"})

    result = await configure(hass, result, {**PLANT_INPUT, "advanced": {"entity_energy_primary_heating": "sensor.gas"}})

    assert result["errors"] == {
        "entity_energy_primary_heating": "state_class_expected_total_increasing", "base": "advanced_invalid",
    }


async def test_kpi_suggestions_from_the_catalog_land_in_the_advanced_section(hass, monkeypatch):
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch)
    entry = setup_mypyllant(hass)
    setup_rooms(hass)
    er.async_get(hass).async_get_or_create(
        "sensor", "mypyllant", "mypyllant_SYSTEM_home_water_pressure", config_entry=entry,
        suggested_object_id="zuhause_system_water_pressure",
    )

    result = await login(hass, await start(hass))
    result = await configure(hass, result, SYSTEM_INPUT)
    result = await configure(hass, result, ROOMS_INPUT)

    assert suggested(result, "entity_system_water_pressure", section="advanced") == "sensor.zuhause_system_water_pressure"


# --- G: Benachrichtigungen ---

async def test_all_phones_preselected_and_batteries_listed(hass, monkeypatch):
    result, _ = await _reach(hass, monkeypatch, "notifications", phones=("mobile_app_pixel", "mobile_app_iphone"))

    assert suggested(result, "notify_services") == ["notify.mobile_app_iphone", "notify.mobile_app_pixel"]
    # Vorbelegung statt default: sonst fuellt voluptuous einen weggelassenen Schluessel wieder
    # mit allen Handys auf (T13).
    assert not has_default(result, "notify_services")
    assert "sensor.wz_batterie" in result["description_placeholders"]["batteries"]


@pytest.mark.parametrize("data", [{}, {"notify_services": []}])
async def test_deselecting_all_phones_writes_an_empty_list(hass, monkeypatch, data):
    """Alle Handys abgewaehlt: das Frontend sendet [] oder laesst den Schluessel weg."""
    result, _ = await _reach(hass, monkeypatch, "notifications", phones=("mobile_app_pixel", "mobile_app_iphone"))
    calls = mock_addons(hass, monkeypatch)

    result = await configure(hass, result, data)
    assert result["description_placeholders"]["recipients"] == "0"
    result = await finish_progress(hass, await configure(hass, result, {}))

    assert result["type"] == "create_entry"
    assert result["data"]["notify_services"] == []
    assert calls.options["heizungsbruecke"]["notify_services"] == []


async def test_notifications_step_without_phones_explains_and_lists_batteries(hass, monkeypatch):
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch)
    setup_mypyllant(hass)
    setup_rooms(hass)

    result = await login(hass, await start(hass))
    result = await configure(hass, result, SYSTEM_INPUT)
    result = await configure(hass, result, ROOMS_INPUT)
    result = await configure(hass, result, PLANT_INPUT)

    assert result["step_id"] == "notifications"
    assert list(result["data_schema"].schema) == []
    assert "companion app" in result["description_placeholders"]["phones_note"]
    assert "sensor.wz_batterie" in result["description_placeholders"]["batteries"]
    result = await configure(hass, result, {})
    assert result["step_id"] == "summary"
    assert result["description_placeholders"]["recipients"] == "0"


# --- H: Zusammenfassung ---

async def test_summary_without_warnings_has_no_checkboxes_and_shows_values(hass, monkeypatch):
    result, _ = await _reach(hass, monkeypatch, "summary")

    assert list(result["data_schema"].schema) == []
    placeholders = result["description_placeholders"]
    assert placeholders["room_temperature"] == "20.8"  # (21.0 + 20.5) / 2
    assert placeholders["room_sensor_count"] == "2"
    assert placeholders["outdoor_source"] == OUTDOOR
    assert placeholders["recipients"] == "1"
    assert "sensor.wz_batterie" in placeholders["batteries"]


async def test_deviating_room_sensor_needs_confirmation(hass, monkeypatch):
    result, _ = await _reach(hass, monkeypatch, "rooms")
    hass.states.async_set("sensor.kz_temperatur", "25.0", {"unit_of_measurement": "°C"})
    for data in (ROOMS_INPUT, PLANT_INPUT, {}):
        result = await configure(hass, result, data)

    assert result["step_id"] == "summary"
    assert "sensor.kz_temperatur" in result["description_placeholders"]["warnings"]
    result = await configure(hass, result, {"confirm_deviation": False})
    assert result["errors"] == {"base": "warnings_not_confirmed"}


async def test_stale_source_needs_confirmation(hass, monkeypatch, freezer):
    result, _ = await _reach(hass, monkeypatch, "notifications")
    freezer.tick(timedelta(hours=7))

    result = await configure(hass, result, {})

    assert "confirm_stale" in [str(key) for key in result["data_schema"].schema]
    result = await configure(hass, result, {"confirm_stale": False})
    assert result["errors"] == {"base": "warnings_not_confirmed"}


# --- I: Einrichten (Fortschritt, Status, Fehler) ---

async def _to_setup(hass, monkeypatch, **addons):
    result, mocks = await _reach(hass, monkeypatch, "summary")
    calls = mock_addons(hass, monkeypatch, **addons)
    result = await configure(hass, result, {})
    assert result["step_id"] == "setup"
    return result, mocks, calls


async def test_ready_addon_creates_the_entry_without_credentials_and_logs_out(hass, monkeypatch):
    result, mocks, calls = await _to_setup(hass, monkeypatch, existing_options={
        "local_check_interval_seconds": 120, "entity_room_actual": "sensor.alt", "notify_service": "notify.alt",
        "profile": "vaillant_gastherme_heizkoerper", "mqtt_password": "alt",
    })

    result = await finish_progress(hass, result)

    assert result["type"] == "create_entry"
    assert result["data"] == {
        "tenant_id": TENANT, "profile_id": "vaillant_gastherme_heizkoerper", "integration_domain": "mypyllant",
        "circuit": {"config_entry_id": result["data"]["circuit"]["config_entry_id"], "system_key": "SYSTEM", "circuit": "0"},
        "entities": {
            "entity_room_target": "climate.wz::temperature", "entity_curve_current": CURVE,
            "entity_offset_current": OFFSET, "entity_heat_limit": HEAT_LIMIT, "entity_outdoor_temp": OUTDOOR,
        },
        "room_sensors": ["sensor.wz_temperatur", "sensor.kz_temperatur"],
        "notify_services": ["notify.mobile_app_pixel"],
        "battery_entities": ["sensor.wz_batterie"],
    }
    assert MQTT_PASSWORD not in str(result["data"]) and CF_SECRET not in str(result["data"])
    mocks.provision.assert_awaited_once_with("tok123", TENANT, "vaillant_gastherme_heizkoerper")
    mocks.logout.assert_awaited_once_with("tok123")
    assert calls.restarts == ["cloudflared_access_mqtt", "heizungsbruecke"]
    options = calls.options["heizungsbruecke"]
    assert options == {
        "local_check_interval_seconds": 120, **PROFILE_PARAMS,
        "tenant_id": TENANT, "mqtt_username": "wohnung1_a1b2c3d4", "mqtt_password": MQTT_PASSWORD,
        "accounts_api_base_url": "https://accounts.hartfussha.org", "setup_id": options["setup_id"],
        "room_sensors": ["sensor.wz_temperatur", "sensor.kz_temperatur"],
        "notify_services": ["notify.mobile_app_pixel"], "battery_entities": ["sensor.wz_batterie"],
        "entity_room_target": "climate.wz::temperature", "entity_curve_current": CURVE,
        "entity_offset_current": OFFSET, "entity_heat_limit": HEAT_LIMIT, "entity_outdoor_temp": OUTDOOR,
    }
    assert calls.options["cloudflared_access_mqtt"]["service_token_secret"] == CF_SECRET


async def test_logout_failure_is_only_logged(hass, monkeypatch, caplog):
    result, mocks, _ = await _to_setup(hass, monkeypatch)
    mocks.logout.side_effect = CannotConnect("weg")

    with caplog.at_level(logging.WARNING):
        result = await finish_progress(hass, result)

    assert result["type"] == "create_entry"
    assert "Logout" in caplog.text


async def test_configuration_error_offers_back_without_second_provision(hass, monkeypatch):
    result, mocks, calls = await _to_setup(hass, monkeypatch, status="konfigurationsfehler",
                                           grund="Entity fehlt in Home Assistant: sensor.kz_temperatur")

    result = await finish_progress(hass, result)
    assert (result["type"], result["step_id"]) == ("menu", "setup_failed")
    assert result["description_placeholders"]["grund"] == "Entity fehlt in Home Assistant: sensor.kz_temperatur"
    first_setup_id = calls.options["heizungsbruecke"]["setup_id"]

    result = await configure(hass, result, {"next_step_id": "rooms"})
    assert result["step_id"] == "rooms"
    assert suggested(result, "room_sensors") == ROOMS_INPUT["room_sensors"]
    calls = mock_addons(hass, monkeypatch, status="bereit")
    for data in ({"room_sensors": ["sensor.wz_temperatur"], "entity_room_target": "climate.wz"}, PLANT_INPUT, {}, {}):
        result = await configure(hass, result, data)
    result = await finish_progress(hass, result)

    assert result["type"] == "create_entry"
    mocks.provision.assert_awaited_once()
    assert calls.options["heizungsbruecke"]["setup_id"] != first_setup_id
    assert calls.options["heizungsbruecke"]["room_sensors"] == ["sensor.wz_temperatur"]


async def test_back_after_a_failure_keeps_the_corrected_plant_values_and_phones(hass, monkeypatch):
    """I-1: "Zurueck zur Auswahl" belegt Anlagenwerte, KPI und Handys mit der Auswahl des Kunden
    vor, nicht erneut mit der Erkennung. Durchklicken schreibt die Korrekturen, nicht die Erkennung."""
    result, _ = await _reach(hass, monkeypatch, "plant_values", phones=("mobile_app_pixel", "mobile_app_iphone"))
    hass.states.async_set("sensor.aussen", "5.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("number.andere_kurve", "1.3")
    hass.states.async_set("sensor.gas", "123", {"state_class": "total_increasing"})
    corrected = {
        **PLANT_INPUT, "entity_outdoor_temp": "sensor.aussen", "entity_curve_current": "number.andere_kurve",
        "advanced": {"entity_energy_primary_heating": "sensor.gas"},
    }
    result = await configure(hass, result, corrected)
    result = await configure(hass, result, {"notify_services": ["notify.mobile_app_pixel"]})
    mock_addons(hass, monkeypatch, status="konfigurationsfehler", grund="x")
    result = await finish_progress(hass, await configure(hass, result, {}))
    assert result["step_id"] == "setup_failed"

    result = await configure(hass, result, {"next_step_id": "rooms"})
    result = await configure(hass, result, ROOMS_INPUT)
    assert result["step_id"] == "plant_values"
    plant = {field: suggested(result, field) for field in (
        "entity_curve_current", "entity_offset_current", "entity_heat_limit", "entity_outdoor_temp",
    )}
    assert plant == {
        "entity_curve_current": "number.andere_kurve", "entity_offset_current": OFFSET,
        "entity_heat_limit": HEAT_LIMIT, "entity_outdoor_temp": "sensor.aussen",
    }
    assert suggested(result, "entity_energy_primary_heating", section="advanced") == "sensor.gas"
    result = await configure(hass, result, {**plant, "advanced": {"entity_energy_primary_heating": "sensor.gas"}})
    assert result["step_id"] == "notifications"
    assert suggested(result, "notify_services") == ["notify.mobile_app_pixel"]
    result = await configure(hass, result, {"notify_services": suggested(result, "notify_services")})
    calls = mock_addons(hass, monkeypatch, status="bereit")
    result = await finish_progress(hass, await configure(hass, result, {}))

    assert result["type"] == "create_entry"
    options = calls.options["heizungsbruecke"]
    assert options["entity_outdoor_temp"] == "sensor.aussen"
    assert options["entity_curve_current"] == "number.andere_kurve"
    assert options["entity_energy_primary_heating"] == "sensor.gas"
    assert options["notify_services"] == ["notify.mobile_app_pixel"]


async def test_timeout_offers_to_check_again(hass, monkeypatch):
    result, _, calls = await _to_setup(hass, monkeypatch, status=None)

    result = await finish_progress(hass, result)
    assert (result["type"], result["step_id"]) == ("menu", "setup_timeout")

    hass.states.async_set(status_entity_id(TENANT), "bereit", {"setup_id": calls.options["heizungsbruecke"]["setup_id"]})
    result = await configure(hass, result, {"next_step_id": "wait_status"})
    result = await finish_progress(hass, result)

    assert result["type"] == "create_entry"


async def test_setup_ignores_status_with_foreign_setup_id(hass, monkeypatch):
    """Review Focus 2."""
    result, _, _ = await _to_setup(hass, monkeypatch, status="bereit", status_setup_id="vom-letzten-lauf")

    result = await finish_progress(hass, result)

    assert result["step_id"] == "setup_timeout"


async def test_session_expired_during_provision_goes_back_to_login(hass, monkeypatch):
    result, mocks = await _reach(hass, monkeypatch, "summary")
    mock_addons(hass, monkeypatch)
    mocks.provision.side_effect = InvalidAuth("abgelaufen")

    result = await finish_progress(hass, await configure(hass, result, {}))

    assert (result["step_id"], result["errors"]) == ("user", {"base": "session_expired"})


async def test_provision_failure_is_a_setup_failure(hass, monkeypatch):
    result, mocks = await _reach(hass, monkeypatch, "summary")
    mock_addons(hass, monkeypatch)
    mocks.provision.side_effect = ApiError("500")

    result = await finish_progress(hass, await configure(hass, result, {}))

    assert result["step_id"] == "setup_failed"
    assert result["description_placeholders"]["grund"]


@pytest.mark.parametrize("profile_params", [None, "Heizkoerper", ["x"]])
async def test_invalid_provisioning_response_is_a_setup_failure(hass, monkeypatch, profile_params):
    result, mocks = await _reach(hass, monkeypatch, "summary")
    calls = mock_addons(hass, monkeypatch)
    mocks.provision.return_value = {**mocks.provision.return_value, "profile_params": profile_params}

    result = await finish_progress(hass, await configure(hass, result, {}))

    assert result["step_id"] == "setup_failed"
    assert calls.options == {}


async def test_supervisor_error_is_shown_without_secrets(hass, monkeypatch):
    error = AddonError(f"invalid option mqtt_password={MQTT_PASSWORD} token={CF_SECRET}")
    result, _, _ = await _to_setup(hass, monkeypatch, set_error=error)

    result = await finish_progress(hass, result)

    grund = result["description_placeholders"]["grund"]
    assert result["step_id"] == "setup_failed"
    assert MQTT_PASSWORD not in grund and CF_SECRET not in grund and "***" in grund


async def test_outdated_addon_found_again_at_setup(hass, monkeypatch):
    result, _ = await _reach(hass, monkeypatch, "summary")
    mock_addons(hass, monkeypatch)
    enable_supervisor(hass, monkeypatch, versions={"heizungsbruecke": "0.18.0"}).side_effect = (
        AddonOutdatedError("heizungsbruecke", "0.18.0", "0.19.0")
    )

    result = await finish_progress(hass, await configure(hass, result, {}))

    assert result["step_id"] == "setup_failed"
    assert "0.18.0" in result["description_placeholders"]["grund"]


async def test_cancel_after_a_failure(hass, monkeypatch):
    result, _, _ = await _to_setup(hass, monkeypatch, status="konfigurationsfehler", grund="x")
    result = await finish_progress(hass, result)

    result = await configure(hass, result, {"next_step_id": "cancel"})

    assert (result["type"], result["reason"]) == ("abort", "setup_cancelled")


# --- J: Eintraege und Texte ---

async def test_created_entry_blocks_a_second_flow_for_the_tenant(hass, monkeypatch):
    result, _, _ = await _to_setup(hass, monkeypatch)
    await finish_progress(hass, result)

    second = await login(hass, await start(hass))

    assert (second["type"], second["reason"]) == ("abort", "already_configured")


_COMPONENT = Path(__file__).parents[1] / "custom_components" / "smartheat"
_EXPECTED_ERRORS = {
    "invalid_auth", "cannot_connect", "no_tenants", "profile_combination_unsupported", "unit_mismatch",
    "entity_not_found", "entity_unavailable", "not_numeric", "out_of_range", "duplicate_entity",
    "room_sensors_required", "advanced_invalid", "warnings_not_confirmed", "session_expired", "unknown",
    "state_class_expected_measurement", "state_class_expected_total_increasing",
}
_EXPECTED_ABORTS = {
    "not_supervisor", "already_configured", "no_verified_profiles", "addon_missing", "addon_ambiguous",
    "addon_outdated", "supervisor_unavailable", "no_supported_integration", "no_heating_circuit", "setup_cancelled",
}


@pytest.mark.parametrize("path", ["strings.json", "translations/en.json", "translations/de.json"])
def test_every_error_and_abort_has_a_text(path):
    config = json.loads((_COMPONENT / path).read_text())["config"]

    assert _EXPECTED_ERRORS <= set(config["error"])
    assert _EXPECTED_ABORTS <= set(config["abort"])
    assert {"user", "tenant", "heating", "system", "rooms", "plant_values", "notifications", "summary",
            "setup_failed", "setup_timeout"} <= set(config["step"])
    assert "setup" in config["progress"]


def test_strings_json_equals_the_english_translation():
    assert json.loads((_COMPONENT / "strings.json").read_text()) == json.loads((_COMPONENT / "translations/en.json").read_text())


def test_translations_have_the_same_keys():
    def keys(node, prefix=""):
        if not isinstance(node, dict):
            return {prefix}
        return set().union(*(keys(value, f"{prefix}.{key}") for key, value in node.items()))

    english = json.loads((_COMPONENT / "translations/en.json").read_text())
    german = json.loads((_COMPONENT / "translations/de.json").read_text())

    assert keys(english) == keys(german)


async def test_hints_follow_the_ui_language(hass, monkeypatch):
    hass.config.language = "de"
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch)
    setup_mypyllant(hass)
    setup_rooms(hass)

    result = await login(hass, await start(hass))
    result = await configure(hass, result, SYSTEM_INPUT)
    result = await configure(hass, result, ROOMS_INPUT)

    assert "erkannt aus myVAILLANT" in result["description_placeholders"]["origins"]
