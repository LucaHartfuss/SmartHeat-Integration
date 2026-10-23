"""Status-Ereignisse und Eintraege fuer die Integrationstests. Add-ons und Supervisor bildet
`supervisor_fakes.FakeSupervisor` an der Grenze nach; der `AddonManager` wird nicht ersetzt."""
from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartheat.const import DOMAIN, STATUS_EVENT_SCHEMA

GERAET_ID = "shg-a"
VAILLANT_HEBEL = ["curve", "room_setpoint", "heat_limit", "min_flow"]


def status_event(tenant_id, status, *, grund=None, **overrides) -> dict:
    """Volles Status-Ereignis Schema 3 (Laufzeit-Teil plus die Geraete-Felder; Geraet GERAET_ID)."""
    event = {
        "schema": STATUS_EVENT_SCHEMA, "tenant_id": tenant_id, "addon_version": "0.20.0",
        "status": status, "grund": grund, "notbetrieb": False, "datenfehler": None, "boost": "keiner",
        "letzte_serverantwort": None, "hebelsatz": "vaillant_vrc720", "hebel": None, "gelernt": None,
        "abo": "aktiv", "abo_frist_ende": None,
        "hinweise": {"raumfuehler_ausgefallen": [], "batterie_niedrig": [], "manueller_eingriff": None},
        "geraet_id": GERAET_ID, "geraet_zustand": "regelt", "server_verbunden": True, "raum_soll": None,
        "geraet_hinweise": [],
    }
    event.update(overrides)
    return event


def make_device_entry(hass, *, tenant_id="wohnung1", geraet_id=GERAET_ID, lever_set="vaillant_vrc720",
                      hebel=None, shift_lever="room_setpoint", profile_id="vaillant_gastherme_heizkoerper"):
    """Eintrag ab 1.0.0 wie nach dem Assistenten: Geraete-ID, Anlage, Profil, Hebel des
    Server-Vorschlags; keine Optionen."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=3, unique_id=geraet_id, title=tenant_id,
        data={"geraet_id": geraet_id, "tenant_id": tenant_id, "profile_id": profile_id, "lever_set": lever_set,
              "hebel": list(VAILLANT_HEBEL if hebel is None else hebel), "shift_lever": shift_lever},
    )
    entry.add_to_hass(hass)
    return entry


def make_entry(hass, *, tenant_id="wohnung1", circuit_entry_id="mypyllant-entry", options=None, data=None, version=2,
               data_without=()):
    """Vollstaendiger v2-Eintrag, wie ihn 0.16 anlegte (mit Hebelsatz). data_without: diese
    Schluessel fehlen (z. B. lever_set/shift_lever wie bei client1 vor dem Update)."""
    if data is None:
        data = {
            "tenant_id": tenant_id, "profile_id": "vaillant_gastherme_heizkoerper", "integration_domain": "mypyllant",
            "circuit": {"config_entry_id": circuit_entry_id, "system_key": "SYSTEM", "circuit": "0"},
            "entities": {
                "entity_curve_current": "number.zuhause_circuit_0_heating_curve",
                "entity_shift_current": "climate.zuhause_zone_1_circuit_0_climate",
                "entity_min_flow": "number.zuhause_circuit_0_min_flow_temperature_setpoint",
                "entity_heat_limit": "number.zuhause_circuit_0_heat_limit",
                "entity_outdoor_temp": "sensor.zuhause_outdoor_temperature",
            },
            "lever_set": "vaillant_vrc720", "shift_lever": "room_setpoint",
        }
    entry = MockConfigEntry(
        domain=DOMAIN, version=version, unique_id=tenant_id, title=tenant_id,
        data={key: value for key, value in data.items() if key not in data_without},
        options=options if options is not None else {
            "room_sensors": ["sensor.wz_temperatur"], "entity_room_target": "climate.wz::temperature",
            "notify_services": ["notify.mobile_app_pixel"], "battery_entities": ["sensor.wz_batterie"],
            "notify_hints_off": [],
        },
    )
    entry.add_to_hass(hass)
    return entry
