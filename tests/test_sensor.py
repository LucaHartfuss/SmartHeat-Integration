"""Sensor-Felder je Hebelsatz (Plan 3c Task 12): stabile Entity-Schluessel, Werte aus hebel/gelernt."""
import pytest

from custom_components.smartheat import sensor

VAILLANT = {"lever_set": "vaillant_vrc720", "shift_lever": "room_setpoint"}


@pytest.mark.parametrize("lever, shift_lever, key", [
    ("curve", "room_setpoint", "heizkurve"), ("heat_limit", "room_setpoint", "heizgrenze"),
    ("min_flow", "room_setpoint", "mindestvorlauf"), ("level", "level", "niveau"),
    ("room_setpoint", "room_setpoint", "parallelverschiebung"), ("room_setpoint", "level", "raum_soll"),
])
def test_lever_entity_keys_are_stable(lever, shift_lever, key):
    assert sensor.lever_entity_key(lever, shift_lever) == key


def test_vaillant_entry_keeps_the_client1_entity_keys():
    keys = sensor.entity_keys(VAILLANT)
    assert {"heizkurve", "parallelverschiebung", "mindestvorlauf", "heizgrenze",
            "gelernte_steigung", "gelernte_heizgrenze"} <= set(keys)
    assert "niveau" not in keys and "raum_soll" not in keys


def test_viessmann_entry_has_level_and_room_setpoint():
    keys = sensor.entity_keys({"lever_set": "viessmann_vicare", "shift_lever": "level"})
    assert {"heizkurve", "niveau", "raum_soll"} <= set(keys)
    assert "heizgrenze" not in keys and "mindestvorlauf" not in keys


def test_incomplete_entry_has_no_lever_or_learned_sensors():
    keys = sensor.entity_keys({})
    assert not {"heizkurve", "gelernte_steigung"} & set(keys)
    assert "status" in keys and "abo" in keys


def test_unknown_lever_set_has_no_lever_sensors():
    assert sensor.entity_fields({"lever_set": "gibt_es_nicht"}) == sensor.FIELDS


EVENT = {
    "schema": 2, "tenant_id": "t1", "status": "regelt", "hebelsatz": "vaillant_vrc720",
    "hebel": {"curve": 1.05, "room_setpoint": 21.0, "heat_limit": 16.0, "min_flow": 20.5},
    "gelernt": {"curve": 1.05, "heat_limit": 16.2},
}


@pytest.mark.parametrize("key, value", [
    ("heizkurve", 1.05), ("parallelverschiebung", 21.0), ("mindestvorlauf", 20.5), ("heizgrenze", 16.0),
    ("gelernte_steigung", 1.05), ("gelernte_heizgrenze", 16.2),
])
def test_values_come_from_hebel_and_gelernt(key, value):
    fields = {f.key: f for f in sensor.entity_fields(VAILLANT)}
    assert fields[key].value(EVENT) == value
