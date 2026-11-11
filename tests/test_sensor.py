"""Sensor-Felder je Hebel (Hebel aus entry.data["hebel"]): stabile Entity-Schluessel,
Werte aus hebel/gelernt."""
import json
from pathlib import Path

import pytest
from homeassistant.const import EntityCategory

from custom_components.smartheat import sensor
from custom_components.smartheat.const import KAELTESCHUTZ_VALUES, KREIS_ZUSTAND_VALUES, STATUS_EVENT_SCHEMA

VAILLANT = {"lever_set": "vaillant_vrc720", "hebel": ["curve", "room_setpoint", "heat_limit", "min_flow"],
            "shift_lever": "room_setpoint"}
VIESSMANN = {"lever_set": "viessmann_vicare", "hebel": ["curve", "level", "room_setpoint"], "shift_lever": "level"}


def _keys(entry_data) -> tuple[str, ...]:
    return tuple(event_field.key for event_field in sensor.kreis_fields(entry_data))


@pytest.mark.parametrize("lever, shift_lever, key", [
    ("curve", "room_setpoint", "heizkurve"), ("heat_limit", "room_setpoint", "heizgrenze"),
    ("min_flow", "room_setpoint", "mindestvorlauf"), ("level", "level", "niveau"),
    ("room_setpoint", "room_setpoint", "parallelverschiebung"), ("room_setpoint", "level", "raum_soll"),
])
def test_lever_entity_keys_are_stable(lever, shift_lever, key):
    assert sensor.lever_entity_key(lever, shift_lever) == key


def test_vaillant_entry_has_the_circuit_keys_in_their_order():
    """Die Schluessel des Kreises: erst die festen, dann Hebel in LEVER_OPTIONS-Reihenfolge, dann die Lernwerte."""
    assert _keys(VAILLANT) == (
        "boost", "abo", "zustand", "kaelteschutz", "raumtemperatur", "befunde",
        "heizkurve", "parallelverschiebung", "heizgrenze", "mindestvorlauf", "gelernte_steigung", "gelernte_heizgrenze",
    )
    assert [f.key for f in sensor.ANLAGE_FIELDS] == ["datenfehler", "letzte_serverantwort", "addon_version"]


def test_lever_and_learned_fields_are_diagnostics_that_start_disabled():
    fields = {f.key: f for f in sensor.kreis_fields(VAILLANT)}
    versteckt = {"heizkurve", "parallelverschiebung", "heizgrenze", "mindestvorlauf", "gelernte_steigung",
                 "gelernte_heizgrenze"}
    for key, event_field in fields.items():
        assert (event_field.enabled_default, event_field.category) == (
            (False, EntityCategory.DIAGNOSTIC) if key in versteckt else (True, event_field.category)), key
    assert {k for k, f in fields.items() if f.category is EntityCategory.DIAGNOSTIC} == versteckt | {"boost"}
    assert all(f.category is EntityCategory.DIAGNOSTIC and f.enabled_default for f in sensor.ANLAGE_FIELDS)


def test_lever_order_does_not_depend_on_the_order_in_the_entry():
    shuffled = {**VAILLANT, "hebel": ["min_flow", "heat_limit", "room_setpoint", "curve"]}
    assert _keys(shuffled) == _keys(VAILLANT)


def test_viessmann_entry_has_level_and_room_setpoint():
    keys = _keys(VIESSMANN)
    assert {"heizkurve", "niveau", "raum_soll"} <= set(keys)
    assert "heizgrenze" not in keys and "mindestvorlauf" not in keys


@pytest.mark.parametrize("data", [{}, {"hebel": []}, {"hebel": None}, {"hebel": "curve"}, {"hebel": ["unbekannt"]}])
def test_entry_without_known_levers_has_no_lever_or_learned_sensors(data):
    assert sensor.kreis_fields(data) == sensor.KREIS_FIELDS
    keys = _keys(data)
    assert "abo" in keys


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
    fields = {f.key: f for f in sensor.kreis_fields(VAILLANT)}
    assert fields[key].value(EVENT) == value


class _Coordinator:
    tenant_id = "client1"


@pytest.mark.parametrize("key", ["gelernte_steigung", "gelernte_heizgrenze"])
def test_learned_values_are_shown_with_two_decimals(key):
    """Server-Lernwerte kommen ungerundet; HA zeigt sie mit zwei Nachkommastellen (Dashboard „Zuhause“)."""
    fields = {f.key: f for f in sensor.kreis_fields(VAILLANT)}
    entity = sensor.EventSensor(_Coordinator(), fields[key], kreis=1)
    assert entity.suggested_display_precision == 2


def test_lever_values_keep_their_display_precision():
    fields = {f.key: f for f in sensor.kreis_fields(VAILLANT)}
    assert sensor.EventSensor(_Coordinator(), fields["heizkurve"], kreis=1).suggested_display_precision is None


def test_zustand_und_kaelteschutz_sind_enum_sensoren_mit_der_wertemenge_des_vertrags():
    fields = {f.key: f for f in sensor.kreis_fields({})}
    assert fields["zustand"].options == list(KREIS_ZUSTAND_VALUES)
    assert fields["kaelteschutz"].options == list(KAELTESCHUTZ_VALUES)
    assert fields["zustand"].attribute_keys == ("testphase_bis",)
    assert fields["zustand"].attributes({"testphase_bis": "2026-12-01"}) == {"testphase_bis": "2026-12-01"}


@pytest.mark.parametrize("path", ["strings.json", "translations/en.json", "translations/de.json"])
def test_zustand_und_kaelteschutz_haben_name_und_text_je_wert(path):
    paket = Path(sensor.__file__).parent
    texte = json.loads((paket / path).read_text(encoding="utf-8"))["entity"]["sensor"]
    for key, werte in (("zustand", KREIS_ZUSTAND_VALUES), ("kaelteschutz", KAELTESCHUTZ_VALUES)):
        assert texte[key]["name"]
        assert set(texte[key]["state"]) == set(werte)
        assert all(texte[key]["state"].values())
