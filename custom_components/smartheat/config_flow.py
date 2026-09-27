"""Config-Flow der SmartHeat-Integration: Setup-Wizard 2.0 (Spec TP6).

Ablauf: user (Vorabpruefung der Add-ons, Login) -> tenant (bei einem Tenant uebersprungen) ->
heating (bei einer Integration uebersprungen) -> system -> rooms -> plant_values ->
notifications (ohne Handy nur mit Hinweis) -> summary -> setup (Fortschritt: provision, Add-ons,
Warten auf die Status-Entity) -> finish. provision() laeuft erst in setup, nach allen Pruefungen
(I1). Der Config-Entry enthaelt keine Zugangsdaten."""
from __future__ import annotations

import asyncio
import logging
import os
import uuid

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.hassio import AddonError
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector, translation
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.hassio import is_hassio
from homeassistant.util import dt as dt_util

from . import detection, validation
from .api_client import ApiError, CannotConnect, HeizungsserverClient, InvalidAuth
from .catalog import parse_integrations, verified_profiles
from .const import (
    ADDON_REPOSITORY_URL, CLOUDFLARED_ADDON_SLUG, DEFAULT_HEIZUNGSSERVER_BASE_URL, DOMAIN,
    HEIZUNGSBRUECKE_ADDON_SLUG, KPI_ENERGY_CHANNELS, KPI_ROLE_STATE_CLASS_EXPECTATIONS,
    KPI_SCALAR_ROLE_BY_CAPABILITY, MIN_ADDON_VERSIONS, OPTION_BATTERY_ENTITIES, OPTION_NOTIFY_SERVICES,
    OPTION_ROOM_SENSORS, OPTION_SETUP_ID, PLAUSIBLE_RANGES, ROLE_DOMAINS, ROOM_SENSOR_DOMAINS,
    STATUS_ATTR_GRUND, STATUS_ATTR_SETUP_ID, STATUS_BEREIT, STATUS_KONFIGURATIONSFEHLER,
    STATUS_POLL_SECONDS, STATUS_WAIT_SECONDS, UNMANAGED_ADDON_OPTIONS, kpi_energy_role, status_entity_id,
)
from .supervisor_client import (
    AddonNotFoundError, AddonOutdatedError, AmbiguousAddonMatchError, async_get_addon_managers,
    async_resolve_addons,
)

_LOGGER = logging.getLogger(__name__)

PLANT_FIELDS = ("entity_curve_current", "entity_offset_current", "entity_heat_limit", "entity_outdoor_temp")
ADVANCED_SECTION = "advanced"
# Beide Karten immer, ohne Vorbelegung: das Verteilsystem bestimmt die lokalen Sicherheits-
# Clamps (Spec TP6, Nutzer-Entscheidung 2026-09-25). Werte = Uebersetzungsschluessel.
VERTEILSYSTEM_OPTIONS = ["fussbodenheizung", "heizkoerper"]
WARNING_STALE = "stale"
WARNING_DEVIATION = "deviation"
ORIGIN_INTEGRATION = "integration"
ORIGIN_WEATHER = "weather"
_ADDON_SPECS = [
    ("Heizungsbruecke", HEIZUNGSBRUECKE_ADDON_SLUG),
    ("Cloudflared Access TCP-Bridge", CLOUDFLARED_ADDON_SLUG),
]


class SmartHeatConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._token: str | None = None
        self._tenants: list[dict] = []
        self._tenant_id: str | None = None
        # Gesetzt, wenn ein 401 mitten im Ablauf zurueck zum Login zwingt (Hinweis im Formular).
        self._session_expired = False
        self._hints: dict[str, str] | None = None
        self._catalog: dict | None = None
        self._integrations: list = []
        self._integration = None
        self._entries: list[detection.RegistryEntry] = []
        self._devices: list[detection.DeviceInfo] = []
        self._circuits: list[detection.Circuit] = []
        self._circuit: detection.Circuit | None = None
        self._profile: dict | None = None
        self._rooms_input: dict = {}
        self._room_sensors: list[str] = []
        self._room_target: str | None = None
        self._plant: dict[str, str] = {}
        self._kpi: dict[str, str] = {}
        self._notify_services: list[str] = []
        self._battery_entities: list[str] = []
        self._provisioning: dict | None = None
        self._setup_id: str | None = None
        self._setup_task: asyncio.Task | None = None
        self._setup_error = ""

    def _client(self) -> HeizungsserverClient:
        return HeizungsserverClient(async_get_clientsession(self.hass), DEFAULT_HEIZUNGSSERVER_BASE_URL)

    async def _hint(self, key: str, **placeholders: str) -> str:
        """Dynamischer Text aus config.hints in der UI-Sprache: description_placeholders werden
        von HA nie selbst uebersetzt."""
        if self._hints is None:
            self._hints = await translation.async_get_translations(
                self.hass, self.hass.config.language, "config", integrations=[DOMAIN],
            )
        text = self._hints.get(f"component.{DOMAIN}.config.hints.{key}", key)
        return text.format(**placeholders) if placeholders else text

    # --- Schritt 0/1: Vorabpruefung und Login ---

    async def _addon_problem(self) -> tuple[str, dict[str, str]] | None:
        try:
            await async_resolve_addons(self.hass, ADDON_REPOSITORY_URL, MIN_ADDON_VERSIONS)
        except (AddonNotFoundError, AmbiguousAddonMatchError, AddonOutdatedError, AddonError) as error:
            return _classify_addon_error(error)
        return None

    async def async_step_user(self, user_input: dict | None = None):
        # Nur auf Supervisor-Installationen: provision() stellt echte Zugangsdaten aus, die in
        # zwei Add-ons geschrieben werden. is_hassio ist HAs kanonischer Check; der rohe
        # SUPERVISOR_TOKEN-Check bleibt als Verteidigung in der Tiefe.
        if not is_hassio(self.hass) or "SUPERVISOR_TOKEN" not in os.environ:
            return self.async_abort(reason="not_supervisor")
        if user_input is None:
            problem = await self._addon_problem()
            if problem is not None:
                return self.async_abort(reason=problem[0], description_placeholders=problem[1])

        errors: dict[str, str] = {}
        if user_input is not None:
            self._session_expired = False
            try:
                self._token = await self._client().login(user_input["email"], user_input["password"])
                self._tenants = await self._client().list_tenants(self._token)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except ApiError:
                errors["base"] = "unknown"
            else:
                if not self._tenants:
                    errors["base"] = "no_tenants"
                else:
                    return await self.async_step_tenant()
        elif self._session_expired:
            errors["base"] = "session_expired"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("email"): str, vol.Required("password"): str}),
            errors=errors,
        )

    async def _session_lost(self):
        self._token = None
        self._session_expired = True
        return await self.async_step_user()

    # --- Schritt 2: Tenant ---

    async def async_step_tenant(self, user_input: dict | None = None):
        tenant_ids = sorted({tenant["tenant_id"] for tenant in self._tenants})
        if user_input is None and len(tenant_ids) == 1:
            user_input = {"tenant_id": tenant_ids[0]}
        if user_input is not None:
            self._tenant_id = user_input["tenant_id"]
            # Single-Instance-Guard: zwei Eintraege wuerden dieselben Add-ons beanspruchen.
            await self.async_set_unique_id(self._tenant_id)
            self._abort_if_unique_id_configured()
            return await self.async_step_heating()
        return self.async_show_form(
            step_id="tenant", data_schema=vol.Schema({vol.Required("tenant_id"): _select(tenant_ids)}),
        )

    # --- Schritt 3: Heizungs-Integration ---

    async def async_step_heating(self, user_input: dict | None = None):
        if self._catalog is None:
            errors: dict[str, str] = {}
            try:
                self._catalog = await self._client().get_catalog(self._token)
            except InvalidAuth:
                return await self._session_lost()
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except ApiError:
                errors["base"] = "unknown"
            if errors:
                return self.async_show_form(step_id="heating", data_schema=vol.Schema({}), errors=errors)
            descriptors = parse_integrations(self._catalog)
            entry_domains = {entry.domain for entry in self.hass.config_entries.async_entries()}
            self._integrations = detection.installed_integrations(descriptors, entry_domains)
            if not self._integrations:
                return self.async_abort(
                    reason="no_supported_integration",
                    description_placeholders={"supported": ", ".join(sorted(d.label for d in descriptors))},
                )
            user_input = None
        domains = [descriptor.domain for descriptor in self._integrations]
        if user_input is None and len(domains) == 1:
            user_input = {"integration": domains[0]}
        if user_input is not None:
            self._integration = next(d for d in self._integrations if d.domain == user_input["integration"])
            self._circuits = []
            return await self.async_step_system()
        options = [{"value": d.domain, "label": d.label} for d in self._integrations]
        return self.async_show_form(
            step_id="heating",
            data_schema=vol.Schema({vol.Required("integration"): selector.SelectSelector(
                selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.LIST),
            )}),
        )

    # --- Schritt 4: System ---

    async def async_step_system(self, user_input: dict | None = None):
        if not self._circuits:
            self._entries, self._devices = detection.registry_snapshot(self.hass)
            self._circuits = detection.find_circuits(self._integration, self._entries, self._devices)
            if not self._circuits:
                return self.async_abort(
                    reason="no_heating_circuit", description_placeholders={"integration": self._integration.label},
                )
        profiles = [p for p in verified_profiles(self._catalog) if p["hersteller"] == self._integration.hersteller]
        if not profiles:
            return self.async_abort(reason="no_verified_profiles")
        erzeuger_typen = _erzeuger_typen(self._catalog)

        errors: dict[str, str] = {}
        if user_input is not None:
            profile = _match_profile(profiles, user_input["erzeuger_typ"], user_input["verteilsystem"])
            if profile is None:
                errors["base"] = "profile_combination_unsupported"
            else:
                self._profile = profile
                key = user_input.get("circuit")
                self._circuit = next((c for c in self._circuits if c.key == key), self._circuits[0])
                return await self.async_step_rooms()

        suggestion = detection.suggest_erzeuger_typ(
            self._integration, {circuit.config_entry_id for circuit in self._circuits}, self._devices,
        )
        erzeuger_key = (
            vol.Required("erzeuger_typ", default=suggestion.lower())
            if suggestion is not None and suggestion.lower() in erzeuger_typen else vol.Required("erzeuger_typ")
        )
        schema = {
            # Pflichtfrage ohne Vorbelegung: bestimmt die lokalen Sicherheits-Clamps.
            vol.Required("verteilsystem"): _select(
                VERTEILSYSTEM_OPTIONS, "verteilsystem", selector.SelectSelectorMode.LIST,
            ),
            erzeuger_key: _select(erzeuger_typen, "erzeuger_typ"),
        }
        if len(self._circuits) > 1:
            schema[vol.Required("circuit")] = selector.SelectSelector(selector.SelectSelectorConfig(
                options=[{"value": c.key, "label": c.label} for c in self._circuits],
                mode=selector.SelectSelectorMode.DROPDOWN,
            ))
        return self.async_show_form(
            step_id="system", data_schema=vol.Schema(schema), errors=errors,
            description_placeholders={"integration": self._integration.label},
        )

    # --- Schritt 5: Raeume ---

    async def async_step_rooms(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            self._rooms_input = dict(user_input)
            refs = [validation.room_sensor_ref(entity_id) for entity_id in user_input.get(OPTION_ROOM_SENSORS) or []]
            target = validation.room_target_ref(user_input["entity_room_target"])
            if not refs:
                errors[OPTION_ROOM_SENSORS] = "room_sensors_required"
            for ref in refs:
                error = validation.check_temperature(self.hass, ref, PLAUSIBLE_RANGES["room"])
                if error:
                    errors[OPTION_ROOM_SENSORS] = error
                    break
            error = validation.check_temperature(self.hass, target, PLAUSIBLE_RANGES["room"])
            if error:
                errors["entity_room_target"] = error
            if not errors:
                errors = validation.duplicate_fields({OPTION_ROOM_SENSORS: refs, "entity_room_target": [target]})
            if not errors:
                self._room_sensors, self._room_target = refs, target
                return await self.async_step_plant_values()
        schema = vol.Schema({
            vol.Required(OPTION_ROOM_SENSORS): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=ROOM_SENSOR_DOMAINS, multiple=True),
            ),
            vol.Required("entity_room_target"): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=ROLE_DOMAINS["entity_room_target"]),
            ),
        })
        return self.async_show_form(
            step_id="rooms", data_schema=self.add_suggested_values_to_schema(schema, self._rooms_input), errors=errors,
        )

    # --- Schritt 6: Anlagenwerte ---

    def _suggestions(self) -> tuple[dict[str, str], dict[str, str]]:
        found = {
            **self._circuit.roles,
            **detection.system_role_suggestions(self._integration, self._circuit, self._entries),
        }
        suggestions = {f"entity_{role}": entity_id for role, entity_id in found.items()}
        origins = {field: ORIGIN_INTEGRATION for field in suggestions}
        if "entity_outdoor_temp" not in suggestions:
            weather = detection.weather_fallback(detection.weather_candidates(self.hass))
            if weather is not None:
                suggestions["entity_outdoor_temp"] = weather
                origins["entity_outdoor_temp"] = ORIGIN_WEATHER
        return suggestions, origins

    async def _origins_text(self, suggestions: dict[str, str], origins: dict[str, str]) -> str:
        lines = []
        for field in PLANT_FIELDS:
            role = await self._hint(f"role_{field}")
            origin = origins.get(field)
            if origin == ORIGIN_INTEGRATION:
                line = await self._hint(
                    "origin_integration", role=role, entity=suggestions[field], integration=self._integration.label,
                )
            elif origin == ORIGIN_WEATHER:
                line = await self._hint("origin_weather", role=role, entity=suggestions[field])
            else:
                line = await self._hint("origin_none", role=role)
            lines.append(f"- {line}")
        return "\n".join(lines)

    def _check_plant(self, plant: dict[str, str]) -> dict[str, str]:
        checks = {
            "entity_curve_current": validation.check_numeric(self.hass, plant["entity_curve_current"]),
            "entity_offset_current": validation.check_temperature(self.hass, plant["entity_offset_current"]),
            "entity_heat_limit": validation.check_temperature(
                self.hass, plant["entity_heat_limit"], PLAUSIBLE_RANGES["heat_limit"],
            ),
            "entity_outdoor_temp": validation.check_temperature(
                self.hass, plant["entity_outdoor_temp"], PLAUSIBLE_RANGES["outdoor"],
            ),
        }
        return {field: error for field, error in checks.items() if error}

    async def async_step_plant_values(self, user_input: dict | None = None):
        kpi_fields = _kpi_fields(self._profile.get("telemetry_capabilities"))
        suggestions, origins = self._suggestions()
        errors: dict[str, str] = {}
        if user_input is not None:
            plant = {field: user_input[field] for field in PLANT_FIELDS}
            advanced = user_input.get(ADVANCED_SECTION) or {}
            errors = self._check_plant(plant)
            kpi, kpi_errors = _resolve_kpi_entities(self.hass, advanced, kpi_fields)
            errors.update(kpi_errors)
            if not errors:
                duplicates = validation.duplicate_fields({
                    OPTION_ROOM_SENSORS: self._room_sensors, "entity_room_target": [self._room_target],
                    **{field: [plant[field]] for field in PLANT_FIELDS},
                    **{field: [entity_id] for field, entity_id in kpi.items()},
                })
                errors = {field: error for field, error in duplicates.items() if field in plant or field in kpi}
            if any(field in kpi_fields for field in errors):
                errors["base"] = "advanced_invalid"
            if not errors:
                self._plant, self._kpi = plant, kpi
                return await self.async_step_notifications()
            suggested = {**{field: user_input.get(field) for field in PLANT_FIELDS}, ADVANCED_SECTION: advanced}
        else:
            suggested = {
                **{field: suggestions.get(field) for field in PLANT_FIELDS},
                ADVANCED_SECTION: {field: suggestions[field] for field in kpi_fields if field in suggestions},
            }
        schema = {
            vol.Required(field): selector.EntitySelector(selector.EntitySelectorConfig(domain=ROLE_DOMAINS[field]))
            for field in PLANT_FIELDS
        }
        if kpi_fields:
            schema[vol.Required(ADVANCED_SECTION)] = section(
                vol.Schema({
                    vol.Optional(field): selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor"]))
                    for field in kpi_fields
                }),
                {"collapsed": True},
            )
        return self.async_show_form(
            step_id="plant_values",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(schema), suggested),
            errors=errors,
            description_placeholders={"origins": await self._origins_text(suggestions, origins)},
        )

    # --- Schritt 7: Benachrichtigungen ---

    async def _battery_text(self) -> str:
        return ", ".join(self._battery_entities) if self._battery_entities else await self._hint("none")

    async def async_step_notifications(self, user_input: dict | None = None):
        battery_candidates = [entry.entity_id for entry in self._entries if entry.device_class == "battery"]
        self._battery_entities = detection.battery_entities(
            [validation.entity_of(ref) for ref in [*self._room_sensors, self._room_target]],
            self._entries, detection.unit_map(self.hass, battery_candidates),
        )
        services = detection.mobile_app_services(self.hass.services.async_services_for_domain("notify"))
        if user_input is not None:
            chosen = user_input.get(OPTION_NOTIFY_SERVICES, [])
            self._notify_services = [service for service in services if service in chosen]
            return await self.async_step_summary()
        # Ohne Companion-App trotzdem zeigen: der Kunde erfaehrt, dass Meldungen nur in HA
        # erscheinen, und sieht die ueberwachten Batterien.
        schema = {}
        if services:
            schema[vol.Optional(OPTION_NOTIFY_SERVICES, default=services)] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=services, multiple=True, mode=selector.SelectSelectorMode.LIST),
            )
        return self.async_show_form(
            step_id="notifications",
            data_schema=vol.Schema(schema),
            description_placeholders={
                "phones_note": await self._hint("notifications_phones" if services else "notifications_no_phones"),
                "batteries": await self._battery_text(),
            },
        )

    # --- Schritt 8: Zusammenfassung ---

    def _warnings(self) -> dict[str, list[str]]:
        refs = [*self._room_sensors, self._room_target, *self._plant.values(), *self._kpi.values()]
        warnings: dict[str, list[str]] = {}
        stale = validation.stale_entities(self.hass, refs, dt_util.utcnow())
        if stale:
            warnings[WARNING_STALE] = stale
        values = {}
        for ref in self._room_sensors:
            value = validation.read_value(self.hass, ref)[0]
            if value is not None:
                values[ref] = value
        deviating = validation.deviating_room_sensors(values)
        if deviating:
            warnings[WARNING_DEVIATION] = deviating
        return warnings

    async def _summary_placeholders(self, warnings: dict[str, list[str]]) -> dict[str, str]:
        def _value(ref: str) -> str:
            value = validation.read_value(self.hass, ref)[0]
            return "–" if value is None else f"{value:g}"

        room_values = [v for v in (validation.read_value(self.hass, ref)[0] for ref in self._room_sensors) if v is not None]
        lines = [
            f"- {await self._hint(f'warning_{kind}', entities=', '.join(entities))}"
            for kind, entities in warnings.items()
        ]
        profile = self._profile
        return {
            "room_temperature": f"{sum(room_values) / len(room_values):.1f}" if room_values else "–",
            "room_sensor_count": str(len(self._room_sensors)),
            "room_target": _value(self._room_target),
            "outdoor_temperature": _value(self._plant["entity_outdoor_temp"]),
            "outdoor_source": self._plant["entity_outdoor_temp"],
            "curve": _value(self._plant["entity_curve_current"]),
            "offset": _value(self._plant["entity_offset_current"]),
            "heat_limit": _value(self._plant["entity_heat_limit"]),
            "profile": ", ".join([
                profile["hersteller"],
                await self._hint(f"erzeuger_typ_{profile['erzeuger_typ'].lower()}"),
                await self._hint(f"verteilsystem_{profile['verteilsystem'].lower()}"),
            ]),
            "recipients": str(len(self._notify_services)),
            "batteries": await self._battery_text(),
            "warnings": "\n".join(lines) if lines else await self._hint("none"),
        }

    async def async_step_summary(self, user_input: dict | None = None):
        warnings = self._warnings()
        errors: dict[str, str] = {}
        if user_input is not None:
            if all(user_input.get(f"confirm_{kind}") for kind in warnings):
                return await self.async_step_setup()
            errors["base"] = "warnings_not_confirmed"
        schema = vol.Schema({vol.Required(f"confirm_{kind}", default=False): bool for kind in warnings})
        return self.async_show_form(
            step_id="summary", data_schema=schema, errors=errors,
            description_placeholders=await self._summary_placeholders(warnings),
        )

    # --- Schritt 9: Einrichten (Fortschritt) ---

    async def async_step_setup(self, user_input: dict | None = None):
        return await self._progress("setup", self._run_setup)

    async def async_step_wait_status(self, user_input: dict | None = None):
        return await self._progress("wait_status", self._wait_for_status)

    async def _progress(self, step_id: str, job):
        if self._setup_task is None:
            # Nicht eager: sonst kann der Job schon vor der done()-Pruefung fertig sein, und der
            # Schritt gaebe nie ein Fortschrittsergebnis zurueck.
            self._setup_task = self.hass.async_create_task(job(), eager_start=False)
        if not self._setup_task.done():
            return self.async_show_progress(step_id=step_id, progress_action="setup", progress_task=self._setup_task)
        task, self._setup_task = self._setup_task, None
        try:
            next_step = task.result()
        except Exception:
            _LOGGER.exception("Unerwarteter Fehler bei der Einrichtung")
            self._setup_error = await self._hint("setup_unexpected")
            next_step = "setup_failed"
        return self.async_show_progress_done(next_step_id=next_step)

    async def _run_setup(self) -> str:
        """Liefert den naechsten Schritt: finish, setup_failed, setup_timeout oder user."""
        problem = await self._addon_problem()
        if problem is not None:
            self._setup_error = await self._hint(problem[0], **problem[1])
            return "setup_failed"
        if self._provisioning is None:
            try:
                provisioning = await self._client().provision(self._token, self._tenant_id, self._profile["profile_id"])
            except InvalidAuth:
                self._token = None
                self._session_expired = True
                return "user"
            except ApiError:
                self._setup_error = await self._hint("provisioning_failed")
                return "setup_failed"
            if not isinstance(provisioning.get("profile_params"), dict):
                # Keine Rueckfallwerte: ohne profile_params startet das Add-on nicht (Spec TP3, 4).
                _LOGGER.error("Provisionierungs-Antwort ohne gueltiges 'profile_params'")
                self._setup_error = await self._hint("invalid_provisioning_response")
                return "setup_failed"
            self._provisioning = provisioning
        self._setup_id = uuid.uuid4().hex
        try:
            heizungsbruecke, cloudflared = await async_get_addon_managers(self.hass, _ADDON_SPECS)
            existing = (await heizungsbruecke.async_get_addon_info()).options
            await heizungsbruecke.async_set_addon_options(self._heizungsbruecke_options(existing))
            await cloudflared.async_set_addon_options(self._cloudflared_options())
            await cloudflared.async_restart_addon()
            await heizungsbruecke.async_restart_addon()
        except (AddonNotFoundError, AmbiguousAddonMatchError, AddonOutdatedError) as error:
            key, placeholders = _classify_addon_error(error)
            self._setup_error = await self._hint(key, **placeholders)
            return "setup_failed"
        except AddonError as error:
            self._setup_error = self._sanitize_addon_error(str(error))
            return "setup_failed"
        return await self._wait_for_status()

    async def _wait_for_status(self) -> str:
        """Nur ein Status mit der setup_id dieses Laufs zaehlt (die Entity ueberlebt den
        Add-on-Neustart und koennte vom vorigen Versuch stammen)."""
        entity_id = status_entity_id(self._tenant_id)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STATUS_WAIT_SECONDS
        while True:
            state = self.hass.states.get(entity_id)
            if state is not None and state.attributes.get(STATUS_ATTR_SETUP_ID) == self._setup_id:
                if state.state == STATUS_BEREIT:
                    return "finish"
                if state.state == STATUS_KONFIGURATIONSFEHLER:
                    self._setup_error = str(state.attributes.get(STATUS_ATTR_GRUND) or "")
                    return "setup_failed"
            if loop.time() >= deadline:
                return "setup_timeout"
            await asyncio.sleep(STATUS_POLL_SECONDS)

    def _heizungsbruecke_options(self, existing: dict) -> dict:
        """Vom Wizard verwaltete Felder vollstaendig neu, nicht verwaltete aus den bestehenden
        Optionen (I3). Veraltete Felder (entity_room_actual, notify_service, profile) fallen weg."""
        options = {key: existing[key] for key in UNMANAGED_ADDON_OPTIONS if key in existing}
        # profile_params zuerst: die festen Schluessel danach kann der Server nicht ueberschreiben.
        options.update(self._provisioning["profile_params"])
        options.update({
            "tenant_id": self._tenant_id,
            "mqtt_username": self._provisioning["username"],
            "mqtt_password": self._provisioning["password"],
            "accounts_api_base_url": DEFAULT_HEIZUNGSSERVER_BASE_URL,
            OPTION_SETUP_ID: self._setup_id,
            OPTION_ROOM_SENSORS: list(self._room_sensors),
            OPTION_NOTIFY_SERVICES: list(self._notify_services),
            OPTION_BATTERY_ENTITIES: list(self._battery_entities),
            "entity_room_target": self._room_target,
            **self._plant,
            **self._kpi,
        })
        return options

    def _cloudflared_options(self) -> dict:
        return {
            "hostname": self._provisioning["cloudflared_hostname"],
            "local_port": self._provisioning["cloudflared_local_port"],
            "service_token_id": self._provisioning["cloudflared_service_token_id"],
            "service_token_secret": self._provisioning["cloudflared_service_token_secret"],
        }

    def _sanitize_addon_error(self, message: str) -> str:
        """Die Supervisor-Meldung nennt oft den abgelehnten Options-Schluessel; Zugangsdaten
        werden vor der Anzeige geschwaerzt und der Text gekuerzt."""
        provisioning = self._provisioning or {}
        for secret in (
            provisioning.get("password"), provisioning.get("cloudflared_service_token_secret"),
            provisioning.get("cloudflared_service_token_id"),
        ):
            if secret:
                message = message.replace(str(secret), "***")
        return message[:300]

    async def async_step_setup_failed(self, user_input: dict | None = None):
        # "Zurueck zur Auswahl" fuehrt zu rooms ohne erneutes provision(): die ausgestellten
        # Zugangsdaten bleiben in self._provisioning.
        return self.async_show_menu(
            step_id="setup_failed", menu_options=["rooms", "cancel"],
            description_placeholders={"grund": self._setup_error},
        )

    async def async_step_setup_timeout(self, user_input: dict | None = None):
        return self.async_show_menu(step_id="setup_timeout", menu_options=["wait_status", "cancel"])

    async def async_step_cancel(self, user_input: dict | None = None):
        return self.async_abort(reason="setup_cancelled")

    async def async_step_finish(self, user_input: dict | None = None):
        try:
            await self._client().logout(self._token)
        except ApiError as error:
            _LOGGER.warning("Logout nach der Einrichtung fehlgeschlagen: %s", error)
        self._token = None
        circuit = self._circuit
        return self.async_create_entry(
            title=self._tenant_id,
            data={
                "tenant_id": self._tenant_id,
                "profile_id": self._profile["profile_id"],
                "integration_domain": self._integration.domain,
                "circuit": {
                    "config_entry_id": circuit.config_entry_id, "system_key": circuit.system_key,
                    "circuit": circuit.circuit,
                },
                "entities": {"entity_room_target": self._room_target, **self._plant, **self._kpi},
                OPTION_ROOM_SENSORS: list(self._room_sensors),
                OPTION_NOTIFY_SERVICES: list(self._notify_services),
                OPTION_BATTERY_ENTITIES: list(self._battery_entities),
            },
        )


def _classify_addon_error(error: Exception) -> tuple[str, dict[str, str]]:
    if isinstance(error, AddonNotFoundError):
        return "addon_missing", {"addon": error.config_slug}
    if isinstance(error, AmbiguousAddonMatchError):
        return "addon_ambiguous", {"addon": error.config_slug}
    if isinstance(error, AddonOutdatedError):
        return "addon_outdated", {"addon": error.config_slug, "installed": error.installed, "required": error.required}
    return "supervisor_unavailable", {}


def _erzeuger_typen(catalog: dict) -> list[str]:
    """Erzeugertypen aus allen Katalog-Profilen (kleingeschrieben = Uebersetzungsschluessel).
    Eine Kombination ohne verifiziertes Profil wird nicht versteckt, sondern am Schritt als
    profile_combination_unsupported gemeldet."""
    return sorted({
        profile["erzeuger_typ"].lower() for profile in catalog.get("profiles") or []
        if isinstance(profile, dict) and isinstance(profile.get("erzeuger_typ"), str)
    })


def _match_profile(profiles: list[dict], erzeuger_typ: str, verteilsystem: str) -> dict | None:
    """Select-Werte sind die kleingeschriebenen Serverwerte. Nicht jede Kombination hat ein
    Profil; dann None statt eines geratenen Profils."""
    for profile in profiles:
        if profile["erzeuger_typ"].lower() == erzeuger_typ and profile["verteilsystem"].lower() == verteilsystem:
            return profile
    return None


def _select(values: list[str], translation_key: str | None = None,
            mode: selector.SelectSelectorMode = selector.SelectSelectorMode.DROPDOWN) -> selector.SelectSelector:
    config = selector.SelectSelectorConfig(options=list(values), mode=mode)
    if translation_key:
        config["translation_key"] = translation_key
    return selector.SelectSelector(config)


_WARNED_UNKNOWN_CHANNELS: set[str] = set()


def _kpi_fields(telemetry_capabilities: dict | None) -> list[str]:
    """KPI-Rollen, die das Profil ausweist (Sektion advanced)."""
    if not telemetry_capabilities:
        return []
    fields = [role for flag, role in KPI_SCALAR_ROLE_BY_CAPABILITY.items() if telemetry_capabilities.get(flag)]
    for channel in telemetry_capabilities.get("energy_channels") or []:
        if channel not in KPI_ENERGY_CHANNELS:
            if channel not in _WARNED_UNKNOWN_CHANNELS:
                _WARNED_UNKNOWN_CHANNELS.add(channel)
                _LOGGER.warning("Ignoring unknown energy channel %r from server profile", channel)
            continue
        fields.append(kpi_energy_role(channel))
    return fields


def _resolve_kpi_entities(hass, user_input: dict, fields: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """state_class-Pruefung der KPI-Rollen: Energie nur total_increasing (KPI-Entscheidung a),
    operating_mode ohne state_class."""
    resolved: dict[str, str] = {}
    errors: dict[str, str] = {}
    for field in fields:
        entity_id = user_input.get(field)
        if not entity_id:
            continue
        state = hass.states.get(entity_id)
        if state is None:
            errors[field] = "entity_not_found"
            continue
        if field != "entity_operating_mode":
            expected = KPI_ROLE_STATE_CLASS_EXPECTATIONS.get(field, "total_increasing")
            if state.attributes.get("state_class") != expected:
                errors[field] = f"state_class_expected_{expected}"
                continue
        resolved[field] = entity_id
    return resolved, errors
