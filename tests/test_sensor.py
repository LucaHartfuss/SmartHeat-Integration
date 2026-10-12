"""Sensor-Felder je Hebel (Plan 3c Task 12; Spec 5c 8: Hebel aus entry.data["hebel"]): stabile Entity-Schluessel,
Werte aus hebel/gelernt."""
import pytest

from custom_components.smartheat import sensor
from custom_components.smartheat.const import STATUS_EVENT_SCHEMA

VAILLANT = {"lever_set": "vaillant_vrc720", "hebel": ["curve", "room_setpoint", "heat_limit", "min_flow"],
            "shift_lever": "room_setpoint"}
VIESSMANN = {"lever_set": "viessmann_vicare", "hebel": ["curve", "level", "room_setpoint"], "shift_lever": "level"}


@pytest.mark.parametrize("lever, shift_lever, key", [
    ("curve", "room_setpoint", "heizkurve"), ("heat_limit", "room_setpoint", "heizgrenze"),
    ("min_flow", "room_setpoint", "mindestvorlauf"), ("level", "level", "niveau"),
    ("room_setpoint", "room_setpoint", "parallelverschiebung"), ("room_setpoint", "level", "raum_soll"),
])
def test_lever_entity_keys_are_stable(lever, shift_lever, key):
    assert sensor.lever_entity_key(lever, shift_lever) == key


def test_vaillant_entry_keeps_the_client1_entity_keys_in_their_order():
    """Gleiche Schluessel und Reihenfolge wie 0.16.1 fuer client1 (Hebel in LEVER_OPTIONS-Reihenfolge)."""
    assert sensor.entity_keys(VAILLANT) == (
        "status", "datenfehler", "boost", "letzte_serverantwort", "abo", "addon_version",
        "heizkurve", "parallelverschiebung", "heizgrenze", "mindestvorlauf", "gelernte_steigung", "gelernte_heizgrenze",
    )


def test_lever_order_does_not_depend_on_the_order_in_the_entry():
    shuffled = {**VAILLANT, "hebel": ["min_flow", "heat_limit", "room_setpoint", "curve"]}
    assert sensor.entity_keys(shuffled) == sensor.entity_keys(VAILLANT)


def test_viessmann_entry_has_level_and_room_setpoint():
    keys = sensor.entity_keys(VIESSMANN)
    assert {"heizkurve", "niveau", "raum_soll"} <= set(keys)
    assert "heizgrenze" not in keys and "mindestvorlauf" not in keys


@pytest.mark.parametrize("data", [{}, {"hebel": []}, {"hebel": None}, {"hebel": "curve"}, {"hebel": ["unbekannt"]}])
def test_entry_without_known_levers_has_no_lever_or_learned_sensors(data):
    assert sensor.entity_fields(data) == sensor.FIELDS
    keys = sensor.entity_keys(data)
    assert "status" in keys and "abo" in keys


EVENT = {
    "schema": STATUS_EVENT_SCHEMA, "tenant_id": "t1", "status": "regelt", "hebelsatz": "vaillant_vrc720",
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


class _Coordinator:
    tenant_id = "client1"


@pytest.mark.parametrize("key", ["gelernte_steigung", "gelernte_heizgrenze"])
def test_learned_values_are_shown_with_two_decimals(key):
    """Server-Lernwerte kommen ungerundet; HA zeigt sie mit zwei Nachkommastellen (Dashboard „Zuhause“)."""
    fields = {f.key: f for f in sensor.entity_fields(VAILLANT)}
    entity = sensor.EventSensor(_Coordinator(), fields[key])
    assert entity.suggested_display_precision == 2


def test_lever_values_keep_their_display_precision():
    fields = {f.key: f for f in sensor.entity_fields(VAILLANT)}
    assert sensor.EventSensor(_Coordinator(), fields["heizkurve"]).suggested_display_precision is None
