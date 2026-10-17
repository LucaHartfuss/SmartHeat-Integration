"""Spec 5c 2.2: Wunschtemperatur als number; Setzen feuert nur das lokale Ereignis smartheat_wunsch."""
from unittest.mock import patch

import pytest
from homeassistant.core import Context, State
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache

from custom_components.smartheat.const import DOMAIN, STATUS_EVENT, WUNSCH_EVENT

DATA = {"geraet_id": "shg-a", "tenant_id": "zuhause2", "profile_id": "vaillant_gastherme_heizkoerper",
        "lever_set": "vaillant_vrc720", "hebel": ["curve", "room_setpoint", "heat_limit", "min_flow"],
        "shift_lever": "room_setpoint"}
WUNSCH = "number.smartheat_zuhause2_wunschtemperatur"


async def _setup(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="shg-a", version=3)
    entry.add_to_hass(hass)
    with patch("custom_components.smartheat.coordinator.async_track_time_interval"):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _listen(hass) -> list:
    events = []
    hass.bus.async_listen(WUNSCH_EVENT, lambda event: events.append(event))
    return events


def _event(**overrides) -> dict:
    return {"schema": 3, "geraet_id": "shg-a", "tenant_id": "zuhause2", "status": "regelt", "raum_soll": 21.5,
            "geraet_hinweise": [], **overrides}


async def test_entity_follows_the_effective_target(hass):
    await _setup(hass)
    hass.bus.async_fire(STATUS_EVENT, _event())
    await hass.async_block_till_done()
    state = hass.states.get(WUNSCH)
    assert float(state.state) == 21.5
    assert (state.attributes["min"], state.attributes["max"], state.attributes["step"]) == (15.0, 25.0, 0.5)
    assert state.attributes["unit_of_measurement"] == "°C"


async def test_setting_fires_only_the_local_event(hass, aioclient_mock, hass_admin_user):
    """Der Kontext des Dienstaufrufs (hier ein Nutzer; HA verlangt einen existierenden) geht mit dem Ereignis mit."""
    await _setup(hass)
    events = _listen(hass)
    context = Context(user_id=hass_admin_user.id)
    await hass.services.async_call("number", "set_value", {
        "entity_id": WUNSCH, "value": 22.5}, blocking=True, context=context)
    await hass.async_block_till_done()
    assert [e.data for e in events] == [{"raum_soll": 22.5}]
    assert (events[0].context.id, events[0].context.user_id) == (context.id, hass_admin_user.id)
    assert aioclient_mock.call_count == 0
    # Bis zum naechsten Status-Ereignis zeigt die Entity den gesetzten Wert, danach den wirksamen.
    assert float(hass.states.get(WUNSCH).state) == 22.5
    hass.bus.async_fire(STATUS_EVENT, _event(raum_soll=22.0))
    await hass.async_block_till_done()
    assert float(hass.states.get(WUNSCH).state) == 22.0


async def test_an_automation_context_is_passed_on(hass):
    """Eine Automation ruft mit einem Kontext ohne Nutzer, aber mit parent_id (dem ausloesenden Ereignis)."""
    await _setup(hass)
    events = _listen(hass)
    context = Context(parent_id="ausloeser")
    await hass.services.async_call("number", "set_value", {"entity_id": WUNSCH, "value": 21}, blocking=True,
                                   context=context)
    await hass.async_block_till_done()
    assert [e.data for e in events] == [{"raum_soll": 21.0}]
    assert (events[0].context.id, events[0].context.parent_id, events[0].context.user_id) == (
        context.id, "ausloeser", None)


@pytest.mark.parametrize("value, sent", [(22.3, 22.5), (22.2, 22.0), (22.25, 22.5), (24.9, 25.0), (15.1, 15.0)])
async def test_the_value_is_rounded_to_the_half_degree_step(hass, value, sent):
    await _setup(hass)
    events = _listen(hass)
    await hass.services.async_call("number", "set_value", {"entity_id": WUNSCH, "value": value}, blocking=True)
    await hass.async_block_till_done()
    assert [e.data for e in events] == [{"raum_soll": sent}]
    assert float(hass.states.get(WUNSCH).state) == sent


async def test_a_non_finite_value_is_refused_without_an_event(hass):
    """HAs min/max-Pruefung laesst NaN durch; die Entity lehnt es ab."""
    await _setup(hass)
    events = _listen(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("number", "set_value", {"entity_id": WUNSCH, "value": float("nan")},
                                       blocking=True)
    await hass.async_block_till_done()
    assert events == []


async def test_a_value_outside_the_range_is_refused_without_an_event(hass):
    await _setup(hass)
    events = _listen(hass)
    assert hass.states.get(WUNSCH) is not None
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("number", "set_value", {"entity_id": WUNSCH, "value": 30}, blocking=True)
    await hass.async_block_till_done()
    assert events == []


async def test_missing_or_broken_target_is_unknown(hass):
    await _setup(hass)
    for raum_soll in (None, "21", True, float("nan")):
        hass.bus.async_fire(STATUS_EVENT, _event(raum_soll=raum_soll))
        await hass.async_block_till_done()
        assert hass.states.get(WUNSCH).state == "unknown", raum_soll


@pytest.mark.parametrize("raum_soll", [10**400, -10**400], ids=["plus", "minus"])
async def test_a_huge_integer_target_is_unknown_without_an_error(hass, caplog, raum_soll):
    """Ein JSON-int ausserhalb des float-Bereichs (isfinite wirft OverflowError) ist unbekannt wie NaN."""
    await _setup(hass)
    hass.bus.async_fire(STATUS_EVENT, _event())
    await hass.async_block_till_done()
    assert float(hass.states.get(WUNSCH).state) == 21.5
    hass.bus.async_fire(STATUS_EVENT, _event(raum_soll=raum_soll))
    await hass.async_block_till_done()
    assert hass.states.get(WUNSCH).state == "unknown"
    assert "OverflowError" not in caplog.text


async def test_restores_the_last_value_after_a_restart(hass):
    mock_restore_cache(hass, [State(WUNSCH, "20.5")])
    await _setup(hass)
    assert float(hass.states.get(WUNSCH).state) == 20.5


async def test_a_wish_is_refused_while_the_thermostat_is_off(hass):
    """5c F/Q3: das Add-on verwirft den Wunsch; die Entity sagt es sofort, statt einen Wert zu zeigen, der nie gilt."""
    await _setup(hass)
    aus = {"key": "soll_quelle_aus", "stufe": "warnung", "text": "Thermostat aus, SmartHeat haelt 21,5 °C"}
    hass.bus.async_fire(STATUS_EVENT, _event(geraet_hinweise=[aus]))
    await hass.async_block_till_done()
    events = _listen(hass)
    with pytest.raises(ServiceValidationError) as error:
        await hass.services.async_call("number", "set_value", {"entity_id": WUNSCH, "value": 23.0}, blocking=True)
    assert error.value.translation_key == "thermostat_aus"
    await hass.async_block_till_done()
    assert events == [] and float(hass.states.get(WUNSCH).state) == 21.5


async def test_a_wish_is_accepted_again_once_the_thermostat_is_on(hass):
    await _setup(hass)
    hass.bus.async_fire(STATUS_EVENT, _event(geraet_hinweise=[{"key": "soll_quelle_aus", "stufe": "warnung",
                                                               "text": "aus"}]))
    hass.bus.async_fire(STATUS_EVENT, _event())  # Hinweis wieder weg
    await hass.async_block_till_done()
    events = _listen(hass)
    await hass.services.async_call("number", "set_value", {"entity_id": WUNSCH, "value": 23.0}, blocking=True)
    await hass.async_block_till_done()
    assert [e.data["raum_soll"] for e in events] == [23.0]
