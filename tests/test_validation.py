"""Harte Pruefungen und Warnungen des Wizards (Spec TP6 4)."""
from datetime import timedelta

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartheat import validation
from custom_components.smartheat.validation import (
    ERROR_DUPLICATE,
    ERROR_NOT_FOUND,
    ERROR_NOT_NUMERIC,
    ERROR_RANGE,
    ERROR_UNAVAILABLE,
    ERROR_UNIT,
    check_numeric,
    check_rooms,
    check_temperature,
    deviating_room_sensors,
    duplicate_fields,
    read_value,
    room_sensor_ref,
    room_target_ref,
    stale_entities,
)

ROOM = (5.0, 35.0)


def test_refs_for_climate_entities():
    assert room_sensor_ref("climate.wz") == "climate.wz::current_temperature"
    assert room_target_ref("climate.wz") == "climate.wz::temperature"
    assert room_sensor_ref("sensor.wz") == "sensor.wz"
    assert room_target_ref("sensor.soll") == "sensor.soll"


async def test_read_value_errors(hass):
    hass.states.async_set("sensor.tot", "unavailable")
    hass.states.async_set("sensor.leer", "unknown")
    hass.states.async_set("sensor.text", "warm")
    hass.states.async_set("climate.ohne", "heat", {})

    assert read_value(hass, "sensor.gibtsnicht") == (None, ERROR_NOT_FOUND)
    assert read_value(hass, "sensor.tot") == (None, ERROR_UNAVAILABLE)
    assert read_value(hass, "sensor.leer") == (None, ERROR_UNAVAILABLE)
    assert read_value(hass, "sensor.text") == (None, ERROR_NOT_NUMERIC)
    assert read_value(hass, "climate.ohne::current_temperature") == (None, ERROR_NOT_NUMERIC)


async def test_check_temperature_units_and_ranges(hass):
    hass.states.async_set("sensor.c", "21.5", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.f", "70", {"unit_of_measurement": "°F"})
    hass.states.async_set("sensor.kalt", "4.9", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.grenze", "35", {"unit_of_measurement": "°C"})
    hass.states.async_set("climate.wz", "heat", {"current_temperature": 20.5, "temperature": 21})
    hass.states.async_set("weather.home", "sunny", {"temperature": 3.2, "temperature_unit": "°C"})
    hass.states.async_set("weather.us", "sunny", {"temperature": 38, "temperature_unit": "°F"})

    assert check_temperature(hass, "sensor.c", ROOM) is None
    assert check_temperature(hass, "sensor.f", ROOM) == ERROR_UNIT
    assert check_temperature(hass, "sensor.kalt", ROOM) == ERROR_RANGE
    assert check_temperature(hass, "sensor.grenze", ROOM) is None
    assert check_temperature(hass, "climate.wz::current_temperature", ROOM) is None
    assert check_temperature(hass, "weather.home", (-40.0, 45.0)) is None
    assert check_temperature(hass, "weather.us", (-40.0, 45.0)) == ERROR_UNIT
    assert check_temperature(hass, "sensor.kalt") is None  # ohne Bereich nur Einheit


async def test_check_numeric_ignores_units(hass):
    hass.states.async_set("number.kurve", "1.2", {})

    assert check_numeric(hass, "number.kurve") is None
    assert check_numeric(hass, "number.fehlt") == ERROR_NOT_FOUND


def test_duplicate_fields_compares_resolved_refs():
    errors = duplicate_fields({
        "room_sensors": ["sensor.wz", "climate.kz::current_temperature"],
        "room_target": ["climate.kz::temperature"],
        "entity_outdoor_temp": ["sensor.wz"],
    })

    assert errors == {"room_sensors": ERROR_DUPLICATE, "entity_outdoor_temp": ERROR_DUPLICATE}


def test_duplicate_within_one_field():
    assert duplicate_fields({"room_sensors": ["sensor.a", "sensor.a"]}) == {"room_sensors": ERROR_DUPLICATE}


async def test_stale_entities_after_six_hours(hass):
    hass.states.async_set("sensor.a", "20", {"unit_of_measurement": "°C"})
    hass.states.async_set("climate.b", "heat", {"current_temperature": 20})
    now = dt_util.utcnow()

    assert stale_entities(hass, ["sensor.a", "climate.b::current_temperature"], now + timedelta(hours=5)) == []
    assert stale_entities(
        hass, ["sensor.a", "climate.b::current_temperature", "climate.b::temperature", "sensor.fehlt"],
        now + timedelta(hours=7),
    ) == ["sensor.a", "climate.b"]


@pytest.mark.parametrize("values,expected", [
    ({"sensor.a": 20.0, "sensor.b": 20.5, "sensor.c": 25.0}, ["sensor.c"]),
    ({"sensor.a": 20.0, "climate.b::current_temperature": 23.5}, ["sensor.a", "climate.b"]),
    ({"sensor.a": 20.0, "sensor.b": 23.0}, []),
    ({"sensor.a": 20.0}, []),
])
def test_deviating_room_sensors(values, expected):
    assert deviating_room_sensors(values) == expected


async def test_check_rooms_returns_refs_and_field_errors(hass):
    hass.states.async_set("sensor.wz", "21.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("climate.wz", "heat", {"current_temperature": 21.2, "temperature": 21.5})

    assert check_rooms(hass, ["sensor.wz", "climate.wz"], "climate.wz") == (
        ["sensor.wz", "climate.wz::current_temperature"], "climate.wz::temperature", {},
    )
    assert check_rooms(hass, [], "climate.wz")[2] == {"room_sensors": "room_sensors_required"}
    assert check_rooms(hass, ["sensor.wz"], "sensor.wz")[2] == {
        "room_sensors": "duplicate_entity", "entity_room_target": "duplicate_entity",
    }


def test_entity_selector_adds_the_integration_to_every_filter():
    config = validation.entity_selector("entity_heat_limit", integration="mypyllant", domains=["number"]).config
    assert [entry["integration"] for entry in config["filter"]] == ["mypyllant"]


async def test_plant_field_checks_by_kind(hass):
    hass.states.async_set("number.kennlinie", "0.75")
    hass.states.async_set("number.normal", "20.5", {"unit_of_measurement": "°C"})
    hass.states.async_set("select.betriebsart", "hz_operationmode_normal")
    hass.states.async_set("select.weg", "unavailable")
    assert validation.check_plant_field(hass, "entity_curve_current", "number.kennlinie", "weishaupt_wwp") is None
    assert validation.check_plant_field(hass, "entity_shift_current", "number.normal", "weishaupt_wwp") is None
    assert validation.check_plant_field(hass, "entity_mode_select", "select.betriebsart", "weishaupt_wwp") is None
    assert validation.check_plant_field(hass, "entity_mode_select", "select.weg", "weishaupt_wwp") == validation.ERROR_UNAVAILABLE
    assert validation.check_plant_field(hass, "entity_mode_select", "select.fehlt", "weishaupt_wwp") == validation.ERROR_NOT_FOUND
    # Domäne je Hebelsatz: eine number als Zone ist bei Vaillant falsch, bei Weishaupt richtig
    assert validation.check_plant_field(hass, "entity_shift_current", "number.normal", "vaillant_vrc720") == validation.ERROR_DOMAIN


def test_entity_selector_uses_lever_set_domains():
    # HA normalisiert `domain` im Selektor-Schema zur Liste.
    selector = validation.entity_selector("entity_mode_select", domains=["select"])
    assert selector.config["filter"] == [{"domain": ["select"]}]
    selector = validation.entity_selector("entity_shift_current", domains=["number"])
    assert selector.config["filter"] == [{"domain": ["number"]}]


async def test_room_target_from_the_device_of_a_write_target_is_a_mirror(hass):
    # Audit 4, A4-12 (IT-1): Zonen-Sollwert-Sensor und beschriebene Zone gehoeren zum selben Geraet
    entry = MockConfigEntry(domain="mypyllant")
    entry.add_to_hass(hass)
    zone = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={("mypyllant", "z1")})
    room = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={("mypyllant", "r1")})
    registry = er.async_get(hass)
    registry.async_get_or_create("climate", "mypyllant", "zone1", device_id=zone.id, suggested_object_id="zone")
    registry.async_get_or_create("sensor", "mypyllant", "zone1_desired", device_id=zone.id,
                                 suggested_object_id="zone_desired")
    registry.async_get_or_create("climate", "mypyllant", "room1", device_id=room.id, suggested_object_id="raum")

    assert validation.room_target_mirrors_plant(hass, "sensor.zone_desired", ["climate.zone"]) is True
    assert validation.room_target_mirrors_plant(hass, "climate.zone::temperature", ["climate.zone"]) is True
    # Review Focus 4: eigenes Raumthermostat (anderes Geraet, gleiche Integration) bleibt zulaessig
    assert validation.room_target_mirrors_plant(hass, "climate.raum::temperature", ["climate.zone"]) is False
    assert validation.room_target_mirrors_plant(hass, "sensor.ohne_registry", ["climate.zone"]) is False
