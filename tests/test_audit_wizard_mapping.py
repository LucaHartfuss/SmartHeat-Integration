"""Audit 2026-09-30 (Teilsystem C): Belege fuer Zuordnungs- und Mehrfach-Eintrags-Befunde.

Befunde AU-003, AU-004, AU-011 (behoben in TP12c). Die Tests dokumentieren das erwartete (sichere)
Verhalten; kein xfail mehr, sie sichern die Behebung ab."""
import json
from pathlib import Path

from homeassistant.helpers import entity_registry as er

from custom_components.smartheat.catalog import parse_integrations
from custom_components.smartheat.const import PLANT_FIELDS
from custom_components.smartheat.detection import RegistryEntry, find_circuits

from .addon_fakes import make_entry
from .flow_helpers import (
    BRIDGE_OPTIONS,
    CF_OPTIONS,
    ROOMS_INPUT,
    SYSTEM_INPUT,
    TENANT,
    configure,
    enable_supervisor,
    fast_status_wait,
    finish_progress,
    login,
    mock_addons,
    mock_server,
    register_phones,
    select_values,
    setup_mypyllant,
    setup_rooms,
    start,
    suggested,
)

FIXTURES = Path(__file__).parent / "fixtures"
[MYPYLLANT] = parse_integrations(json.loads((FIXTURES / "catalog.json").read_text()))


async def test_reconfigure_with_a_circuit_change_suggests_the_new_circuits_entities(hass, monkeypatch):
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch)
    mypyllant = setup_mypyllant(hass, circuits=("0", "1"))
    setup_rooms(hass)
    register_phones(hass, "mobile_app_pixel")
    fast_status_wait(monkeypatch)
    calls = mock_addons(hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS)
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)  # Eintrag auf Kreis 0

    result = await login(hass, await entry.start_reconfigure_flow(hass))
    circuit_1 = next(value for value in select_values(result, "circuit") if value.endswith("|1"))
    result = await configure(hass, result, {**SYSTEM_INPUT, "circuit": circuit_1})
    result = await configure(hass, result, ROOMS_INPUT)
    assert result["step_id"] == "plant_values"

    # Der Kunde uebernimmt die Vorbelegung unveraendert ("Weiter").
    plant = {field: suggested(result, field) for field in (*PLANT_FIELDS, "entity_flow_setpoint")}
    result = await configure(hass, result, {**plant, "advanced": {}})
    result = await configure(hass, result, {"notify_services": suggested(result, "notify_services")})
    result = await finish_progress(hass, await configure(hass, result, {}))
    await hass.async_block_till_done()
    assert (result["type"], result["reason"]) == ("abort", "reconfigure_successful")

    written = calls.options["heizungsbruecke"]
    assert entry.data["circuit"]["circuit"] == "1"
    # Erwartet: der gewaehlte Kreis 1 wird beschrieben, nicht der alte Kreis 0.
    assert written["entity_curve_current"] == "number.zuhause_circuit_1_heating_curve"
    assert written["entity_min_flow"] == "number.zuhause_circuit_1_min_flow_temperature_setpoint"
    assert written["entity_shift_current"] == "climate.zuhause_zone_1_circuit_1_climate"


async def test_reconfigure_with_an_integration_change_uses_the_new_detection(hass, monkeypatch):
    """AU-003: Eintrag auf einer anderen Integration -> keine Vorbelegung aus dem Eintrag."""
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch)
    mypyllant = setup_mypyllant(hass)
    er.async_get(hass).async_get_or_create(
        "sensor", "mypyllant", "mypyllant_SYSTEM_home_water_pressure", config_entry=mypyllant,
        suggested_object_id="zuhause_system_water_pressure",
    )
    setup_rooms(hass)
    register_phones(hass, "mobile_app_pixel")
    fast_status_wait(monkeypatch)
    mock_addons(hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS)
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)
    hass.config_entries.async_update_entry(entry, data={
        **entry.data, "integration_domain": "andere",
        "entities": {
            **entry.data["entities"], "entity_curve_current": "number.fremd",
            "entity_system_water_pressure": "sensor.veralteter_druck",
        },
    })

    result = await login(hass, await entry.start_reconfigure_flow(hass))
    result = await configure(hass, result, SYSTEM_INPUT)
    result = await configure(hass, result, ROOMS_INPUT)

    assert suggested(result, "entity_curve_current") == "number.zuhause_circuit_0_heating_curve"
    # KPI genauso: Erkennung statt veraltetem Eintragswert.
    assert suggested(result, "entity_system_water_pressure", section="advanced") == "sensor.zuhause_system_water_pressure"


async def test_second_entry_on_the_same_home_assistant_is_refused(hass, monkeypatch):
    make_entry(hass, tenant_id=TENANT)
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch, tenants=(TENANT, "haus2"))
    setup_mypyllant(hass)

    result = await start(hass)

    # single_config_entry: die Add-ons auf dieser HA gehoeren bereits TENANT (AU-011).
    assert (result["type"], result["reason"]) == ("abort", "single_instance_allowed")


def _entry(entity_id, unique_id, name=None):
    return RegistryEntry(entity_id, unique_id, "mypyllant", name, "e1", None, None)


def _circuit(circuit: str) -> list[RegistryEntry]:
    return [
        _entry(f"number.curve_{circuit}", f"mypyllant S_circuit_{circuit}_heating_curve"),
        _entry(f"number.min_flow_{circuit}", f"mypyllant S_circuit_{circuit}_min_flow_temperature_setpoint"),
        _entry(f"number.limit_{circuit}", f"mypyllant S_circuit_{circuit}_heat_demand_limited_by_outside_temperature"),
    ]


def test_zone_of_another_circuit_is_not_suggested():
    # mypyllant benennt Zonen "<Anlage> Zone <n> (Circuit <zugeordneter Kreis>)" (vgl. Fixture client1:
    # unique_id ..._zone_0_climate, Name "Zuhause Zone 1 (Circuit 0) Climate").
    entries = _circuit("0") + _circuit("1") + [
        _entry("climate.zone_a", "mypyllant_S_zone_0_climate", "Haus Zone 1 (Circuit 1) Climate"),
        _entry("climate.zone_b", "mypyllant_S_zone_1_climate", "Haus Zone 2 (Circuit 0) Climate"),
    ]

    circuit_0 = next(c for c in find_circuits(MYPYLLANT, entries, []) if c.circuit == "0")

    # Erwartet: keine Vorbelegung (oder die Zone von Kreis 0), nie die Zone eines anderen Kreises.
    assert circuit_0.roles.get("shift_current") in (None, "climate.zone_b")
