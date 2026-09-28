"""Aufzeichnende Attrappen fuer Add-ons, Supervisor und Status-Events (Spec TP7)."""
from __future__ import annotations

from types import SimpleNamespace

from homeassistant.components.hassio import AddonState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartheat.const import DOMAIN


class FakeAddon:
    """AddonManager-Ersatz: jede Aktion landet als Tupel in `log`."""

    def __init__(self, slug, log, *, options=None, state=AddonState.RUNNING, on_restart=None, error=None):
        self.addon_slug = slug
        self.log = log
        self.options = dict(options or {})
        self.state = state
        self.on_restart = on_restart
        self.error = error

    def _check(self):
        if self.error is not None:
            raise self.error

    async def async_get_addon_info(self):
        self._check()
        return SimpleNamespace(options=dict(self.options), state=self.state, version="0.20.0")

    async def async_set_addon_options(self, config):
        self._check()
        self.log.append(("options", self.addon_slug, dict(config)))
        self.options = dict(config)

    async def async_restart_addon(self):
        self._check()
        self.log.append(("restart", self.addon_slug))
        if self.on_restart is not None:
            self.on_restart(self)

    async def async_start_addon(self):
        self._check()
        self.log.append(("start", self.addon_slug))

    async def async_stop_addon(self):
        self._check()
        self.log.append(("stop", self.addon_slug))


class FakeSupervisor:
    """get_supervisor_client(hass)-Ersatz: zeichnet set_addon_options(slug, AddonsOptions) auf."""

    def __init__(self, log, error=None):
        self.log = log
        self.error = error
        self.addons = SimpleNamespace(set_addon_options=self._set_addon_options)

    async def _set_addon_options(self, slug, options):
        if self.error is not None:
            raise self.error
        self.log.append(("supervision", slug, options.to_dict()))


def status_event(tenant_id, status, *, setup_id=None, grund=None, **overrides) -> dict:
    """Volles Status-Event wie vom Add-on 0.20.0 (Spec TP7 1.1)."""
    event = {
        "schema": 1, "tenant_id": tenant_id, "setup_id": setup_id, "addon_version": "0.20.0",
        "status": status, "grund": grund, "notbetrieb": False, "datenfehler": None, "boost": "keiner",
        "letzte_serverantwort": None, "kurve": None, "offset": None, "abo": "aktiv", "abo_frist_ende": None,
        "hinweise": {"raumfuehler_ausgefallen": [], "batterie_niedrig": [], "manueller_eingriff": None},
    }
    event.update(overrides)
    return event


def make_entry(hass, *, tenant_id="wohnung1", circuit_entry_id="mypyllant-entry", options=None, data=None, version=2):
    """Vollstaendiger v2-Eintrag wie nach dem Wizard (Spec TP7 2.1)."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=version, unique_id=tenant_id, title=tenant_id,
        data=data if data is not None else {
            "tenant_id": tenant_id, "profile_id": "vaillant_gastherme_heizkoerper", "integration_domain": "mypyllant",
            "circuit": {"config_entry_id": circuit_entry_id, "system_key": "SYSTEM", "circuit": "0"},
            "entities": {
                "entity_curve_current": "number.zuhause_circuit_0_heating_curve",
                "entity_offset_current": "number.zuhause_circuit_0_min_flow_temperature_setpoint",
                "entity_heat_limit": "number.zuhause_circuit_0_heat_limit",
                "entity_outdoor_temp": "sensor.zuhause_outdoor_temperature",
            },
        },
        options=options if options is not None else {
            "room_sensors": ["sensor.wz_temperatur"], "entity_room_target": "climate.wz::temperature",
            "notify_services": ["notify.mobile_app_pixel"], "battery_entities": ["sensor.wz_batterie"],
            "notify_hints_off": [],
        },
    )
    entry.add_to_hass(hass)
    return entry
