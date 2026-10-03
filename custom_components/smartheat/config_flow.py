"""Config-Flow der SmartHeat-Integration: Setup-Wizard 2.0 (Spec TP6) mit Neu konfigurieren und
Reauth (Spec TP7 2.3, 2.4).

Einrichten: user (Vorabpruefung der Add-ons, Login) -> tenant (bei einem Tenant uebersprungen) ->
heating (bei einer Integration uebersprungen) -> system -> rooms -> plant_values ->
notifications -> summary -> setup (Fortschritt: Zugang, Add-on-Optionen, Watchdog/Boot,
Neustart, Warten auf das Status-Event) -> finish.
Neu konfigurieren: derselbe Weg ab Login mit festem Tenant, vorbelegt aus dem Eintrag; statt
provision() nur der Profilwechsel, der Zugang bleibt (provision() nur, wenn das Add-on keinen
vollstaendigen Zugang hat oder der Server inzwischen eine andere Transportart nutzt, Spec AWS-IoT
4.4). Reauth: Login -> setup mit provision(). provision() sendet immer einen CSR; der private
Schluessel entsteht hier und bleibt im Add-on (Spec AWS-IoT 4.1, 5.2). Bei iot_core wird
cloudflared geleert, gestoppt und ohne Watchdog/Boot gelassen (Plan AWS-2, Praez. 1). Der Eintrag
enthaelt keine Zugangsdaten."""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import persistent_notification
from homeassistant.components.hassio import AddonError
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow, section
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.hassio import is_hassio

from . import detection, provisioning, validation
from .addon_control import (
    WAIT_DONE,
    WAIT_FAILED,
    StatusListener,
    async_set_supervision,
    async_update_addon_options,
)
from .api_client import (
    AccessDenied,
    ApiError,
    CannotConnect,
    HeizungsserverClient,
    InvalidAuth,
    InvalidResponse,
    ProfileRejected,
)
from .catalog import IntegrationDescriptor, parse_integrations, verified_profiles
from .const import (
    ADDON_REPOSITORY_URL,
    ADDON_SPECS,
    DEFAULT_HEIZUNGSSERVER_BASE_URL,
    DOMAIN,
    KPI_ENERGY_CHANNELS,
    KPI_ROLE_STATE_CLASS_EXPECTATIONS,
    KPI_SCALAR_ROLE_BY_CAPABILITY,
    MIN_ADDON_VERSIONS,
    OPTION_ABGEMELDET,
    OPTION_ACCOUNTS_API_BASE_URL,
    OPTION_BATTERY_ENTITIES,
    OPTION_ENTITY_ROOM_TARGET,
    OPTION_NOTIFY_HINTS_OFF,
    OPTION_NOTIFY_SERVICES,
    OPTION_ROOM_SENSORS,
    OPTION_SETUP_ID,
    OPTIONAL_PLANT_FIELDS,
    PLANT_FIELDS,
    PLAUSIBLE_RANGES,
    POLL_INTERVAL_MAX_SECONDS,
    REQUIRED_CATALOG_VERSION,
    SETUP_DONE_STATUSES,
    STATUS_KONFIGURATIONSFEHLER,
    STATUS_REGELT,
    STATUS_WAIT_SECONDS,
    STATUS_ZUGANG_ABGELEHNT,
    UNMANAGED_ADDON_OPTIONS,
    WRITE_ROLE_FIELDS,
    entry_incomplete,
    kpi_energy_role,
    setup_notification_id,
)
from .flow_progress import ProgressFlowMixin
from .options_flow import SmartHeatOptionsFlow
from .setup_rollback import (
    ReconfigureSnapshot,
    async_rollback_first_setup,
    async_rollback_reconfigure,
    async_rollback_server_only,
)
from .supervisor_client import (
    AddonNotFoundError,
    AddonOutdatedError,
    AmbiguousAddonMatchError,
    async_get_addon_managers,
    async_resolve_addons,
)
from .texts import async_hint

_LOGGER = logging.getLogger(__name__)

ADVANCED_SECTION = "advanced"
# Beide Karten immer, ohne Vorbelegung bei der Ersteinrichtung: das Verteilsystem bestimmt die
# lokalen Sicherheits-Clamps (Spec TP6, Nutzer-Entscheidung 2026-09-25). Werte = Uebersetzungsschluessel.
VERTEILSYSTEM_OPTIONS = ["fussbodenheizung", "heizkoerper"]
ORIGIN_INTEGRATION = "integration"
ORIGIN_WEATHER = "weather"


class SmartHeatConfigFlow(ProgressFlowMixin, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self) -> None:
        self._token: str | None = None
        self._tenants: list[dict] = []
        self._tenant_id: str | None = None
        # Gesetzt, wenn ein 401 mitten im Ablauf zurueck zum Login zwingt (Hinweis im Formular).
        self._session_expired = False
        self._catalog: dict | None = None
        self._integrations: list[IntegrationDescriptor] = []
        self._integration: IntegrationDescriptor | None = None
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
        # (Integration, Kreis-Schluessel), zu denen _plant/_kpi gehoeren; None = keine Auswahl (AU-003).
        self._plant_binding: tuple[str | None, str] | None = None
        # None = Schritt notifications noch nicht bestaetigt (dann alle Handys vorbelegen).
        self._notify_services: list[str] | None = None
        self._battery_entities: list[str] = []
        # Neu konfigurieren eines vollstaendigen Eintrags: gespeicherte Handys (bleiben waehlbar,
        # auch wenn gerade nicht registriert) und die in den Optionen gewaehlten Batterien (statt
        # der Erkennung); None = Erkennung wie bei der Ersteinrichtung.
        self._stored_notify_services: list[str] = []
        self._stored_battery_entities: list[str] | None = None
        self._hints_off: list[str] = []
        # Zugang dieses Laufs (neu ausgestellt oder aus den Add-ons uebernommen) und die zugehoerigen
        # profile_params; der Schluessel steckt nur im Add-on-Zugang, nie im Eintrag.
        self._access: provisioning.Access | None = None
        self._profile_params: dict | None = None
        # provision() lief in diesem Flow: neuer Zugang (Abschlusstext im Neu konfigurieren) und das
        # dabei ausgestellte Installations-Token fuer den Rueckbau.
        self._new_credentials = False
        self._new_token: str | None = None
        self._setup_id: str | None = None
        self._setup_error = ""
        # Neu konfigurieren/Reauth: der bestehende Eintrag, Tenant fest.
        self._entry: config_entries.ConfigEntry | None = None
        self._reauth = False
        self._system_defaults: dict = {}
        self._status: StatusListener | None = None
        self._supervision_failed = False
        # Letzter Status des Add-ons beim Abschluss (TP12c 3.1), fuer den Abschlusstext.
        self._final_status: str | None = None
        self._final_grund: str | None = None
        # Rueckbau ohne Abschluss (TP12c 3.2): in die Add-ons geschrieben, abgeschlossen, Rueckbau
        # gelaufen; beim Neu konfigurieren der Stand vor dem ersten Schreiben. Aenderungen auf dem
        # Server vor dem ersten Schreiben: _new_credentials (provision) und _profile_changed
        # (update_profile beim Neu konfigurieren).
        self._written = False
        self._profile_changed = False
        self._finished = False
        self._rolled_back = False
        self._snapshot: ReconfigureSnapshot | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> SmartHeatOptionsFlow:
        return SmartHeatOptionsFlow()

    @callback
    def async_remove(self) -> None:
        if self._status is not None:
            self._status.close()
            self._status = None
        if self._needs_rollback() or self._token is not None:
            # Dialog geschlossen oder Flow abgebrochen: Rueckbau/Logout im Hintergrund (TP12c 3.2).
            self.hass.async_create_background_task(self._async_rollback_then_logout(), "smartheat_setup_cleanup")

    def _needs_rollback(self) -> bool:
        changed = self._written or self._new_credentials or self._profile_changed
        return changed and not self._finished and not self._rolled_back and not self._reauth

    async def _async_rollback(self) -> None:
        """Genau einmal (async_remove laeuft bei jedem Flow-Ende); vor dem Logout, weil der
        Profil-Rueckbau das Token braucht. Ohne Schreiben in die Add-ons nur auf dem Server."""
        if not self._needs_rollback():
            return
        self._rolled_back = True
        tenant_id = self._tenant_id
        assert tenant_id is not None  # geaendert wird erst nach der Tenant-Auswahl
        new_token = self._new_token if self._new_credentials else None
        if self._entry is None:
            if self._written:
                # Widerruft ueber die Zugangsdaten in den Optionen der Heizungsbruecke.
                await async_rollback_first_setup(self.hass, tenant_id)
            else:
                await async_rollback_server_only(
                    self.hass, client=self._client(), token=self._token, tenant_id=tenant_id, first_setup=True,
                    new_token=new_token, previous_profile_id=None, profile_id=None,
                )
            return
        snapshot, profile = self._snapshot, self._profile
        # _run_setup setzt den Snapshot vor _obtain_access, also vor jeder Aenderung.
        assert snapshot is not None and profile is not None
        if self._written:
            await async_rollback_reconfigure(
                self.hass, client=self._client(), token=self._token, tenant_id=tenant_id, snapshot=snapshot,
                profile_id=profile["profile_id"], new_token=new_token,
            )
        else:
            await async_rollback_server_only(
                self.hass, client=self._client(), token=self._token, tenant_id=tenant_id, first_setup=False,
                new_token=new_token, previous_profile_id=snapshot.profile_id,
                profile_id=profile["profile_id"],
            )

    async def _abort(self, reason: str, **placeholders: str):
        """Jeder Abbruch nach dem Login meldet die Sitzung ab (AU-036)."""
        await self._logout()
        return self.async_abort(reason=reason, description_placeholders=placeholders or None)

    async def _async_rollback_then_logout(self) -> None:
        """Ein unerwarteter Fehler im Rueckbau darf den Logout nicht verhindern; er wird nur mit
        dem Typnamen protokolliert (der Text koennte Optionswerte zitieren)."""
        try:
            await self._async_rollback()
        except Exception as error:  # noqa: BLE001 -- best effort, der Abbruch laeuft weiter
            _LOGGER.warning("Rueckbau unerwartet fehlgeschlagen: %s", type(error).__name__)
        finally:
            await self._logout()

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
        if entry_incomplete(self._entry.data):
            return self.async_abort(reason="reconfigure_first")
        self._reauth = True
        self._tenant_id = self._entry.data["tenant_id"]
        return await self.async_step_user()

    def _prefill_from_entry(self) -> None:
        """Vorbelegung fuer Neu konfigurieren. Bei `unvollstaendig` nichts: dann gilt die Erkennung
        wie bei der Ersteinrichtung, auch ohne vorbelegtes Verteilsystem."""
        entry = self._entry
        # _prefill_from_entry() laeuft nur aus async_step_reconfigure(), das self._entry vorher
        # setzt (async_step_reauth() ruft es nicht auf).
        assert entry is not None
        if entry_incomplete(entry.data):
            return
        options = entry.options
        self._rooms_input = {
            OPTION_ROOM_SENSORS: [validation.entity_of(ref) for ref in options.get(OPTION_ROOM_SENSORS, [])],
            OPTION_ENTITY_ROOM_TARGET: validation.entity_of(options.get(OPTION_ENTITY_ROOM_TARGET, "")),
        }
        entities = entry.data.get("entities", {})
        plant_fields = PLANT_FIELDS + OPTIONAL_PLANT_FIELDS
        self._plant = {field: entities[field] for field in plant_fields if field in entities}
        self._kpi = {field: value for field, value in entities.items() if field not in plant_fields}
        stored = entry.data.get("circuit") or {}
        self._plant_binding = (
            entry.data.get("integration_domain"),
            f"{stored.get('config_entry_id')}|{stored.get('system_key')}|{stored.get('circuit')}",
        )
        self._notify_services = list(options.get(OPTION_NOTIFY_SERVICES, []))
        self._stored_notify_services = list(self._notify_services)
        if OPTION_BATTERY_ENTITIES in options:
            self._stored_battery_entities = list(options[OPTION_BATTERY_ENTITIES])
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
            except InvalidResponse:
                errors["base"] = "invalid_response"
            except ApiError:
                errors["base"] = "unknown"
            else:
                if not self._tenants:
                    errors["base"] = "no_tenants"
                elif self._entry is None:
                    return await self.async_step_tenant()
                elif self._tenant_id not in {tenant["tenant_id"] for tenant in self._tenants}:
                    return await self._abort("wrong_account")
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
        token = self._token
        if token is None:  # ohne Login nichts abzumelden; best effort, kein Fehler
            return
        try:
            await self._client().logout(token)
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
            try:
                self._abort_if_unique_id_configured()
            except AbortFlow:
                await self._logout()
                raise
            return await self.async_step_heating()
        return self.async_show_form(
            step_id="tenant", data_schema=vol.Schema({vol.Required("tenant_id"): _select(tenant_ids)}),
        )

    # --- Schritt 3: Heizungs-Integration ---

    async def async_step_heating(self, user_input: dict | None = None):
        if self._catalog is None:
            errors: dict[str, str] = {}
            token = self._token
            assert token is not None  # dieser Schritt folgt nur nach erfolgreichem Login
            try:
                self._catalog = await self._client().get_catalog(token)
            except InvalidAuth:
                return await self._session_lost()
            except AccessDenied as error:
                return await self._abort("access_denied", grund=error.reason or "-")
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidResponse:
                errors["base"] = "invalid_response"
            except ApiError:
                errors["base"] = "unknown"
            if errors:
                return self.async_show_form(step_id="heating", data_schema=vol.Schema({}), errors=errors)
            # Kein Fehlerpfad oben griff (sonst waere oben schon zurueckgekehrt worden): self._catalog
            # wurde also gerade erfolgreich gesetzt.
            catalog = self._catalog
            assert catalog is not None
            version = catalog.get("catalog_version")
            if not isinstance(version, int) or version < REQUIRED_CATALOG_VERSION:
                return await self._abort("catalog_outdated")
            descriptors = parse_integrations(catalog)
            entry_domains = {entry.domain for entry in self.hass.config_entries.async_entries()}
            self._integrations = detection.installed_integrations(descriptors, entry_domains)
            if not self._integrations:
                return await self._abort(
                    "no_supported_integration", supported=", ".join(sorted(d.label for d in descriptors)),
                )
            user_input = None
        domains = [descriptor.domain for descriptor in self._integrations]
        if user_input is None and len(domains) == 1:
            user_input = {"integration": domains[0]}
        if user_input is not None:
            self._integration = next(d for d in self._integrations if d.domain == user_input["integration"])
            self._circuits = []
            return await self.async_step_system()
        options: list[selector.SelectOptionDict] = [
            {"value": d.domain, "label": d.label} for d in self._integrations
        ]
        # Vorbelegung, kein Ueberspringen: bei mehreren erkannten Integrationen muss der Kunde
        # beim Neu konfigurieren weiterhin wechseln koennen, auch wenn die im Eintrag gespeicherte
        # Integration noch installiert ist (Spec 2.3: "vorausgefuellt").
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
        integration = self._integration
        catalog = self._catalog
        # Dieser Schritt folgt nur nach der Integrationsauswahl in async_step_heating(), die beide
        # setzt.
        assert integration is not None
        assert catalog is not None
        if not self._circuits:
            self._entries, self._devices = detection.registry_snapshot(self.hass)
            self._circuits = detection.find_circuits(integration, self._entries, self._devices)
            if not self._circuits:
                return await self._abort("no_heating_circuit", integration=integration.label)
        profiles = [p for p in verified_profiles(catalog) if p["hersteller"] == integration.hersteller]
        if not profiles:
            return await self._abort("no_verified_profiles")
        erzeuger_typen = _erzeuger_typen(catalog)

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
            integration, {circuit.config_entry_id for circuit in self._circuits}, self._devices,
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
            description_placeholders={"integration": integration.label},
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
            vol.Required(OPTION_ROOM_SENSORS): validation.entity_selector(OPTION_ROOM_SENSORS, multiple=True),
            vol.Required(OPTION_ENTITY_ROOM_TARGET): validation.entity_selector(OPTION_ENTITY_ROOM_TARGET),
        })
        return self.async_show_form(
            step_id="rooms", data_schema=self.add_suggested_values_to_schema(schema, self._rooms_input), errors=errors,
        )

    # --- Schritt 6: Anlagenwerte ---

    def _binding(self) -> tuple[str, str]:
        """Integration und Kreis, zu denen eine Auswahl im Schritt Anlagenwerte gehoert (AU-003)."""
        integration, circuit = self._integration, self._circuit
        assert integration is not None and circuit is not None  # erst nach async_step_system()
        return integration.domain, circuit.key

    def _suggestions(self) -> tuple[dict[str, str], dict[str, str]]:
        integration = self._integration
        circuit = self._circuit
        # Dieser Schritt folgt nur nach async_step_system(), das beide setzt.
        assert integration is not None
        assert circuit is not None
        found = {
            **circuit.roles,
            **detection.system_role_suggestions(integration, circuit, self._entries),
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
        integration = self._integration
        assert integration is not None  # dieser Schritt folgt nur nach async_step_heating()
        lines = []
        for field in PLANT_FIELDS + OPTIONAL_PLANT_FIELDS:
            role = await self._hint(f"role_{field}")
            origin = origins.get(field)
            if origin == ORIGIN_INTEGRATION:
                line = await self._hint(
                    "origin_integration", role=role, entity=suggestions[field], integration=integration.label,
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
            # Climate-Zone: Wunschtemperatur-Attribut; bei abgeschalteter Zone 0, deshalb nur numerisch.
            "entity_shift_current": validation.check_numeric(
                self.hass, validation.room_target_ref(plant["entity_shift_current"]),
            ),
            "entity_min_flow": validation.check_temperature(self.hass, plant["entity_min_flow"]),
            "entity_heat_limit": validation.check_temperature(
                self.hass, plant["entity_heat_limit"], PLAUSIBLE_RANGES["heat_limit"],
            ),
            "entity_outdoor_temp": validation.check_temperature(
                self.hass, plant["entity_outdoor_temp"], PLAUSIBLE_RANGES["outdoor"],
            ),
        }
        if "entity_flow_setpoint" in plant:
            checks["entity_flow_setpoint"] = validation.check_temperature(self.hass, plant["entity_flow_setpoint"])
        checks = {field: validation.check_domain(plant[field], field) or error for field, error in checks.items()}
        integration, circuit = self._integration, self._circuit
        assert integration is not None and circuit is not None  # erst nach async_step_system()
        for field in WRITE_ROLE_FIELDS:
            if field in plant and not checks.get(field):
                checks[field] = validation.check_installation(
                    self.hass, plant[field], integration.domain, circuit.config_entry_id,
                )
        return {field: error for field, error in checks.items() if error}

    async def async_step_plant_values(self, user_input: dict | None = None):
        profile = self._profile
        room_target = self._room_target
        # Dieser Schritt folgt nur nach async_step_system()/async_step_rooms(), die beide setzen.
        assert profile is not None
        assert room_target is not None
        kpi_fields = _kpi_fields(profile.get("telemetry_capabilities"))
        suggestions, origins = self._suggestions()
        errors: dict[str, str] = {}
        if user_input is not None:
            plant = {field: user_input[field] for field in PLANT_FIELDS}
            plant.update({field: user_input[field] for field in OPTIONAL_PLANT_FIELDS if user_input.get(field)})
            advanced = user_input.get(ADVANCED_SECTION) or {}
            errors = self._check_plant(plant)
            kpi, kpi_errors = _resolve_kpi_entities(self.hass, advanced, kpi_fields)
            errors.update(kpi_errors)
            if not errors:
                duplicates = validation.duplicate_fields({
                    OPTION_ROOM_SENSORS: self._room_sensors, "entity_room_target": [room_target],
                    **{field: [value] for field, value in plant.items()},
                    **{field: [entity_id] for field, entity_id in kpi.items()},
                })
                errors = {field: error for field, error in duplicates.items() if field in plant or field in kpi}
                if validation.zone_is_room_target(plant["entity_shift_current"], room_target):
                    errors["entity_shift_current"] = validation.ERROR_ZONE_IS_ROOM_TARGET
            if any(field in kpi_fields for field in errors):
                errors["base"] = "advanced_invalid"
            if not errors:
                self._plant, self._kpi = plant, kpi
                self._plant_binding = self._binding()
                return await self.async_step_notifications()
            suggested = {
                **{field: user_input.get(field) for field in PLANT_FIELDS + OPTIONAL_PLANT_FIELDS},
                ADVANCED_SECTION: advanced,
            }
        elif self._plant and self._plant_binding == self._binding():
            # Nur fuer dieselbe Integration und denselben Kreis (AU-003); sonst gilt die Erkennung.
            # Zurueck nach setup_failed: die Auswahl des Kunden, nicht erneut die Erkennung (I-1).
            # KPI genau wie gewaehlt: ein bewusst leer gelassenes Feld bleibt leer.
            suggested = {
                **{
                    field: self._plant.get(field) or suggestions.get(field)
                    for field in PLANT_FIELDS + OPTIONAL_PLANT_FIELDS
                },
                ADVANCED_SECTION: {field: self._kpi[field] for field in kpi_fields if field in self._kpi},
            }
        else:
            suggested = {
                **{field: suggestions.get(field) for field in PLANT_FIELDS + OPTIONAL_PLANT_FIELDS},
                ADVANCED_SECTION: {field: suggestions[field] for field in kpi_fields if field in suggestions},
            }
        integration = self._integration
        assert integration is not None  # erst nach async_step_system()

        def _selector(field: str):
            return validation.entity_selector(field, integration=integration.domain if field in WRITE_ROLE_FIELDS else None)

        schema: dict[vol.Marker, Any] = {vol.Required(field): _selector(field) for field in PLANT_FIELDS}
        for field in OPTIONAL_PLANT_FIELDS:
            schema[vol.Optional(field)] = _selector(field)
        if kpi_fields:
            schema[vol.Required(ADVANCED_SECTION)] = section(
                vol.Schema({
                    vol.Optional(field): validation.entity_selector(field) for field in kpi_fields
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
        if self._stored_battery_entities is not None:
            # Neu konfigurieren: die Auswahl aus den Optionen bleibt.
            self._battery_entities = list(self._stored_battery_entities)
        else:
            battery_candidates = [entry.entity_id for entry in self._entries if entry.device_class == "battery"]
            self._battery_entities = detection.battery_entities(
                [validation.entity_of(ref) for ref in [*self._room_sensors, self._room_target]],
                self._entries, detection.unit_map(self.hass, battery_candidates),
            )
        registered = detection.mobile_app_services(self.hass.services.async_services_for_domain("notify"))
        # Gespeicherte, gerade nicht registrierte Handys bleiben waehlbar (wie options_flow._notify_options):
        # sonst fielen sie beim Neu konfigurieren still aus notify_services, und die
        # Waechter-Meldungen verstummten.
        services = [*registered, *[s for s in self._stored_notify_services if s not in registered]]
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
        warnings = validation.collect_warnings(self.hass, refs, self._room_sensors)
        unmatched = self._unmatched_write_roles()
        if unmatched:
            warnings[validation.WARNING_WRITE_ROLE_UNMATCHED] = unmatched
        seconds = self._poll_interval_seconds()
        if seconds is not None and seconds > POLL_INTERVAL_MAX_SECONDS:
            warnings[validation.WARNING_POLL_INTERVAL] = [f"{seconds / 60:g} min"]
        return warnings

    def _unmatched_write_roles(self) -> list[str]:
        """Gebundene Felder, die nicht der Erkennung fuer den gewaehlten Kreis entsprechen (AU-019);
        das optionale Vorlauf-Soll nur, wenn es eine abweichende Erkennung gibt."""
        suggestions, origins = self._suggestions()
        result = []
        for field in WRITE_ROLE_FIELDS:
            chosen = self._plant.get(field)
            detected = suggestions.get(field) if origins.get(field) == ORIGIN_INTEGRATION else None
            if chosen is None or chosen == detected:
                continue
            if field in OPTIONAL_PLANT_FIELDS and detected is None:
                continue
            result.append(chosen)
        return result

    def _entity_label(self, entity_id: str) -> str:
        state = self.hass.states.get(entity_id)
        name = state.name if state is not None else entity_id
        return f"{name} ({entity_id})" if name != entity_id else entity_id

    def _poll_interval_seconds(self) -> float | None:
        integration, circuit = self._integration, self._circuit
        assert integration is not None and circuit is not None
        option = integration.poll_interval_option
        if option is None or circuit.config_entry_id is None:
            return None
        entry = self.hass.config_entries.async_get_entry(circuit.config_entry_id)
        return None if entry is None else option.seconds(entry.options)

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
        if self._keepable_access(bridge_options, cloudflared_options) is not None:
            return ""
        return await self._hint("new_credentials")

    def _server_transport_kind(self) -> str | None:
        """transport_kind aus /accounts/me/tenants (Spec AWS-IoT 4.4); None, wenn der Server keins nennt."""
        for tenant in self._tenants:
            if tenant.get("tenant_id") == self._tenant_id:
                kind = tenant.get("transport_kind")
                return kind if isinstance(kind, str) else None
        return None

    def _keepable_access(self, bridge_options: dict, cloudflared_options: dict) -> provisioning.Access | None:
        """Der laufende Zugang, wenn Neu konfigurieren ihn behalten darf: vollstaendig (also nicht von
        vor AWS-2 und mit Tunnel-Token bei Mosquitto) und von der Transportart, die der Server nennt."""
        access = provisioning.access_from_options(bridge_options, cloudflared_options)
        server_kind = self._server_transport_kind()
        if access is None or (server_kind is not None and access.kind != server_kind):
            return None
        return access

    async def _summary_placeholders(self, warnings: dict[str, list[str]]) -> dict[str, str]:
        room_target = self._room_target
        profile = self._profile
        notify_services = self._notify_services
        # Die Zusammenfassung folgt nur nach Raeumen/System/Benachrichtigungen, die alle drei
        # setzen.
        assert room_target is not None
        assert profile is not None
        assert notify_services is not None

        def _value(ref: str) -> str:
            value = validation.read_value(self.hass, ref)[0]
            return "–" if value is None else f"{value:g}"

        room_values = [v for v in (validation.read_value(self.hass, ref)[0] for ref in self._room_sensors) if v is not None]
        lines = [
            f"- {await self._hint(f'warning_{kind}', entities=', '.join(entities))}"
            for kind, entities in warnings.items()
        ]
        return {
            "room_temperature": f"{sum(room_values) / len(room_values):.1f}" if room_values else "–",
            "room_sensor_count": str(len(self._room_sensors)),
            "room_target": _value(room_target),
            "outdoor_temperature": _value(self._plant["entity_outdoor_temp"]),
            "outdoor_source": self._plant["entity_outdoor_temp"],
            "curve": _value(self._plant["entity_curve_current"]),
            "shift": _value(validation.room_target_ref(self._plant["entity_shift_current"])),
            "min_flow": _value(self._plant["entity_min_flow"]),
            "heat_limit": _value(self._plant["entity_heat_limit"]),
            "write_entities": "\n".join([
                f"- {await self._hint(f'role_{field}')}: {self._entity_label(self._plant[field])}"
                for field in WRITE_ROLE_FIELDS if field in self._plant
            ]),
            "profile": ", ".join([
                profile["hersteller"],
                await self._hint(f"erzeuger_typ_{profile['erzeuger_typ'].lower()}"),
                await self._hint(f"verteilsystem_{profile['verteilsystem'].lower()}"),
            ]),
            "recipients": str(len(notify_services)),
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
        return await self._run_progress("setup", "setup", self._run_setup)

    async def async_step_wait_status(self, user_input: dict | None = None):
        return await self._run_progress("wait_status", "setup", self._wait_for_status)

    async def _progress_error_step(self) -> str:
        self._setup_error = await self._hint("setup_unexpected")
        return "setup_failed"

    async def _run_setup(self) -> str:
        """Liefert den naechsten Schritt: finish, setup_failed, setup_timeout oder user."""
        problem = await self._addon_problem()
        if problem is not None:
            self._setup_error = await self._hint(problem[0], **problem[1])
            return "setup_failed"
        tenant_id = self._tenant_id
        assert tenant_id is not None  # spaetestens aus Tenant-Auswahl/Eintrag gesetzt
        try:
            heizungsbruecke, cloudflared = await async_get_addon_managers(self.hass, ADDON_SPECS)
            if self._entry is not None and not self._reauth and self._snapshot is None:
                # Stand vor dem ERSTEN Schreiben dieses Laufs (auch nach "Zurueck zur Auswahl").
                self._snapshot = ReconfigureSnapshot(
                    dict((await heizungsbruecke.async_get_addon_info()).options),
                    dict((await cloudflared.async_get_addon_info()).options),
                    self._entry.data.get("profile_id"),
                )
            if self._access is None:
                next_step = await self._obtain_access(heizungsbruecke, cloudflared)
                if next_step is not None:
                    return next_step
            self._setup_id = uuid.uuid4().hex
            if self._status is None:
                self._status = StatusListener(self.hass, tenant_id)
            existing = (await heizungsbruecke.async_get_addon_info()).options
            options = self._heizungsbruecke_options(existing)
            # Vor dem Aufruf: auch ein abgebrochener oder gescheiterter Aufruf kann geschrieben haben.
            self._written = True
            await heizungsbruecke.async_set_addon_options(options)
            access = self._access
            assert access is not None  # siehe _heizungsbruecke_options()
            if access.cloudflared is not None:
                await cloudflared.async_set_addon_options(access.cloudflared)
                supervised = [heizungsbruecke.addon_slug, cloudflared.addon_slug]
            else:
                # iot_core (Plan AWS-2, Praez. 1): cloudflared ohne Ziel und Token, gestoppt, ohne Watchdog/Boot.
                await async_update_addon_options(cloudflared, provisioning.CLOUDFLARED_CLEARED_OPTIONS)
                await cloudflared.async_stop_addon()
                await async_set_supervision(self.hass, [cloudflared.addon_slug], enabled=False)
                supervised = [heizungsbruecke.addon_slug]
            failed = await async_set_supervision(self.hass, supervised, enabled=True)
            self._supervision_failed = bool(failed)
            if access.cloudflared is not None:
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
        """Zugang fuer diesen Lauf. Ersteinrichtung und Reauth stellen einen neuen aus (provision);
        Neu konfigurieren behaelt den laufenden und wechselt nur das Profil, solange er vollstaendig
        ist und zur Transportart des Servers passt (Spec AWS-IoT 4.4). None = erledigt, sonst der
        naechste Schritt."""
        if self._entry is not None and not self._reauth:
            bridge_options = (await heizungsbruecke.async_get_addon_info()).options
            cloudflared_options = (await cloudflared.async_get_addon_info()).options
            access = self._keepable_access(bridge_options, cloudflared_options)
            if access is not None:
                return await self._keep_access(access)
        return await self._provision()

    def _expire_session(self) -> str:
        self._token = None
        self._session_expired = True
        return "user"

    async def _provision(self) -> str | None:
        if self._profile is not None:
            profile_id = self._profile["profile_id"]
        else:
            entry = self._entry
            # Ohne self._profile lief kein async_step_system() (Ersteinrichtung/Neu konfigurieren);
            # das ist nur beim Reauth der Fall, das self._entry bereits gesetzt hat.
            assert entry is not None
            profile_id = entry.data["profile_id"]
        token = self._token
        tenant_id = self._tenant_id
        assert token is not None  # dieser Schritt folgt nur nach erfolgreichem Login
        assert tenant_id is not None  # spaetestens aus Tenant-Auswahl/Eintrag gesetzt
        key_pem, csr = await self.hass.async_add_executor_job(provisioning.generate_key_and_csr, tenant_id)
        try:
            body = await self._client().provision(token, tenant_id, profile_id, csr)
        except InvalidAuth:
            return self._expire_session()
        except AccessDenied as error:
            self._setup_error = await self._hint("access_denied", grund=error.reason or "-")
            return "setup_failed"
        except ApiError:
            self._setup_error = await self._hint("provisioning_failed")
            return "setup_failed"
        try:
            access = provisioning.parse_provisioning(body, key_pem)
        except provisioning.InvalidProvisioning as error:
            # Keine Rueckfallwerte: ohne vollstaendige Antwort startet das Add-on nicht (Spec TP3, 4).
            # Der Text nennt nur Schluessel, nie Werte. Ein ausgestelltes, aber unbrauchbares Ergebnis
            # kann der Rueckbau mangels Token nicht widerrufen; reconcile-broker findet es.
            _LOGGER.error("Provisionierungs-Antwort unbrauchbar: %s", error)
            self._setup_error = await self._hint("invalid_provisioning_response")
            return "setup_failed"
        self._access, self._profile_params = access, access.profile_params
        self._new_credentials, self._new_token = True, access.installation_token
        return None

    async def _keep_access(self, access: provisioning.Access) -> str | None:
        """Spec TP7 2.3: Profilwechsel am Server; Zugang und cloudflared-Optionen aus den laufenden
        Add-ons. So bleibt der Rueckweg per Backup-Restore gueltig."""
        token = self._token
        tenant_id = self._tenant_id
        profile = self._profile
        # _keep_access() laeuft nur beim Neu konfigurieren (siehe _obtain_access), das immer durch
        # einen erfolgreichen Login (Token/Tenant) und async_step_system() (setzt self._profile) kam.
        assert token is not None
        assert tenant_id is not None
        assert profile is not None
        # Vor dem Aufruf (wie _written): auch ein abgebrochener Aufruf oder einer ohne gueltige
        # Antwort kann das Profil gewechselt haben. Nur eine klare Ablehnung laesst es unveraendert.
        self._profile_changed = True
        try:
            profile_params = await self._client().update_profile(token, tenant_id, profile["profile_id"])
        except InvalidAuth:
            self._profile_changed = False
            return self._expire_session()
        except AccessDenied as error:
            self._profile_changed = False
            self._setup_error = await self._hint("access_denied", grund=error.reason or "-")
            return "setup_failed"
        except ProfileRejected:
            self._profile_changed = False
            self._setup_error = await self._hint("profile_rejected")
            return "setup_failed"
        except ApiError:
            self._setup_error = await self._hint("profile_update_failed")
            return "setup_failed"
        self._access, self._profile_params = access, profile_params
        return None

    async def _wait_for_status(self) -> str:
        """Nur ein Event mit der setup_id dieses Laufs zaehlt (Spec TP7 2.5)."""
        status = self._status
        assert status is not None  # _run_setup() setzt ihn, bevor dieser Schritt erreichbar ist
        outcome, grund = await status.async_wait(
            setup_id=self._setup_id, done=frozenset(SETUP_DONE_STATUSES),
            failed=frozenset({STATUS_KONFIGURATIONSFEHLER, STATUS_ZUGANG_ABGELEHNT}), timeout=STATUS_WAIT_SECONDS,
        )
        if outcome == WAIT_DONE:
            latest = status.latest or {}
            self._final_status = latest.get("status")
            self._final_grund = latest.get("grund")
            return "finish"
        if outcome == WAIT_FAILED:
            assert grund is not None  # WAIT_FAILED traegt immer einen Text (StatusListener._outcome)
            self._setup_error = grund
            return "setup_failed"
        return "setup_timeout"

    def _heizungsbruecke_options(self, existing: dict) -> dict:
        """Einrichten/Neu konfigurieren: vom Wizard verwaltete Felder vollstaendig neu, nicht
        verwaltete aus den bestehenden Optionen (I3); veraltete Felder (entity_room_actual,
        notify_service, profile) fallen weg. Reauth: nur Zugangsdaten, Laufkennung und
        abgemeldet, alles andere bleibt (Spec TP7 2.4)."""
        # _obtain_access() (via _provision()/_keep_access()) setzt self._access und _profile_params,
        # bevor dieser Schritt in _run_setup() erreicht wird.
        assert self._access is not None and self._profile_params is not None
        access = {
            **provisioning.bridge_access_options(self._access),
            OPTION_SETUP_ID: self._setup_id,
            OPTION_ABGEMELDET: False,
        }
        if self._reauth:
            return {**existing, **access}
        notify_services = self._notify_services
        assert notify_services is not None  # Ersteinrichtung/Neu konfigurieren durchlaufen async_step_notifications()
        options = {key: existing[key] for key in UNMANAGED_ADDON_OPTIONS if key in existing}
        # profile_params zuerst: die festen Schluessel danach kann der Server nicht ueberschreiben.
        options.update(self._profile_params)
        options.update({
            "tenant_id": self._tenant_id,
            **access,
            OPTION_ACCOUNTS_API_BASE_URL: DEFAULT_HEIZUNGSSERVER_BASE_URL,
            OPTION_ROOM_SENSORS: list(self._room_sensors),
            OPTION_NOTIFY_SERVICES: list(notify_services),
            OPTION_BATTERY_ENTITIES: list(self._battery_entities),
            OPTION_NOTIFY_HINTS_OFF: list(self._hints_off),
            OPTION_ENTITY_ROOM_TARGET: self._room_target,
            **self._plant,
            **self._kpi,
        })
        return options

    def _secrets(self) -> set[str]:
        """Alles, was nie angezeigt werden darf: Passwort bzw. privater Schluessel (auch zeilenweise,
        falls der Supervisor ihn umbricht), Installations-Token, Service-Token. Das Zertifikat ist
        oeffentlich und bleibt lesbar."""
        access = self._access
        if access is None:
            return set()
        values = [value for key, value in access.credential.items() if key != "tls_certificate"]
        values.append(access.installation_token)
        if access.cloudflared is not None:
            values += [access.cloudflared["service_token_id"], access.cloudflared["service_token_secret"]]
        secrets: set[str] = set()
        for value in values:
            if isinstance(value, str) and value:
                secrets.add(value)
                secrets.add(value.strip())
                if "\n" in value:
                    secrets.update(line for line in value.splitlines() if len(line) >= 16 and not line.startswith("-----"))
                    secrets.add("".join(value.split()))
        secrets.discard("")
        return secrets

    def _sanitize_addon_error(self, message: str) -> str:
        """Die Supervisor-Meldung nennt oft den abgelehnten Options-Schluessel; Zugangsdaten
        werden vor der Anzeige geschwaerzt (laengste zuerst) und der Text gekuerzt."""
        for secret in sorted(self._secrets(), key=len, reverse=True):
            message = message.replace(secret, "***")
        return message[:300]

    async def async_step_setup_failed(self, user_input: dict | None = None):
        # "Zurueck zur Auswahl" fuehrt zu rooms ohne erneutes provision(): die Zugangsdaten bleiben
        # in self._access. Im Reauth gibt es keine Auswahl.
        menu = ["cancel"] if self._reauth else ["rooms", "cancel"]
        return self.async_show_menu(
            step_id="setup_failed", menu_options=menu, description_placeholders={"grund": self._setup_error},
        )

    async def async_step_setup_timeout(self, user_input: dict | None = None):
        return self.async_show_menu(step_id="setup_timeout", menu_options=["wait_status", "cancel"])

    async def async_step_cancel(self, user_input: dict | None = None):
        await self._async_rollback_then_logout()
        return self.async_abort(reason="setup_cancelled")

    def _entry_data(self) -> dict:
        profile = self._profile
        integration = self._integration
        circuit = self._circuit
        # _entry_data() laeuft nur am Ende des Wizards (async_step_finish), wenn System/Raeume/
        # Anlagenwerte bereits durchlaufen sind und alle drei gesetzt haben.
        assert profile is not None
        assert integration is not None
        assert circuit is not None
        return {
            "tenant_id": self._tenant_id,
            "profile_id": profile["profile_id"],
            "integration_domain": integration.domain,
            "circuit": {
                "config_entry_id": circuit.config_entry_id, "system_key": circuit.system_key, "circuit": circuit.circuit,
            },
            "entities": {**self._plant, **self._kpi},
        }

    def _entry_options(self) -> dict:
        notify_services = self._notify_services
        assert notify_services is not None  # siehe _entry_data(): laeuft nur nach Benachrichtigungen
        return {
            OPTION_ROOM_SENSORS: list(self._room_sensors),
            OPTION_ENTITY_ROOM_TARGET: self._room_target,
            OPTION_NOTIFY_SERVICES: list(notify_services),
            OPTION_BATTERY_ENTITIES: list(self._battery_entities),
            OPTION_NOTIFY_HINTS_OFF: list(self._hints_off),
        }

    async def _notes(self) -> str:
        """Hinweise zum Abschluss: Warnzustand des Add-ons und/oder nicht gesetzter Watchdog."""
        lines = []
        if self._final_status not in (None, STATUS_REGELT):
            lines.append(await self._hint(f"done_status_{self._final_status}", grund=self._final_grund or "-"))
        if self._supervision_failed:
            lines.append(await self._hint("done_supervision"))
        return "\n\n".join(lines)

    async def async_step_finish(self, user_input: dict | None = None):
        self._finished = True
        tenant_id = self._tenant_id
        assert tenant_id is not None  # spaetestens aus Tenant-Auswahl/Eintrag gesetzt
        # Die Rueckbau-Meldung eines frueher abgebrochenen Laufs ist mit diesem Abschluss erledigt.
        persistent_notification.async_dismiss(self.hass, setup_notification_id(tenant_id))
        await self._logout()
        notes = await self._notes()
        entry = self._entry
        if entry is not None:
            if self._reauth:
                reason = "reauth_successful"
            else:
                # Nach dem provision()-Rueckfall (Add-on ohne Zugangsdaten) nicht "gleich geblieben" melden.
                reason = "reconfigure_successful_new_credentials" if self._new_credentials else "reconfigure_successful"
                self.hass.config_entries.async_update_entry(entry, data=self._entry_data(), options=self._entry_options())
            # Wie async_update_reload_and_abort, aber mit Platzhaltern fuer die Hinweise.
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_abort(reason=reason, description_placeholders={"notes": f"\n\n{notes}" if notes else ""})
        return self.async_create_entry(
            title=tenant_id, data=self._entry_data(), options=self._entry_options(),
            description="setup_notes" if notes else None,
            description_placeholders={"notes": notes} if notes else None,
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
        if error := validation.check_domain(entity_id, field):
            errors[field] = error
            continue
        if field != "entity_operating_mode":
            expected = KPI_ROLE_STATE_CLASS_EXPECTATIONS.get(field, "total_increasing")
            if state.attributes.get("state_class") != expected:
                errors[field] = f"state_class_expected_{expected}"
                continue
        resolved[field] = entity_id
    return resolved, errors
