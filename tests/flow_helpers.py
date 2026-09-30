"""Hilfen fuer die Config- und Options-Flow-Tests (Wizard 2.0, Optionen ohne Login): Supervisor,
Server, Registry, Add-ons."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import voluptuous as vol
from homeassistant.components.hassio import AddonError, AddonManager
from homeassistant.data_entry_flow import UnknownFlow
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartheat.const import DOMAIN
from custom_components.smartheat.supervisor_client import ResolvedAddon

from .addon_fakes import FakeSupervisor, check_options, status_event

FLOW = "custom_components.smartheat.config_flow"
CATALOG = json.loads((Path(__file__).parent / "fixtures" / "catalog.json").read_text())
TENANT = "wohnung1"
MQTT_PASSWORD = "mqtt-geheim-123"
CF_SECRET = "cf-secret-789"
PROFILE_PARAMS = {"verteilsystem": "Heizkoerper", "daily_trigger_time": "12:00"}
PROVISIONING = {
    "username": "wohnung1_a1b2c3d4", "password": MQTT_PASSWORD,
    "cloudflared_hostname": "mqtt.example.org", "cloudflared_local_port": 18830,
    "cloudflared_service_token_id": "cf-id-456", "cloudflared_service_token_secret": CF_SECRET,
    "profile_params": PROFILE_PARAMS,
}
CURVE = "number.zuhause_circuit_0_heating_curve"
MIN_FLOW = "number.zuhause_circuit_0_min_flow_temperature_setpoint"
ZONE = "climate.zuhause_zone_1_circuit_0_climate"
FLOW_SETPOINT = "sensor.heizraum_zuhause_circuit_0_flow_temperature_setpoint"
HEAT_LIMIT = "number.zuhause_circuit_0_heat_limit"
OUTDOOR = "sensor.zuhause_outdoor_temperature"
SYSTEM_INPUT = {"verteilsystem": "heizkoerper", "erzeuger_typ": "gastherme"}
ROOMS_INPUT = {"room_sensors": ["sensor.wz_temperatur", "sensor.kz_temperatur"], "entity_room_target": "climate.wz"}
PLANT_INPUT = {
    "entity_curve_current": CURVE, "entity_shift_current": ZONE, "entity_min_flow": MIN_FLOW,
    "entity_heat_limit": HEAT_LIMIT, "entity_outdoor_temp": OUTDOOR,
    "entity_flow_setpoint": FLOW_SETPOINT, "advanced": {},
}
BRIDGE_OPTIONS = {
    "tenant_id": TENANT, "mqtt_username": "wohnung1_alt", "mqtt_password": "alt-geheim",
    "local_check_interval_seconds": 120, "room_sensors": ["sensor.wz_temperatur"],
}
CF_OPTIONS = {
    "hostname": "mqtt.example.org", "local_port": 18830, "service_token_id": "cf-id-alt", "service_token_secret": "cf-alt",
}


def enable_supervisor(hass, monkeypatch, versions: dict[str, str] | None = None) -> AsyncMock:
    """Supervisor-Installation mit beiden Add-ons (bare Slugs) in der Mindestversion."""
    hass.config.components.add("hassio")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-supervisor-token")
    monkeypatch.setattr(
        "homeassistant.components.hassio.addon_manager.get_supervisor_client", lambda hass: SimpleNamespace(),
    )
    resolve = AsyncMock(side_effect=lambda hass, url, min_versions: {
        slug: ResolvedAddon(slug, (versions or {}).get(slug, version)) for slug, version in min_versions.items()
    })
    monkeypatch.setattr("custom_components.smartheat.supervisor_client.async_resolve_addons", resolve)
    monkeypatch.setattr(f"{FLOW}.async_resolve_addons", resolve)
    return resolve


def mock_server(monkeypatch, *, tenants=(TENANT,), catalog=CATALOG, provisioning=PROVISIONING) -> SimpleNamespace:
    mocks = SimpleNamespace(
        login=AsyncMock(return_value="tok123"),
        list_tenants=AsyncMock(return_value=[{"tenant_id": tenant} for tenant in tenants]),
        get_catalog=AsyncMock(return_value=catalog),
        provision=AsyncMock(return_value=provisioning),
        update_profile=AsyncMock(return_value=PROFILE_PARAMS),
        logout=AsyncMock(return_value=None),
        delete_installation=AsyncMock(return_value=204),
    )
    for name in (
        "login", "list_tenants", "get_catalog", "provision", "update_profile", "logout", "delete_installation",
    ):
        monkeypatch.setattr(f"{FLOW}.HeizungsserverClient.{name}", getattr(mocks, name))
    return mocks


def setup_mypyllant(hass, *, circuits=("0",), model="ecoTEC plus VC 206/5-5", outdoor=True) -> MockConfigEntry:
    """Heizungs-Integration wie bei client1 (unique_ids wie im Registry-Auszug)."""
    entry = MockConfigEntry(domain="mypyllant", title="myVAILLANT")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("mypyllant", "SYSTEM")}, name="Zuhause", model=model,
    )
    ent_reg = er.async_get(hass)
    for circuit in circuits:
        for suffix, object_id, value, unit in (
            ("heating_curve", f"zuhause_circuit_{circuit}_heating_curve", "1.1", None),
            ("min_flow_temperature_setpoint", f"zuhause_circuit_{circuit}_min_flow_temperature_setpoint", "22", "°C"),
            ("heat_demand_limited_by_outside_temperature", f"zuhause_circuit_{circuit}_heat_limit", "16", "°C"),
        ):
            ent_reg.async_get_or_create(
                "number", "mypyllant", f"mypyllant SYSTEM_circuit_{circuit}_{suffix}",
                config_entry=entry, device_id=device.id, suggested_object_id=object_id,
            )
            hass.states.async_set(f"number.{object_id}", value, {"unit_of_measurement": unit} if unit else {})
        ent_reg.async_get_or_create(
            "climate", "mypyllant", f"mypyllant_SYSTEM_zone_{circuit}_climate",
            config_entry=entry, device_id=device.id, suggested_object_id=f"zuhause_zone_1_circuit_{circuit}_climate",
            original_name=f"Zuhause Zone 1 (Circuit {circuit}) Climate",
        )
        hass.states.async_set(
            f"climate.zuhause_zone_1_circuit_{circuit}_climate", "auto", {"temperature": 20.0},
        )
        ent_reg.async_get_or_create(
            "sensor", "mypyllant", f"mypyllant_SYSTEM_circuit_{circuit}_flow_temperature_setpoint",
            config_entry=entry, device_id=device.id,
            suggested_object_id=f"heizraum_zuhause_circuit_{circuit}_flow_temperature_setpoint",
        )
        hass.states.async_set(
            f"sensor.heizraum_zuhause_circuit_{circuit}_flow_temperature_setpoint", "38.5",
            {"unit_of_measurement": "°C", "device_class": "temperature", "state_class": "measurement"},
        )
    if outdoor:
        ent_reg.async_get_or_create(
            "sensor", "mypyllant", "mypyllant_SYSTEM_home_outdoor_temperature",
            config_entry=entry, device_id=device.id, suggested_object_id="zuhause_outdoor_temperature",
        )
        hass.states.async_set(OUTDOOR, "7.5", {"unit_of_measurement": "°C"})
    return entry


def setup_rooms(hass) -> None:
    """Wohnzimmer: Fuehler + Batterie + Thermostat an einem Geraet; Kinderzimmer: Fuehler ohne Geraet."""
    entry = MockConfigEntry(domain="zha")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("zha", "wz")}, name="Wohnzimmer",
    )
    ent_reg = er.async_get(hass)
    for domain, unique_id, object_id, device_class in (
        ("sensor", "wz_temp", "wz_temperatur", None),
        ("sensor", "wz_bat", "wz_batterie", "battery"),
        ("climate", "wz_climate", "wz", None),
    ):
        ent_reg.async_get_or_create(
            domain, "zha", unique_id, config_entry=entry, device_id=device.id,
            suggested_object_id=object_id, original_device_class=device_class,
        )
    hass.states.async_set("sensor.wz_temperatur", "21.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.wz_batterie", "80", {"unit_of_measurement": "%", "device_class": "battery"})
    hass.states.async_set("climate.wz", "heat", {"current_temperature": 21.2, "temperature": 21.5})
    hass.states.async_set("sensor.kz_temperatur", "20.5", {"unit_of_measurement": "°C"})


def register_phones(hass, *names: str) -> None:
    for name in names:
        hass.services.async_register("notify", name, AsyncMock())


def mock_addons(hass, monkeypatch, *, status="regelt", grund=None, existing_options=None, cloudflared_options=None,
                set_error=None, status_setup_id=None, supervision_error=None) -> SimpleNamespace:
    """AddonManager-Aufrufe aufzeichnen. Der Neustart der Heizungsbruecke feuert das Status-Event
    wie das echte Add-on (mit der setup_id aus den gesetzten Optionen, ausser status_setup_id)."""
    calls = SimpleNamespace(options={}, restarts=[], supervision=[], history=[])

    async def set_options(manager, config):
        if set_error is not None:
            raise set_error
        check_options(manager.addon_slug, config)
        calls.options[manager.addon_slug] = config
        calls.history.append((manager.addon_slug, dict(config)))

    async def restart(manager):
        calls.restarts.append(manager.addon_slug)
        if manager.addon_slug == "heizungsbruecke" and status is not None:
            setup_id = status_setup_id or calls.options["heizungsbruecke"].get("setup_id")
            # Wie der echte Supervisor: das Status-Event kommt erst nach der Rueckkehr von async_restart_addon.
            hass.loop.call_soon(
                hass.bus.async_fire, "smartheat_status", status_event(TENANT, status, setup_id=setup_id, grund=grund),
            )

    async def info(manager):
        source = existing_options if manager.addon_slug == "heizungsbruecke" else cloudflared_options
        return SimpleNamespace(options=dict(source or {}))

    supervisor_log = []
    monkeypatch.setattr("homeassistant.components.hassio.AddonManager.async_set_addon_options", set_options)
    monkeypatch.setattr("homeassistant.components.hassio.AddonManager.async_restart_addon", restart)
    monkeypatch.setattr("homeassistant.components.hassio.AddonManager.async_get_addon_info", info)
    monkeypatch.setattr(
        "custom_components.smartheat.addon_control.get_supervisor_client",
        lambda hass: FakeSupervisor(supervisor_log, error=supervision_error),
    )
    calls.supervision = supervisor_log
    return calls


def fail_addon_reads_after(monkeypatch, server_call) -> None:
    """Jede Add-on-Abfrage nach dem ersten Serveraufruf `server_call` scheitert: der Server ist
    geaendert, in die Add-ons ist noch nichts geschrieben (nach mock_addons() aufrufen)."""
    read = AddonManager.async_get_addon_info

    async def info(manager):
        if server_call.await_count:
            raise AddonError("weg")
        return await read(manager)

    monkeypatch.setattr("homeassistant.components.hassio.AddonManager.async_get_addon_info", info)


def fast_status_wait(monkeypatch, wait_seconds: float = 0.2) -> None:
    monkeypatch.setattr(f"{FLOW}.STATUS_WAIT_SECONDS", wait_seconds)
    monkeypatch.setattr("custom_components.smartheat.options_flow.STATUS_WAIT_SECONDS", wait_seconds)


async def start(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})


def _flow_manager(hass, flow_id: str):
    """Config-Flow und Options-Flow leben in getrennten FlowManagern (`hass.config_entries.flow`
    bzw. `.options`) mit je eigenem `_progress`; `configure`/`finish_progress` bedienen beide."""
    try:
        hass.config_entries.options.async_get(flow_id)
        return hass.config_entries.options
    except UnknownFlow:
        return hass.config_entries.flow


async def configure(hass, result, data=None):
    return await _flow_manager(hass, result["flow_id"]).async_configure(result["flow_id"], data)


async def login(hass, result):
    return await configure(hass, result, {"email": "a@b.de", "password": "geheim"})


async def finish_progress(hass, result):
    """Fortschrittsschritt abwarten und das Folgeergebnis holen."""
    assert result["type"] == "progress"
    await hass.async_block_till_done()
    return await configure(hass, result)


def marker(result, field: str, section: str | None = None):
    schema = result["data_schema"].schema
    if section is not None:
        schema = schema[section].schema.schema
    return next(key for key in schema if key == field)


def suggested(result, field: str, section: str | None = None):
    description = marker(result, field, section).description or {}
    return description.get("suggested_value")


def has_default(result, field: str) -> bool:
    return marker(result, field).default is not vol.UNDEFINED


def select_values(result, field: str) -> list:
    config = result["data_schema"].schema[marker(result, field)].config
    return [option["value"] if isinstance(option, dict) else option for option in config["options"]]


def offers(result, field: str, domain: str, device_class: str | None = None, section: str | None = None) -> bool:
    """Bietet der Entity-Selektor eine Entity dieser Domain/Geraeteklasse an? Bildet die
    Frontend-Auswertung von `filter` nach (Eintraege oder-verknuepft, Felder je Eintrag
    und-verknuepft). Legacy-`domain` auf oberster Ebene ist bewusst ausgeschlossen: wie das
    Frontend es mit `filter` kombiniert, ist nicht festgelegt."""
    schema = result["data_schema"].schema
    if section is not None:
        schema = schema[section].schema.schema
    config = schema[marker(result, field, section)].config
    assert "domain" not in config and "device_class" not in config, config
    return any(
        domain in entry.get("domain", [domain])
        and (not entry.get("device_class") or device_class in entry["device_class"])
        for entry in config["filter"]
    )
