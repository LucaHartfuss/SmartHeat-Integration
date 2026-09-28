"""Erkennungs-Engine (Spec TP6 2.2, Tests laut Spec 6)."""
import json
from pathlib import Path

import pytest

from custom_components.smartheat.catalog import parse_integrations
from custom_components.smartheat.detection import (
    DeviceInfo,
    RegistryEntry,
    WeatherCandidate,
    battery_entities,
    find_circuits,
    installed_integrations,
    mobile_app_services,
    suggest_erzeuger_typ,
    system_role_suggestions,
    weather_fallback,
)

FIXTURES = Path(__file__).parent / "fixtures"
[MYPYLLANT] = parse_integrations(json.loads((FIXTURES / "catalog.json").read_text()))

# Wie im Server-Test tests/generic/test_catalog_registry_fixture.py (gleiche Suchregeln).
EXPECTED = {
    "curve_current": "number.zuhause_circuit_0_heating_curve",
    "offset_current": "number.zuhause_circuit_0_min_flow_temperature_setpoint",
    "heat_limit": "number.zuhause_circuit_0_heat_demand_limited_by_outside_temperature",
    "flow_temperature": "sensor.zuhause_circuit_0_current_flow_temperature",
    "outdoor_temp": "sensor.zuhause_outdoor_temperature",
    "system_water_pressure": "sensor.zuhause_system_water_pressure",
    "energy_electrical_heating": "sensor.zuhause_device_0_ecotec_plus_consumed_electrical_energy_heating",
    "energy_electrical_dhw":
        "sensor.zuhause_device_0_ecotec_plus_consumed_electrical_energy_domestic_hot_water",
    "energy_primary_heating": "sensor.zuhause_device_0_ecotec_plus_consumed_primary_energy_heating",
    "energy_primary_dhw": "sensor.zuhause_device_0_ecotec_plus_consumed_primary_energy_domestic_hot_water",
    "energy_thermal_heating": "sensor.zuhause_device_0_ecotec_plus_heat_generated_heating",
    "energy_thermal_dhw": "sensor.zuhause_device_0_ecotec_plus_heat_generated_domestic_hot_water",
}


def _fixture_entries(config_entry_id="entry1"):
    return [
        RegistryEntry(e["entity_id"], e["unique_id"], e["platform"], e["original_name"], config_entry_id, None, None)
        for e in json.loads((FIXTURES / "client1_mypyllant_registry.json").read_text())
    ]


def _entry(entity_id, unique_id, *, platform="mypyllant", name=None, entry="e1", device=None, device_class=None):
    return RegistryEntry(entity_id, unique_id, platform, name, entry, device, device_class)


def _circuit_entries(system="S", circuit="0", entry="e1", device=None, prefix="a"):
    return [
        _entry(f"number.{prefix}_curve_{circuit}", f"mypyllant {system}_circuit_{circuit}_heating_curve", entry=entry, device=device),
        _entry(f"number.{prefix}_offset_{circuit}", f"mypyllant {system}_circuit_{circuit}_min_flow_temperature_setpoint", entry=entry),
        _entry(f"number.{prefix}_limit_{circuit}",
               f"mypyllant {system}_circuit_{circuit}_heat_demand_limited_by_outside_temperature", entry=entry),
    ]


def test_client1_registry_yields_one_circuit_and_every_expected_role():
    entries = _fixture_entries()

    [circuit] = find_circuits(MYPYLLANT, entries, [])
    suggestions = {**circuit.roles, **system_role_suggestions(MYPYLLANT, circuit, entries)}

    assert (circuit.config_entry_id, circuit.system_key, circuit.circuit) == ("entry1", "SYSTEM", "0")
    assert circuit.label == "Heizkreis 0"
    assert circuit.key == "entry1|SYSTEM|0"
    assert suggestions == EXPECTED


def test_two_circuits_in_one_system():
    entries = _circuit_entries(circuit="0") + _circuit_entries(circuit="1")

    circuits = find_circuits(MYPYLLANT, entries, [])

    assert [(c.circuit, c.label) for c in circuits] == [("0", "Heizkreis 0"), ("1", "Heizkreis 1")]


def test_two_systems_in_one_account_are_separated_by_system_key():
    entries = (
        _circuit_entries(system="A", prefix="a") + _circuit_entries(system="B", prefix="b")
        + [_entry("sensor.a_out", "mypyllant_A_home_outdoor_temperature"),
           _entry("sensor.b_out", "mypyllant_B_home_outdoor_temperature")]
    )

    circuits = find_circuits(MYPYLLANT, entries, [])

    assert [c.system_key for c in circuits] == ["A", "B"]
    assert circuits[0].label != circuits[1].label
    assert system_role_suggestions(MYPYLLANT, circuits[1], entries)["outdoor_temp"] == "sensor.b_out"


def test_circuit_with_missing_required_role_is_not_offered():
    assert find_circuits(MYPYLLANT, _circuit_entries()[:2], []) == []


def test_circuit_with_ambiguous_required_role_is_not_offered():
    entries = _circuit_entries() + [_entry("number.dup", "mypyllant S_circuit_0_heating_curve", entry="e1")]
    # Gleicher Config-Entry, gleiche Kennung, gleicher Kreis: zwei Treffer fuer curve_current.
    assert find_circuits(MYPYLLANT, entries, []) == []


def test_other_platforms_and_domains_are_ignored():
    entries = _circuit_entries() + [
        _entry("sensor.a_curve_0", "mypyllant_S_circuit_0_heating_curve"),
        _entry("number.other", "other S_circuit_0_heating_curve", platform="other"),
    ]

    [circuit] = find_circuits(MYPYLLANT, entries, [])

    assert circuit.roles["curve_current"] == "number.a_curve_0"


def test_device_name_labels_the_circuit():
    entries = _circuit_entries(device="dev1")

    [circuit] = find_circuits(MYPYLLANT, entries, [DeviceInfo("dev1", "Zuhause Circuit 0", None, ("e1",))])

    assert circuit.label == "Zuhause Circuit 0"


def test_circuits_on_one_device_get_their_number_appended():
    entries = _circuit_entries(circuit="0", device="dev1") + _circuit_entries(circuit="1", device="dev1")

    circuits = find_circuits(MYPYLLANT, entries, [DeviceInfo("dev1", "Zuhause", None, ("e1",))])

    assert [c.label for c in circuits] == ["Zuhause · Kreis 0", "Zuhause · Kreis 1"]


def test_system_role_with_several_hits_is_not_suggested():
    entries = _circuit_entries() + [
        _entry("sensor.e1", "mypyllant_S_D1_0", name="Gerät 1 Heat Generated Heating"),
        _entry("sensor.e2", "mypyllant_S_D2_0", name="Gerät 2 Heat Generated Heating"),
    ]
    [circuit] = find_circuits(MYPYLLANT, entries, [])

    assert "energy_thermal_heating" not in system_role_suggestions(MYPYLLANT, circuit, entries)


def test_system_roles_only_from_the_circuit_config_entry():
    entries = _circuit_entries() + [_entry("sensor.fremd", "mypyllant_S_home_outdoor_temperature", entry="e2")]
    [circuit] = find_circuits(MYPYLLANT, entries, [])

    assert "outdoor_temp" not in system_role_suggestions(MYPYLLANT, circuit, entries)


@pytest.mark.parametrize("models,expected", [
    (["ecoTEC plus VC 206"], "Gastherme"),
    (["aroTHERM plus", "VRC 720"], "Waermepumpe"),
    (["aroTHERM plus", "ecoTEC plus"], None),
    (["VRC 720"], None),
    ([None], None),
])
def test_erzeuger_typ_from_device_models(models, expected):
    devices = [DeviceInfo(f"d{i}", None, model, ("e1",)) for i, model in enumerate(models)]
    devices.append(DeviceInfo("fremd", None, "flexoTHERM", ("e9",)))

    assert suggest_erzeuger_typ(MYPYLLANT, {"e1"}, devices) == expected


def test_weather_fallback_prefers_forecast_home_then_alphabetical_and_needs_celsius():
    assert weather_fallback([
        WeatherCandidate("weather.b", 3.0, "°C"), WeatherCandidate("weather.forecast_home", 2.0, "°C"),
    ]) == "weather.forecast_home"
    assert weather_fallback([WeatherCandidate("weather.b", 3.0, "°C"), WeatherCandidate("weather.a", 1.0, "°C")]) == "weather.a"
    assert weather_fallback([WeatherCandidate("weather.forecast_home", 2.0, "°F")]) is None
    assert weather_fallback([WeatherCandidate("weather.x", "kalt", "°C"), WeatherCandidate("weather.y", True, "°C")]) is None
    assert weather_fallback([]) is None


def test_batteries_prefer_percent_sensor_per_device():
    entries = [
        _entry("sensor.wz_temp", "u1", platform="zha", device="d1"),
        _entry("sensor.wz_battery", "u2", platform="zha", device="d1", device_class="battery"),
        _entry("binary_sensor.wz_battery_low", "u3", platform="zha", device="d1", device_class="battery"),
        _entry("climate.kz", "u4", platform="zha", device="d2"),
        _entry("binary_sensor.kz_battery_low", "u5", platform="zha", device="d2", device_class="battery"),
        _entry("sensor.bad", "u6", platform="zha", device="d3"),
        _entry("sensor.bad_battery_mv", "u7", platform="zha", device="d3", device_class="battery"),
        _entry("sensor.no_device", "u8", platform="zha"),
    ]
    units = {"sensor.wz_battery": "%", "sensor.bad_battery_mv": "mV"}

    result = battery_entities(["sensor.wz_temp", "climate.kz", "sensor.bad", "sensor.no_device", "sensor.wz_temp"], entries, units)

    assert result == ["sensor.wz_battery", "binary_sensor.kz_battery_low"]


def test_mobile_app_services():
    assert mobile_app_services(["mobile_app_pixel", "notify", "persistent_notification", "mobile_app_iphone"]) == [
        "notify.mobile_app_iphone", "notify.mobile_app_pixel",
    ]


def test_installed_integrations_needs_a_config_entry():
    assert installed_integrations([MYPYLLANT], {"mypyllant", "met"}) == [MYPYLLANT]
    assert installed_integrations([MYPYLLANT], {"met"}) == []
