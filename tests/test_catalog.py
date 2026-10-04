"""Katalog lesen (Spec TP6 2.1): Deskriptoren validieren, ungueltige verwerfen."""
import copy
import json
import logging
from pathlib import Path

import pytest

from custom_components.smartheat.catalog import (
    PollIntervalOption,
    RoleMatcher,
    parse_integrations,
    profile_lever_sets,
    profile_shift_lever,
    verified_profiles,
)

CATALOG = json.loads((Path(__file__).parent / "fixtures" / "catalog.json").read_text())


def _with_descriptor(**changes):
    catalog = copy.deepcopy(CATALOG)
    catalog["integrations"][0].update(changes)
    return catalog


def _mypyllant(catalog=CATALOG):
    return next(d for d in parse_integrations(catalog) if d.domain == "mypyllant")


def test_real_catalog_yields_the_mypyllant_descriptor():
    descriptor = _mypyllant()

    assert (descriptor.domain, descriptor.label, descriptor.hersteller) == ("mypyllant", "myVAILLANT", "Vaillant")
    # Katalog v3 (Plan 3c): Hebelnamen statt curve_current/shift_current; die Zonen-Climate ist room_setpoint.
    assert {
        "curve", "room_setpoint", "min_flow", "heat_limit", "flow_temperature", "flow_setpoint",
    } == set(descriptor.circuit_roles)
    assert "outdoor_temp" in descriptor.system_roles
    assert descriptor.system_roles["energy_thermal_heating"].original_name_suffix == "Heat Generated Heating"
    assert descriptor.erzeuger_typ_hints[0].model_contains == "aroTHERM"


def test_verified_profiles_only():
    assert [p["profile_id"] for p in verified_profiles(CATALOG)] == ["vaillant_gastherme_heizkoerper"]


def _broken_circuit_roles(role_json):
    roles = copy.deepcopy(CATALOG["integrations"][0]["circuit_scoped_roles"])
    roles["curve"] = role_json
    return roles


def _zone_role(role_json):
    roles = copy.deepcopy(CATALOG["integrations"][0]["circuit_scoped_roles"])
    roles["room_setpoint"] = role_json
    return roles


@pytest.mark.parametrize("catalog", [
    _with_descriptor(circuit_scoped_roles={
        k: v for k, v in CATALOG["integrations"][0]["circuit_scoped_roles"].items() if k != "heat_limit"
    }),
    _with_descriptor(circuit_scoped_roles=_broken_circuit_roles(
        {"entity_domain": "number", "unique_id_suffix": "_circuit_{circuit}_{circuit}"})),
    _with_descriptor(circuit_scoped_roles=_broken_circuit_roles({"entity_domain": "number", "unique_id_suffix": ""})),
    _with_descriptor(circuit_scoped_roles=_broken_circuit_roles({"entity_domain": "number", "unique_id_suffix": "_heating_curve"})),
    _with_descriptor(circuit_scoped_roles=_broken_circuit_roles(
        {"entity_domain": "number", "unique_id_suffix": "_c_{circuit}", "original_name_suffix": "x"})),
    _with_descriptor(circuit_scoped_roles=_broken_circuit_roles({"entity_domain": "number", "unique_id_suffix": "_{kreis}"})),
    _with_descriptor(system_roles={"outdoor_temp": {"entity_domain": "sensor", "unique_id_suffix": "_{circuit}_x"}}),
    _with_descriptor(label=None),
    _with_descriptor(erzeuger_typ_hints=[{"model_contains": 3}]),
    _with_descriptor(circuit_scoped_roles=_zone_role({"entity_domain": "climate", "unique_id_suffix": "_zone_{index}_climate"})),
    _with_descriptor(circuit_scoped_roles=_zone_role(
        {"entity_domain": "climate", "unique_id_suffix": "_zone_{circuit}_climate", "circuit_in_name": "(Circuit {circuit})"})),
    _with_descriptor(circuit_scoped_roles=_zone_role(
        {"entity_domain": "climate", "unique_id_suffix": "_zone_{index}_climate", "circuit_in_name": "(Circuit)"})),
    _with_descriptor(circuit_scoped_roles=_broken_circuit_roles(
        {"entity_domain": "number", "unique_id_suffix": "_x_{index}", "circuit_in_name": "(Circuit {circuit})"})),
    _with_descriptor(circuit_scoped_roles=_zone_role(
        {"entity_domain": "climate", "original_name_suffix": "Climate", "circuit_in_name": "(Circuit {circuit})"})),
    _with_descriptor(poll_interval_option={"key": "update_interval", "unit": "h", "default": 1800}),
    _with_descriptor(poll_interval_option={"key": "update_interval", "unit": "s", "default": 0}),
], ids=["missing-required", "two-placeholders", "empty", "circuit-without-placeholder", "both-kinds",
        "unknown-placeholder", "placeholder-in-system-role", "no-label", "bad-hint",
        "index-without-name", "name-with-circuit-uid", "name-without-placeholder", "name-on-required-role",
        "name-on-name-search", "bad-poll-unit", "bad-poll-default"])
def test_invalid_descriptor_is_dropped_and_logged(catalog, caplog):
    with caplog.at_level(logging.WARNING):
        # mypyllant (Index 0) faellt weg, die beiden anderen Deskriptoren bleiben.
        assert [d.domain for d in parse_integrations(catalog)] == ["weishaupt_modbus", "vicare"]

    assert "mypyllant" in caplog.text


def test_unknown_fields_are_ignored():
    catalog = _with_descriptor(zukunft={"x": 1})
    catalog["integrations"][0]["system_roles"]["outdoor_temp"]["neu"] = True

    assert len(parse_integrations(catalog)) == 3


def test_one_bad_descriptor_does_not_drop_the_others():
    catalog = copy.deepcopy(CATALOG)
    catalog["integrations"].append({"domain": "kaputt"})

    assert [d.domain for d in parse_integrations(catalog)] == ["mypyllant", "weishaupt_modbus", "vicare"]


def test_uid_pattern_masks_regex_characters():
    pattern = RoleMatcher("number", "_a.b_{circuit}_x+").uid_pattern()

    assert pattern.search("dev S_a.b_12_x+").group(1) == "12"
    assert pattern.search("dev S_aXb_12_x+") is None
    assert pattern.search("dev S_a.b_12_x+_setpoint") is None


def test_zone_matcher_carries_the_circuit_in_its_name():
    descriptor = _mypyllant()
    zone = descriptor.circuit_roles["room_setpoint"]

    assert (zone.unique_id_suffix, zone.circuit_in_name) == ("_zone_{index}_climate", "(Circuit {circuit})")
    assert zone.name_circuit("Zuhause Zone 1 (Circuit 0) Climate") == "0"
    assert zone.name_circuit("zuhause zone 1 (CIRCUIT 3) climate") == "3"
    assert zone.name_circuit("Zuhause Zone 1 Climate") is None
    assert zone.name_circuit(None) is None


def test_circuit_in_name_compares_whole_numbers():
    """Review Focus 3: "(Circuit 10)" ist Kreis 10, nicht 1."""
    zone = RoleMatcher("climate", "_zone_{index}_climate", circuit_in_name="(Circuit {circuit})")

    assert zone.name_circuit("Haus Zone 1 (Circuit 10) Climate") == "10"
    assert zone.uid_pattern().search("mypyllant_S_zone_12_climate") is not None


def test_circuit_in_name_uses_the_last_match():
    """mypyllant haengt " (Circuit N)" hinter den frei waehlbaren Zonennamen: ein Zonenname, der
    selbst "(Circuit 1)" enthaelt, darf nicht gewinnen."""
    zone = RoleMatcher("climate", "_zone_{index}_climate", circuit_in_name="(Circuit {circuit})")

    assert zone.name_circuit("Bad (Circuit 1) Zone (Circuit 0) Climate") == "0"


def test_poll_interval_option_is_parsed_and_converted():
    descriptor = _mypyllant()
    option = descriptor.poll_interval_option

    assert (option.key, option.unit, option.default) == ("update_interval", "s", 1800)
    assert option.seconds({}) == 1800
    assert option.seconds({"update_interval": 3600}) == 3600
    assert PollIntervalOption("interval", "min", 30).seconds({"interval": 45}) == 2700
    assert option.seconds({"update_interval": "3600"}) is None
    assert option.seconds({"update_interval": True}) is None


def test_descriptor_without_poll_interval_option_is_valid():
    catalog = copy.deepcopy(CATALOG)
    del catalog["integrations"][0]["poll_interval_option"]

    descriptor = _mypyllant(catalog)
    assert descriptor.poll_interval_option is None


def test_v3_descriptors_parse_with_defining_roles_and_hint():
    descriptors = {d.domain: d for d in parse_integrations(CATALOG)}
    assert set(descriptors) == {"mypyllant", "weishaupt_modbus", "vicare"}
    assert descriptors["mypyllant"].circuit_defining == ("curve", "min_flow", "heat_limit")
    assert descriptors["weishaupt_modbus"].circuit_defining == ("room_setpoint", "mode_select")
    assert descriptors["mypyllant"].hinweis is None
    assert "Modbus TCP" in descriptors["weishaupt_modbus"].hinweis


def test_circuit_opt_matches_with_and_without_number():
    matcher = RoleMatcher("number", unique_id_suffix="Heizkennlinie{circuit_opt}")
    assert matcher.uid_pattern().search("weishaupt_wbbHeizkennlinie").group(1) == ""
    assert matcher.uid_pattern().search("weishaupt_wbbHeizkennlinie2").group(1) == "2"


@pytest.mark.parametrize("change, reason", [
    ({"circuit_defining_roles": []}, "kreisbildend"),
    ({"circuit_defining_roles": ["level"]}, "kreisbildend"),
    ({"hinweis": ""}, "hinweis"),
    ({"circuit_scoped_roles": {"room_setpoint": {"entity_domain": "number", "unique_id_suffix": "X{circuit_opt}Y"},
                               "mode_select": {"entity_domain": "select", "unique_id_suffix": "B{circuit_opt}"}}}, "Ende"),
])
def test_invalid_v3_descriptor_is_dropped(caplog, change, reason):
    raw = {**next(d for d in CATALOG["integrations"] if d["domain"] == "weishaupt_modbus"), **change}
    assert parse_integrations({"integrations": [raw]}) == []
    assert reason in caplog.text


def test_profile_lever_sets_and_shift_lever():
    by_id = {p["profile_id"]: p for p in CATALOG["profiles"]}
    assert [ls["id"] for ls in profile_lever_sets(by_id["weishaupt_waermepumpe_heizkoerper"])] == [
        "weishaupt_wwp", "weishaupt_wwp_basis",
    ]
    assert profile_shift_lever(by_id["viessmann_gastherme_heizkoerper"]) == "level"
    broken = {**by_id["vaillant_gastherme_heizkoerper"], "lever_sets": [{"id": 1}, "x"]}
    assert profile_lever_sets(broken) == []


def test_verified_profiles_need_model_and_lever_sets():
    vaillant = next(p for p in CATALOG["profiles"] if p["profile_id"] == "vaillant_gastherme_heizkoerper")
    assert verified_profiles({"profiles": [vaillant]}) == [vaillant]
    assert verified_profiles({"profiles": [{**vaillant, "model": None}]}) == []
    assert verified_profiles({"profiles": [{**vaillant, "lever_sets": []}]}) == []
