"""Config-Flow der SmartHeat-Integration: Setup-Wizard 2.0 (Spec TP6) mit Neu konfigurieren und
Reauth (Spec TP7 2.3, 2.4).

Einrichten: user (Vorabpruefung der Add-ons, Login) -> tenant (bei einem Tenant uebersprungen) ->
heating (bei einer Integration uebersprungen) -> system -> rooms -> plant_values ->
notifications -> summary -> setup (Fortschritt: Zugang, Add-on-Optionen, Watchdog/Boot,
Neustart, Warten auf das Status-Event) -> finish.
Neu konfigurieren: derselbe Weg ab Login mit festem Tenant, vorbelegt aus dem Eintrag; statt
provision() nur der Profilwechsel, die Zugangsdaten bleiben (provision() nur, wenn das Add-on
keine hat). Reauth: Login -> setup mit provision(). Der Eintrag enthaelt keine Zugangsdaten."""
from __future__ import annotations

import asyncio
import logging
import os
import uuid

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.hassio import AddonError
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.hassio import is_hassio
from homeassistant.util import dt as dt_util

from . import detection, validation
from .addon_control import WAIT_DONE, WAIT_FAILED, StatusListener, async_set_supervision
from .api_client import ApiError, CannotConnect, HeizungsserverClient, InvalidAuth
from .catalog import parse_integrations, verified_profiles
from .const import (
    ADDON_REPOSITORY_URL, ADDON_SPECS, BRIDGE_CREDENTIAL_OPTIONS, CLOUDFLARED_CREDENTIAL_OPTIONS, DATA_INCOMPLETE,
    DEFAULT_HEIZUNGSSERVER_BASE_URL, DOMAIN, KPI_ENERGY_CHANNELS, KPI_ROLE_STATE_CLASS_EXPECTATIONS,
    KPI_SCALAR_ROLE_BY_CAPABILITY, MIN_ADDON_VERSIONS, OPTION_ABGEMELDET, OPTION_BATTERY_ENTITIES,
    OPTION_ENTITY_ROOM_TARGET, OPTION_NOTIFY_HINTS_OFF, OPTION_NOTIFY_SERVICES, OPTION_ROOM_SENSORS, OPTION_SETUP_ID,
    PLAUSIBLE_RANGES, ROLE_DOMAINS, ROOM_SENSOR_DOMAINS, STATUS_KONFIGURATIONSFEHLER, STATUS_REGELT,
    STATUS_WAIT_SECONDS, STATUS_ZUGANG_ABGELEHNT, UNMANAGED_ADDON_OPTIONS, kpi_energy_role,
)
from .options_flow import SmartHeatOptionsFlow
from .supervisor_client import (
    AddonNotFoundError, AddonOutdatedError, AmbiguousAddonMatchError, async_get_addon_managers,
    async_resolve_addons,
)
from .texts import async_hint

_LOGGER = logging.getLogger(__name__)

PLANT_FIELDS = ("entity_curve_current", "entity_offset_current", "entity_heat_limit", "entity_outdoor_temp")
ADVANCED_SECTION = "advanced"
# Beide Karten immer, ohne Vorbelegung bei der Ersteinrichtung: das Verteilsystem bestimmt die
# lokalen Sicherheits-Clamps (Spec TP6, Nutzer-Entscheidung 2026-09-25). Werte = Uebersetzungsschluessel.
VERTEILSYSTEM_OPTIONS = ["fussbodenheizung", "heizkoerper"]
WARNING_STALE = "stale"
WARNING_DEVIATION = "deviation"
ORIGIN_INTEGRATION = "integration"
ORIGIN_WEATHER = "weather"


def _has_credentials(bridge_options: dict, cloudflared_options: dict) -> bool:
    """Beide Add-ons brauchen ihre Zugangsdaten (Fund 2, Fix-Runde 1): fehlen die Tunnel-Token,
    weil nur cloudflared neu installiert wurde, waeren sonst provision() uebersprungen und leere
    Token in _keep_access uebernommen worden (stiller setup_timeout, da der Tunnel nicht steht)."""
    return (
        all(bridge_options.get(key) for key in BRIDGE_CREDENTIAL_OPTIONS)
        and all(cloudflared_options.get(key) for key in CLOUDFLARED_CREDENTIAL_OPTIONS)
    )


class SmartHeatConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self) -> None:
        self._token: str | None = None
        self._tenants: list[dict] = []
        self._tenant_id: str | None = None
        # Gesetzt, wenn ein 401 mitten im Ablauf zurueck zum Login zwingt (Hinweis im Formular).
        self._session_expired = False
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
        # None = Schritt notifications noch nicht bestaetigt (dann alle Handys vorbelegen).
        self._notify_services: list[str] | None = None
        self._battery_entities: list[str] = []
        self._hints_off: list[str] = []
        self._provisioning: dict | None = None
        self._setup_id: str | None = None
        self._setup_task: asyncio.Task | None = None
        self._setup_error = ""
        # Neu konfigurieren/Reauth: der bestehende Eintrag, Tenant fest.
        self._entry: config_entries.ConfigEntry | None = None
        self._reauth = False
        self._system_defaults: dict = {}
        self._status: StatusListener | None = None
        self._supervision_failed = False

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> SmartHeatOptionsFlow:
        return SmartHeatOptionsFlow()

    @callback
    def async_remove(self) -> None:
        if self._status is not None:
            self._status.close()
            self._status = None

    def _client(self) -> HeizungsserverClient:
        return HeizungsserverClient(async_get_clientsession(self.hass), DEFAULT_HEIZUNGSSERVER_BASE_URL)

    async def _hint(self, key: str, **placeholders: str) -> str:
        return await async_hint(self.hass, key, **placeholders)

    # --- Einstiege Neu konfigurieren / Reauth ---

    async def async_step_reconfigure(self, user_input: dict | None = None):
        """Spec TP7 2.3: derselbe Wizard ab Login, Tenant fest, vorbelegt aus dem Eintrag; bei
        `unvollstaendig` (client1 nach dem Update) aus der Erkennung."""
        self._entry = self._get_reconfigure_entry()
        self._tenant_id = self._entry.data["tenant_id"]
        self._prefill_from_entry()
        return await self.async_step_user()

    async def async_step_reauth(self, entry_data):
        """Spec TP7 2.4: das Add-on meldet zugang_abgelehnt -> Login, dann neue Zugangsdaten."""
        self._entry = self._get_reauth_entry()
        if self._entry.data.get(DATA_INCOMPLETE) or not self._entry.data.get("profile_id"):
            return self.async_abort(reason="reconfigure_first")
        self._reauth = True
        self._tenant_id = self._entry.data["tenant_id"]
        return await self.async_step_user()

    def _prefill_from_entry(self) -> None:
        """Vorbelegung fuer Neu konfigurieren. Bei `unvollstaendig` nichts: dann gilt die Erkennung
        wie bei der Ersteinrichtung, auch ohne vorbelegtes Verteilsystem."""
        entry = self._entry
        if entry.data.get(DATA_INCOMPLETE):
            return
        options = entry.options
        self._rooms_input = {
            OPTION_ROOM_SENSORS: [validation.entity_of(ref) for ref in options.get(OPTION_ROOM_SENSORS, [])],
            OPTION_ENTITY_ROOM_TARGET: validation.entity_of(options.get(OPTION_ENTITY_ROOM_TARGET, "")),
        }
        entities = entry.data.get("entities", {})
        self._plant = {field: entities[field] for field in PLANT_FIELDS if field in entities}
        self._kpi = {field: value for field, value in entities.items() if field not in PLANT_FIELDS}
        self._notify_services = list(options.get(OPTION_NOTIFY_SERVICES, []))
        self._hints_off = list(options.get(OPTION_NOTIFY_HINTS_OFF, []))
        self._system_defaults = {
            "integration": entry.data.get("integration_domain"),
            "circuit": entry.data.get("circuit"),
            "profile_id": entry.data.get("profile_id"),
        }

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
                elif self._entry is None:
                    return await self.async_step_tenant()
                elif self._tenant_id not in {tenant["tenant_id"] for tenant in self._tenants}:
                    # Fund 3, Fix-Runde 1: eine erfolgreiche Sitzung nicht offen lassen, obwohl der
                    # Flow hier abbricht.
                    await self._logout()
                    return self.async_abort(reason="wrong_account")
                elif self._reauth:
                    return await self.async_step_setup()
                else:
                    return await self.async_step_heating()
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

    async def _logout(self) -> None:
        """Best effort, wie am regulaeren Flow-Ende: ein Fehler wird nur geloggt."""
        try:
            await self._client().logout(self._token)
        except ApiError as error:
            _LOGGER.warning("Logout fehlgeschlagen: %s", error)
        self._token = None

    # --- Schritt 2: Tenant (nur Ersteinrichtung) ---

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
        # Vorbelegung, kein Ueberspringen (Controller-Ruling, Fix-Runde 1): bei mehreren erkannten
        # Integrationen muss der Kunde beim Neu konfigurieren weiterhin wechseln koennen, auch wenn
        # die im Eintrag gespeicherte Integration noch installiert ist (Spec 2.3: "vorausgefuellt").
        default = self._system_defaults.get("integration")
        integration_key = vol.Required("integration", default=default) if default in domains else vol.Required("integration")
        return self.async_show_form(
            step_id="heating",
            data_schema=vol.Schema({integration_key: selector.SelectSelector(
                selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.LIST),
            )}),
        )

    # --- Schritt 4: System ---

    def _profile_defaults(self, profiles: list[dict]) -> tuple[str | None, str | None]:
        """Verteilsystem/Erzeugertyp aus dem Profil des Eintrags (nur Neu konfigurieren)."""
        profile = next((p for p in profiles if p["profile_id"] == self._system_defaults.get("profile_id")), None)
        if profile is None:
            return None, None
        return profile["verteilsystem"].lower(), profile["erzeuger_typ"].lower()

    def _stored_circuit_key(self) -> str | None:
        stored = self._system_defaults.get("circuit") or {}
        wanted = (stored.get("config_entry_id"), stored.get("system_key"), stored.get("circuit"))
        return next((c.key for c in self._circuits if (c.config_entry_id, c.system_key, c.circuit) == wanted), None)

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

        verteilsystem_default, erzeuger_default = self._profile_defaults(profiles)
        suggestion = erzeuger_default or detection.suggest_erzeuger_typ(
            self._integration, {circuit.config_entry_id for circuit in self._circuits}, self._devices,
        )
        erzeuger_key = (
            vol.Required("erzeuger_typ", default=suggestion.lower())
            if suggestion is not None and suggestion.lower() in erzeuger_typen else vol.Required("erzeuger_typ")
        )
        # Pflichtfrage: bestimmt die lokalen Sicherheits-Clamps. Vorbelegt nur beim Neu konfigurieren
        # eines vollstaendigen Eintrags (Spec TP7 2.3).
        verteilsystem_key = (
            vol.Required("verteilsystem", default=verteilsystem_default)
            if verteilsystem_default in VERTEILSYSTEM_OPTIONS else vol.Required("verteilsystem")
        )
        schema = {
            verteilsystem_key: _select(VERTEILSYSTEM_OPTIONS, "verteilsystem", selector.SelectSelectorMode.LIST),
            erzeuger_key: _select(erzeuger_typen, "erzeuger_typ"),
        }
        if len(self._circuits) > 1:
            stored_key = self._stored_circuit_key()
            circuit_key = vol.Required("circuit", default=stored_key) if stored_key else vol.Required("circuit")
            schema[circuit_key] = selector.SelectSelector(selector.SelectSelectorConfig(
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
            refs, target, errors = validation.check_rooms(
                self.hass, user_input.get(OPTION_ROOM_SENSORS) or [], user_input[OPTION_ENTITY_ROOM_TARGET],
            )
            if not errors:
                self._room_sensors, self._room_target = refs, target
                return await self.async_step_plant_values()
        schema = vol.Schema({
            vol.Required(OPTION_ROOM_SENSORS): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=ROOM_SENSOR_DOMAINS, multiple=True),
            ),
            vol.Required(OPTION_ENTITY_ROOM_TARGET): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=ROLE_DOMAINS[OPTION_ENTITY_ROOM_TARGET]),
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
        elif self._plant:
            # Zurueck nach setup_failed: die Auswahl des Kunden, nicht erneut die Erkennung (I-1).
            # KPI genau wie gewaehlt: ein bewusst leer gelassenes Feld bleibt leer.
            suggested = {
                **{field: self._plant.get(field) or suggestions.get(field) for field in PLANT_FIELDS},
                ADVANCED_SECTION: {field: self._kpi[field] for field in kpi_fields if field in self._kpi},
            }
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
            schema[vol.Optional(OPTION_NOTIFY_SERVICES)] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=services, multiple=True, mode=selector.SelectSelectorMode.LIST),
            )
        # Vorbelegung statt default: ein weggelassener Schluessel (alle abgewaehlt) bleibt [] und
        # wird nicht wieder mit allen Handys gefuellt. Nach "Zurueck" die Auswahl des Kunden.
        chosen = services if self._notify_services is None else [s for s in services if s in self._notify_services]
        return self.async_show_form(
            step_id="notifications",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(schema), {OPTION_NOTIFY_SERVICES: chosen}),
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

    async def _credentials_note(self) -> str:
        """Neu konfigurieren ohne Zugangsdaten im Add-on (neu installiert, zuvor abgemeldet): der
        Kunde erfaehrt vorher, dass neue ausgestellt werden (Spec TP7 2.3)."""
        if self._entry is None or self._reauth:
            return ""
        try:
            heizungsbruecke, cloudflared = await async_get_addon_managers(self.hass, ADDON_SPECS)
            bridge_options = (await heizungsbruecke.async_get_addon_info()).options
            cloudflared_options = (await cloudflared.async_get_addon_info()).options
        except (AddonNotFoundError, AmbiguousAddonMatchError, AddonOutdatedError, AddonError):
            return ""
        return "" if _has_credentials(bridge_options, cloudflared_options) else await self._hint("new_credentials")

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
            "credentials_note": await self._credentials_note(),
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
        try:
            heizungsbruecke, cloudflared = await async_get_addon_managers(self.hass, ADDON_SPECS)
            if self._provisioning is None:
                next_step = await self._obtain_access(heizungsbruecke, cloudflared)
                if next_step is not None:
                    return next_step
            self._setup_id = uuid.uuid4().hex
            if self._status is None:
                self._status = StatusListener(self.hass, self._tenant_id)
            existing = (await heizungsbruecke.async_get_addon_info()).options
            await heizungsbruecke.async_set_addon_options(self._heizungsbruecke_options(existing))
            await cloudflared.async_set_addon_options(self._cloudflared_options())
            failed = await async_set_supervision(
                self.hass, [heizungsbruecke.addon_slug, cloudflared.addon_slug], enabled=True,
            )
            self._supervision_failed = bool(failed)
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

    async def _obtain_access(self, heizungsbruecke, cloudflared) -> str | None:
        """Zugangsdaten fuer diesen Lauf. Ersteinrichtung und Reauth stellen neue aus
        (provision); Neu konfigurieren behaelt die laufenden und wechselt nur das Profil. None =
        erledigt, sonst der naechste Schritt."""
        if self._entry is not None and not self._reauth:
            bridge_options = (await heizungsbruecke.async_get_addon_info()).options
            cloudflared_options = (await cloudflared.async_get_addon_info()).options
            if _has_credentials(bridge_options, cloudflared_options):
                return await self._keep_access(bridge_options, cloudflared_options)
        return await self._provision()

    def _expire_session(self) -> str:
        self._token = None
        self._session_expired = True
        return "user"

    async def _provision(self) -> str | None:
        profile_id = self._profile["profile_id"] if self._profile is not None else self._entry.data["profile_id"]
        try:
            provisioning = await self._client().provision(self._token, self._tenant_id, profile_id)
        except InvalidAuth:
            return self._expire_session()
        except ApiError:
            self._setup_error = await self._hint("provisioning_failed")
            return "setup_failed"
        if not isinstance(provisioning.get("profile_params"), dict):
            # Keine Rueckfallwerte: ohne profile_params startet das Add-on nicht (Spec TP3, 4).
            _LOGGER.error("Provisionierungs-Antwort ohne gueltiges 'profile_params'")
            self._setup_error = await self._hint("invalid_provisioning_response")
            return "setup_failed"
        self._provisioning = provisioning
        return None

    async def _keep_access(self, bridge_options: dict, cloudflared_options: dict) -> str | None:
        """Spec TP7 2.3: Profilwechsel am Server; MQTT-Zugangsdaten und cloudflared-Optionen aus den
        laufenden Add-ons. So bleibt der Rueckweg per Backup-Restore gueltig."""
        try:
            profile_params = await self._client().update_profile(self._token, self._tenant_id, self._profile["profile_id"])
        except InvalidAuth:
            return self._expire_session()
        except ApiError:
            self._setup_error = await self._hint("profile_update_failed")
            return "setup_failed"
        self._provisioning = {
            "username": bridge_options["mqtt_username"],
            "password": bridge_options["mqtt_password"],
            "cloudflared_hostname": cloudflared_options.get("hostname"),
            "cloudflared_local_port": cloudflared_options.get("local_port"),
            "cloudflared_service_token_id": cloudflared_options.get("service_token_id"),
            "cloudflared_service_token_secret": cloudflared_options.get("service_token_secret"),
            "profile_params": profile_params,
        }
        return None

    async def _wait_for_status(self) -> str:
        """Nur ein Event mit der setup_id dieses Laufs zaehlt (Spec TP7 2.5)."""
        outcome, grund = await self._status.async_wait(
            setup_id=self._setup_id, done=frozenset({STATUS_REGELT}),
            failed=frozenset({STATUS_KONFIGURATIONSFEHLER, STATUS_ZUGANG_ABGELEHNT}), timeout=STATUS_WAIT_SECONDS,
        )
        if outcome == WAIT_DONE:
            return "finish"
        if outcome == WAIT_FAILED:
            self._setup_error = grund
            return "setup_failed"
        return "setup_timeout"

    def _heizungsbruecke_options(self, existing: dict) -> dict:
        """Einrichten/Neu konfigurieren: vom Wizard verwaltete Felder vollstaendig neu, nicht
        verwaltete aus den bestehenden Optionen (I3); veraltete Felder (entity_room_actual,
        notify_service, profile) fallen weg. Reauth: nur Zugangsdaten, Laufkennung und
        abgemeldet, alles andere bleibt (Spec TP7 2.4)."""
        access = {
            "mqtt_username": self._provisioning["username"],
            "mqtt_password": self._provisioning["password"],
            OPTION_SETUP_ID: self._setup_id,
            OPTION_ABGEMELDET: False,
        }
        if self._reauth:
            return {**existing, **access}
        options = {key: existing[key] for key in UNMANAGED_ADDON_OPTIONS if key in existing}
        # profile_params zuerst: die festen Schluessel danach kann der Server nicht ueberschreiben.
        options.update(self._provisioning["profile_params"])
        options.update({
            "tenant_id": self._tenant_id,
            **access,
            "accounts_api_base_url": DEFAULT_HEIZUNGSSERVER_BASE_URL,
            OPTION_ROOM_SENSORS: list(self._room_sensors),
            OPTION_NOTIFY_SERVICES: list(self._notify_services),
            OPTION_BATTERY_ENTITIES: list(self._battery_entities),
            OPTION_NOTIFY_HINTS_OFF: list(self._hints_off),
            OPTION_ENTITY_ROOM_TARGET: self._room_target,
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
        # "Zurueck zur Auswahl" fuehrt zu rooms ohne erneutes provision(): die Zugangsdaten bleiben
        # in self._provisioning. Im Reauth gibt es keine Auswahl.
        menu = ["cancel"] if self._reauth else ["rooms", "cancel"]
        return self.async_show_menu(
            step_id="setup_failed", menu_options=menu, description_placeholders={"grund": self._setup_error},
        )

    async def async_step_setup_timeout(self, user_input: dict | None = None):
        return self.async_show_menu(step_id="setup_timeout", menu_options=["wait_status", "cancel"])

    async def async_step_cancel(self, user_input: dict | None = None):
        return self.async_abort(reason="setup_cancelled")

    def _entry_data(self) -> dict:
        circuit = self._circuit
        return {
            "tenant_id": self._tenant_id,
            "profile_id": self._profile["profile_id"],
            "integration_domain": self._integration.domain,
            "circuit": {
                "config_entry_id": circuit.config_entry_id, "system_key": circuit.system_key, "circuit": circuit.circuit,
            },
            "entities": {**self._plant, **self._kpi},
        }

    def _entry_options(self) -> dict:
        return {
            OPTION_ROOM_SENSORS: list(self._room_sensors),
            OPTION_ENTITY_ROOM_TARGET: self._room_target,
            OPTION_NOTIFY_SERVICES: list(self._notify_services),
            OPTION_BATTERY_ENTITIES: list(self._battery_entities),
            OPTION_NOTIFY_HINTS_OFF: list(self._hints_off),
        }

    def _done_reason(self, reason: str) -> str:
        return f"{reason}_warning" if self._supervision_failed else reason

    async def async_step_finish(self, user_input: dict | None = None):
        await self._logout()
        if self._reauth:
            return self.async_update_reload_and_abort(self._entry, reason=self._done_reason("reauth_successful"))
        if self._entry is not None:
            return self.async_update_reload_and_abort(
                self._entry, data=self._entry_data(), options=self._entry_options(),
                reason=self._done_reason("reconfigure_successful"),
            )
        return self.async_create_entry(
            title=self._tenant_id, data=self._entry_data(), options=self._entry_options(),
            description="supervision_warning" if self._supervision_failed else None,
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
