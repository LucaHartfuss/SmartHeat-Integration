from custom_components.smartheat.const import (
    PLAUSIBLE_RANGES,
    STATUS_ATTR_GRUND,
    STATUS_ATTR_SETUP_ID,
    status_entity_id,
)


def test_status_entity_id_matches_the_addon_rule():
    assert status_entity_id("client1") == "sensor.smartheat_client1_status"
    assert status_entity_id("Smoke-Test 01") == "sensor.smartheat_smoke_test_01_status"


def test_plausible_ranges_are_the_r4_values():
    assert PLAUSIBLE_RANGES == {"room": (5.0, 35.0), "outdoor": (-40.0, 45.0), "heat_limit": (5.0, 25.0)}


def test_status_attribute_names_match_the_addon():
    assert (STATUS_ATTR_SETUP_ID, STATUS_ATTR_GRUND) == ("setup_id", "grund")
