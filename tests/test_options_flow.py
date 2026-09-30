"""Optionen ohne Login (Spec TP7 2.2)."""
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.hassio import AddonError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartheat.const import DOMAIN
from custom_components.smartheat.texts import async_hint

from .addon_fakes import make_entry, status_event
from .flow_helpers import (
    BRIDGE_OPTIONS,
    configure,
    enable_supervisor,
    fast_status_wait,
    finish_progress,
    mock_addons,
    mock_server,
    offers,
    register_phones,
    select_values,
    setup_mypyllant,
    setup_rooms,
    suggested,
)

NEW_ROOMS = {
    "room_sensors": ["sensor.wz_temperatur", "sensor.kz_temperatur"], "entity_room_target": "climate.wz",
    "notify_services": ["notify.mobile_app_iphone"], "battery_entities": ["sensor.wz_batterie"],
    "hint_raumfuehler": True, "hint_batterie": False, "hint_manueller_eingriff": True, "hint_quellwechsel": True,
    "hint_therme": True,
}


async def _open(hass, monkeypatch, entry=None, **addons):
    enable_supervisor(hass, monkeypatch)
    server = mock_server(monkeypatch)
    mypyllant = setup_mypyllant(hass)
    setup_rooms(hass)
    register_phones(hass, "mobile_app_pixel", "mobile_app_iphone")
    fast_status_wait(monkeypatch)
    calls = mock_addons(hass, monkeypatch, existing_options=BRIDGE_OPTIONS, **addons)
    entry = entry or make_entry(hass, circuit_entry_id=mypyllant.entry_id)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return result, entry, calls, server


async def test_options_are_prefilled_from_the_entry(hass, monkeypatch):
    result, _, _, _ = await _open(hass, monkeypatch)

    assert result["step_id"] == "init"
    assert suggested(result, "room_sensors") == ["sensor.wz_temperatur"]
    assert suggested(result, "entity_room_target") == "climate.wz"
    assert suggested(result, "notify_services") == ["notify.mobile_app_pixel"]
    assert suggested(result, "battery_entities") == ["sensor.wz_batterie"]
    schema = result["data_schema"].schema
    assert all(next(k for k in schema if k == f"hint_{c}").default() is True
               for c in ("raumfuehler", "batterie", "manueller_eingriff", "quellwechsel", "therme"))


async def test_saving_merges_only_the_own_keys_and_restarts_only_the_bridge(hass, monkeypatch):
    result, entry, calls, server = await _open(hass, monkeypatch)

    result = await finish_progress(hass, await configure(hass, result, NEW_ROOMS))

    assert result["type"] == "create_entry"
    assert entry.options == {
        "room_sensors": ["sensor.wz_temperatur", "sensor.kz_temperatur"], "entity_room_target": "climate.wz::temperature",
        "notify_services": ["notify.mobile_app_iphone"], "battery_entities": ["sensor.wz_batterie"],
        "notify_hints_off": ["batterie"],
    }
    options = calls.options["heizungsbruecke"]
    assert options == {**BRIDGE_OPTIONS, **entry.options, "setup_id": options["setup_id"]}
    assert calls.restarts == ["heizungsbruecke"]
    assert "cloudflared_access_mqtt" not in calls.options
    server.login.assert_not_awaited()
    server.provision.assert_not_awaited()


@pytest.mark.parametrize("field", ["room_sensors", "entity_room_target"])
async def test_room_selectors_offer_only_temperature_sensors_and_thermostats(hass, monkeypatch, field):
    result, _, _, _ = await _open(hass, monkeypatch)

    assert offers(result, field, "sensor", "temperature")
    assert offers(result, field, "climate")
    assert not offers(result, field, "sensor")
    assert not offers(result, field, "number")


async def test_a_room_sensor_outside_the_offered_domains_is_rejected(hass, monkeypatch):
    result, _, _, _ = await _open(hass, monkeypatch)
    hass.states.async_set("input_number.raum", "21", {"unit_of_measurement": "°C"})

    result = await configure(hass, result, {**NEW_ROOMS, "room_sensors": ["input_number.raum"]})

    assert (result["step_id"], result["errors"]) == ("init", {"room_sensors": "wrong_domain"})


async def test_a_dead_room_sensor_is_rejected(hass, monkeypatch):
    result, entry, calls, _ = await _open(hass, monkeypatch)
    hass.states.async_set("sensor.kz_temperatur", "unavailable")

    result = await configure(hass, result, NEW_ROOMS)

    assert (result["step_id"], result["errors"]) == ("init", {"room_sensors": "entity_unavailable"})
    assert calls.options == {}


async def test_a_room_sensor_that_is_also_the_outdoor_sensor_is_a_duplicate(hass, monkeypatch):
    result, _, _, _ = await _open(hass, monkeypatch)
    hass.states.async_set("sensor.zuhause_outdoor_temperature", "7.5", {"unit_of_measurement": "°C"})

    result = await configure(hass, result, {**NEW_ROOMS, "room_sensors": ["sensor.zuhause_outdoor_temperature"]})

    assert result["errors"] == {"room_sensors": "duplicate_entity"}


async def test_the_heating_zone_as_room_target_is_rejected(hass, monkeypatch):
    result, _, calls, _ = await _open(hass, monkeypatch)

    result = await configure(hass, result, {**NEW_ROOMS, "entity_room_target": "climate.zuhause_zone_1_circuit_0_climate"})

    assert (result["step_id"], result["errors"]) == ("init", {"entity_room_target": "zone_is_room_target"})
    assert calls.options == {}


async def test_warnings_need_confirmation(hass, monkeypatch):
    result, _, calls, _ = await _open(hass, monkeypatch)
    hass.states.async_set("sensor.kz_temperatur", "25.0", {"unit_of_measurement": "°C"})

    result = await configure(hass, result, NEW_ROOMS)
    assert result["step_id"] == "confirm"
    assert "sensor.kz_temperatur" in result["description_placeholders"]["warnings"]
    result = await configure(hass, result, {"confirm_deviation": False})
    assert result["errors"] == {"base": "warnings_not_confirmed"}

    result = await finish_progress(hass, await configure(hass, result, {"confirm_deviation": True}))

    assert result["type"] == "create_entry"


async def test_timeout_saves_the_options_anyway(hass, monkeypatch):
    result, entry, _, _ = await _open(hass, monkeypatch, status=None)

    result = await finish_progress(hass, await configure(hass, result, NEW_ROOMS))
    assert result["step_id"] == "timeout"
    result = await configure(hass, result, {})

    assert result["type"] == "create_entry"
    assert entry.options["notify_hints_off"] == ["batterie"]


@pytest.mark.parametrize("status", ["notbetrieb", "abo_inaktiv"])
async def test_a_warning_state_saves_without_a_timeout(hass, monkeypatch, status):
    """Wie im Wizard (SETUP_DONE_STATUSES): das Add-on hat die Optionen uebernommen, auch wenn es gerade
    einen Warnzustand meldet."""
    result, entry, _, _ = await _open(hass, monkeypatch, status=status)

    result = await finish_progress(hass, await configure(hass, result, NEW_ROOMS))

    assert result["type"] == "create_entry"
    assert entry.options["notify_hints_off"] == ["batterie"]


async def test_configuration_error_shows_the_reason_and_keeps_the_old_options(hass, monkeypatch):
    result, entry, _, _ = await _open(hass, monkeypatch, status="konfigurationsfehler", grund="Entity fehlt")
    before = dict(entry.options)

    result = await finish_progress(hass, await configure(hass, result, NEW_ROOMS))
    assert (result["step_id"], result["description_placeholders"]["grund"]) == ("failed", "Entity fehlt")
    result = await configure(hass, result, {})

    assert result["step_id"] == "init"
    assert suggested(result, "room_sensors") == NEW_ROOMS["room_sensors"]
    assert entry.options == before


async def test_notify_services_survive_when_no_phone_is_currently_registered(hass, monkeypatch):
    """Fund Review-Runde 1 (1): kein Handy gerade registriert (Begleit-App noch nicht geladen o.
    Ae.) darf das gespeicherte notify_services nicht beim naechsten Speichern auf leer setzen."""
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch)
    mypyllant = setup_mypyllant(hass)
    setup_rooms(hass)
    # register_phones() bewusst nicht aufgerufen: aktuell kein Handy registriert.
    fast_status_wait(monkeypatch)
    mock_addons(hass, monkeypatch, existing_options=BRIDGE_OPTIONS)
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)  # notify_services: [pixel]
    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert suggested(result, "notify_services") == ["notify.mobile_app_pixel"]
    assert select_values(result, "notify_services") == ["notify.mobile_app_pixel"]

    changed = {**NEW_ROOMS, "notify_services": ["notify.mobile_app_pixel"]}
    result = await finish_progress(hass, await configure(hass, result, changed))

    assert result["type"] == "create_entry"
    assert entry.options["notify_services"] == ["notify.mobile_app_pixel"]


async def test_a_stored_but_currently_unregistered_phone_stays_selectable(hass, monkeypatch):
    """Fund Review-Runde 1 (1): ein frueher gewaehltes Handy, das gerade nicht registriert ist
    (z. B. ein zweites Telefon offline), faellt nicht aus der Auswahl und verschwindet nicht beim
    Speichern, ohne dass der Kunde es abgewaehlt hat."""
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch)
    mypyllant = setup_mypyllant(hass)
    setup_rooms(hass)
    register_phones(hass, "mobile_app_pixel")  # mobile_app_iphone bewusst nicht registriert
    fast_status_wait(monkeypatch)
    mock_addons(hass, monkeypatch, existing_options=BRIDGE_OPTIONS)
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id, options={
        "room_sensors": ["sensor.wz_temperatur"], "entity_room_target": "climate.wz::temperature",
        "notify_services": ["notify.mobile_app_pixel", "notify.mobile_app_iphone"],
        "battery_entities": ["sensor.wz_batterie"], "notify_hints_off": [],
    })
    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert suggested(result, "notify_services") == ["notify.mobile_app_pixel", "notify.mobile_app_iphone"]
    assert select_values(result, "notify_services") == ["notify.mobile_app_pixel", "notify.mobile_app_iphone"]

    changed = {**NEW_ROOMS, "notify_services": ["notify.mobile_app_pixel", "notify.mobile_app_iphone"]}
    result = await finish_progress(hass, await configure(hass, result, changed))

    assert result["type"] == "create_entry"
    assert entry.options["notify_services"] == ["notify.mobile_app_pixel", "notify.mobile_app_iphone"]


async def test_addon_write_failure_keeps_a_fixed_text_and_the_old_options(hass, monkeypatch):
    """Ein Supervisor-Fehler beim Schreiben der Add-on-Optionen zeigt einen festen Hinweistext
    (nicht die Supervisor-Meldung, die Optionswerte zitieren kann) und laesst entry.options und
    die Add-on-Optionen unveraendert; kein Neustart."""
    error = AddonError(f"invalid option mqtt_password={BRIDGE_OPTIONS['mqtt_password']}")
    result, entry, calls, _ = await _open(hass, monkeypatch, set_error=error)
    before = dict(entry.options)

    result = await finish_progress(hass, await configure(hass, result, NEW_ROOMS))

    grund = result["description_placeholders"]["grund"]
    assert result["step_id"] == "failed"
    assert grund == await async_hint(hass, "options_addon_failed")
    assert BRIDGE_OPTIONS["mqtt_password"] not in grund
    assert entry.options == before
    assert calls.options == {}
    assert calls.restarts == []


async def test_zugang_abgelehnt_is_a_failure_with_reason(hass, monkeypatch):
    """Wie konfigurationsfehler: zugang_abgelehnt fuehrt ebenfalls auf `failed`, mit dem Grund vom
    Add-on, und laesst die alten Optionen unveraendert (bislang nur konfigurationsfehler getestet)."""
    result, entry, _, _ = await _open(
        hass, monkeypatch, status="zugang_abgelehnt", grund="Zugangsdaten vom Server abgelehnt",
    )
    before = dict(entry.options)

    result = await finish_progress(hass, await configure(hass, result, NEW_ROOMS))

    assert (result["step_id"], result["description_placeholders"]["grund"]) == (
        "failed", "Zugangsdaten vom Server abgelehnt",
    )
    assert entry.options == before


async def test_an_unexpected_exception_shows_the_generic_hint(hass, monkeypatch):
    """Ein unerwarteter Fehler (nicht die bekannten Add-on-/Statusfaelle) fuehrt ueber
    setup_unexpected auf `failed`, ohne die alten Optionen zu aendern."""
    result, entry, _, _ = await _open(hass, monkeypatch)
    before = dict(entry.options)
    monkeypatch.setattr(
        "custom_components.smartheat.addon_control.StatusListener.async_wait",
        AsyncMock(side_effect=RuntimeError("kaputt")),
    )

    result = await finish_progress(hass, await configure(hass, result, NEW_ROOMS))

    grund = result["description_placeholders"]["grund"]
    assert result["step_id"] == "failed"
    assert grund == await async_hint(hass, "setup_unexpected")
    assert "kaputt" not in grund
    assert entry.options == before


async def test_a_stored_notify_hints_off_defaults_the_switch_to_false(hass, monkeypatch):
    """Ein Hinweis-Schalter, den der Kunde zuvor abgeschaltet hat (`notify_hints_off`), oeffnet
    sich mit `default=False`, nicht mit dem ueblichen `True`."""
    entry = make_entry(hass, options={
        "room_sensors": ["sensor.wz_temperatur"], "entity_room_target": "climate.wz::temperature",
        "notify_services": ["notify.mobile_app_pixel"], "battery_entities": ["sensor.wz_batterie"],
        "notify_hints_off": ["batterie", "quellwechsel"],
    })

    result, _, _, _ = await _open(hass, monkeypatch, entry=entry)

    schema = result["data_schema"].schema
    defaults = {
        category: next(k for k in schema if k == f"hint_{category}").default()
        for category in ("raumfuehler", "batterie", "manueller_eingriff", "quellwechsel", "therme")
    }
    assert defaults == {
        "raumfuehler": True, "batterie": False, "manueller_eingriff": True, "quellwechsel": False, "therme": True,
    }


async def test_incomplete_entry_has_no_options_yet(hass, monkeypatch):
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, unique_id="wohnung1", options={},
        data={"tenant_id": "wohnung1", "profile_id": "p", "unvollstaendig": True},
    )
    entry.add_to_hass(hass)

    result, _, _, _ = await _open(hass, monkeypatch, entry=entry)

    assert (result["type"], result["reason"]) == ("abort", "setup_incomplete")


async def test_entry_without_a_current_plant_field_has_no_options_yet(hass, monkeypatch):
    entry = make_entry(hass)
    entities = {k: v for k, v in entry.data["entities"].items() if k != "entity_min_flow"}
    hass.config_entries.async_update_entry(entry, data={**entry.data, "entities": entities})

    result, _, _, _ = await _open(hass, monkeypatch, entry=entry)

    assert (result["type"], result["reason"]) == ("abort", "setup_incomplete")


_COMPONENT = Path(__file__).parents[1] / "custom_components" / "smartheat"


@pytest.mark.parametrize("path", ["strings.json", "translations/en.json", "translations/de.json"])
def test_every_options_step_has_a_text(path):
    options = json.loads((_COMPONENT / path).read_text())["options"]

    assert {"init", "confirm", "failed", "timeout"} <= set(options["step"])
    assert "apply" in options["progress"]
    assert "setup_incomplete" in options["abort"]
    assert {"room_sensors_required", "entity_unavailable", "duplicate_entity", "warnings_not_confirmed",
            "zone_is_room_target"} <= set(options["error"])


async def test_failed_options_restore_the_previous_addon_options(hass, monkeypatch):
    result, entry, calls, _ = await _open(hass, monkeypatch, status="konfigurationsfehler", grund="Entity fehlt")

    result = await finish_progress(hass, await configure(hass, result, NEW_ROOMS))

    assert result["step_id"] == "failed"
    assert calls.history[-1] == ("heizungsbruecke", BRIDGE_OPTIONS)
    assert calls.restarts == ["heizungsbruecke", "heizungsbruecke"]
    assert result["description_placeholders"]["restore"] == await async_hint(hass, "options_restored")


async def test_write_failure_needs_no_restore(hass, monkeypatch):
    result, _, calls, _ = await _open(hass, monkeypatch, set_error=AddonError("x"))

    result = await finish_progress(hass, await configure(hass, result, NEW_ROOMS))

    assert calls.restarts == []
    assert result["description_placeholders"]["restore"] == ""


async def test_restart_failure_after_write_restores_the_options(hass, monkeypatch):
    result, _, calls, _ = await _open(hass, monkeypatch)
    restarts = []

    async def restart(manager):
        restarts.append(manager.addon_slug)
        if len(restarts) == 1:
            raise AddonError("kaputt")  # erster Neustart scheitert, Optionen sind schon geschrieben
        hass.bus.async_fire("smartheat_status", status_event(
            "wohnung1", "regelt", setup_id=calls.options["heizungsbruecke"].get("setup_id"),
        ))

    monkeypatch.setattr("homeassistant.components.hassio.AddonManager.async_restart_addon", restart)

    result = await finish_progress(hass, await configure(hass, result, NEW_ROOMS))

    assert result["step_id"] == "failed"
    assert calls.history[-1] == ("heizungsbruecke", BRIDGE_OPTIONS)
    assert restarts == ["heizungsbruecke", "heizungsbruecke"]
    assert result["description_placeholders"]["restore"] == await async_hint(hass, "options_restored")


async def test_restore_failure_is_named(hass, monkeypatch):
    result, _, calls, _ = await _open(hass, monkeypatch, status="konfigurationsfehler", grund="x")
    restarts = []

    async def restart(manager):
        restarts.append(manager.addon_slug)
        if len(restarts) > 1:
            raise AddonError("weg")  # Neustart nach dem Wiederherstellen scheitert
        hass.bus.async_fire("smartheat_status", status_event(
            "wohnung1", "konfigurationsfehler", setup_id=calls.options["heizungsbruecke"]["setup_id"], grund="x",
        ))

    monkeypatch.setattr("homeassistant.components.hassio.AddonManager.async_restart_addon", restart)

    result = await finish_progress(hass, await configure(hass, result, NEW_ROOMS))

    assert result["step_id"] == "failed"
    assert result["description_placeholders"]["restore"] == await async_hint(hass, "options_restore_failed")


async def test_the_therme_hint_switch_is_offered_and_defaults_to_on(hass, monkeypatch):
    result, _, _, _ = await _open(hass, monkeypatch)

    schema = result["data_schema"].schema
    assert next(k for k in schema if k == "hint_therme").default() is True
