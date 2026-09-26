"""Config-Flow fuer die SmartHeat-Integration."""
from __future__ import annotations

import logging
import os

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.hassio import AddonError
from homeassistant.helpers import selector, translation
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.hassio import is_hassio

from .api_client import ApiError, CannotConnect, HeizungsserverClient, InvalidAuth
from .const import (
    CLIMATE_ATTRIBUTE_BY_ROLE, CLOUDFLARED_ADDON_SLUG, DEFAULT_HEIZUNGSSERVER_BASE_URL, DOMAIN,
    ERZEUGER_TYP_LABELS, HEIZUNGSBRUECKE_ADDON_SLUG, KPI_ENERGY_CHANNELS,
    KPI_ROLE_STATE_CLASS_EXPECTATIONS,
    KPI_SCALAR_ROLE_BY_CAPABILITY, ROLE_DOMAINS, ROLE_UNIT_EXPECTATIONS,
    VERTEILSYSTEM_LABELS, kpi_energy_role,
)
from .supervisor_client import AddonNotFoundError, AmbiguousAddonMatchError, async_get_addon_managers

_LOGGER = logging.getLogger(__name__)


class SmartHeatConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._token: str | None = None
        self._tenants: list[dict] = []
        self._tenant_id: str | None = None
        self._profiles: list[dict] = []
        self._profile_id: str | None = None
        self._entities: dict[str, str] = {}
        self._telemetry_capabilities: dict | None = None
        self._kpi_entities: dict[str, str] = {}
        self._provisioning: dict | None = None
        self._retry_error_detail: str = ""
        # Final-Review-Fix (Finding 2): gesetzt, wenn ein InvalidAuth mitten in der Flow
        # (Tenant- oder Finish-Schritt) zurueck zum Login zwingt, damit async_step_user
        # beim naechsten Rendern erklaeren kann, warum der Nutzer ploetzlich wieder ganz
        # vorne steht, statt es wie einen kommentarlosen Reset aussehen zu lassen.
        self._session_expired: bool = False

    def _client(self) -> HeizungsserverClient:
        return HeizungsserverClient(async_get_clientsession(self.hass), DEFAULT_HEIZUNGSSERVER_BASE_URL)

    async def async_step_user(self, user_input: dict | None = None):
        # Diese Integration provisioniert echte MQTT-Zugangsdaten und schreibt sie in
        # zwei Add-ons per Supervisor-API -- das funktioniert nur auf einer Supervisor-
        # Installation (HA OS/Supervised). Muss VOR allem anderen (inkl. der ersten
        # Login-Form) geprueft werden, fail fast, damit ein Nutzer auf einer
        # Nicht-Supervisor-Installation nicht erst den ganzen Login-/Tenant-/Profil-/
        # Entities-Flow durchlaeuft (und provision() dabei bereits Live-Credentials
        # ausstellt), bevor _push_config_and_finish scheitert. is_hassio(hass) ist HA's
        # eigener, kanonischer Supervisor-Detection-Helper (prueft "hassio" in
        # hass.config.components, verifiziert gegen .venv/.../homeassistant/helpers/
        # hassio.py); der zusaetzliche rohe os.environ-Check bleibt als Verteidigung in
        # der Tiefe, falls hassio zwar geladen ist, der Token aber (z.B. in einem
        # kaputten Testsetup) fehlt -- AddonManager selbst faellt in diesem Fall nicht
        # mit einem KeyError um (holt sich os.environ.get(..., "") intern), sondern
        # scheitert kontrolliert mit AddonError beim ersten echten Supervisor-Aufruf,
        # was _push_config_and_finish ohnehin abfaengt; dieser fruehe Guard ist also
        # reine UX (schneller, klarer Abbruch statt eines spaeten "provisioning_failed").
        if not is_hassio(self.hass) or "SUPERVISOR_TOKEN" not in os.environ:
            return self.async_abort(reason="not_supervisor")

        errors: dict[str, str] = {}
        if user_input is not None:
            # Ein neuer Login-Versuch loest den session_expired-Hinweis unabhaengig vom
            # Ausgang ab -- entweder er gelingt (Hinweis nicht mehr relevant), oder er
            # scheitert an etwas Konkretem (invalid_auth/cannot_connect/unknown unten),
            # was Vorrang vor der alten "Sitzung abgelaufen"-Meldung hat.
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
            data_schema=vol.Schema({
                vol.Required("email"): str,
                vol.Required("password"): str,
            }),
            errors=errors,
        )

    async def async_step_tenant(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            self._tenant_id = user_input["tenant_id"]
            # Single-Instance-Guard: verhindert, dass die Flow zweimal fuer dieselbe
            # Anlage durchlaufen wird und zwei Config-Entries entstehen, die beide
            # dieselben zwei Add-ons beanspruchen (Loeschen einer der beiden konfiguriert
            # dann nichts zurueck). Fail fast hier, vor dem API-Call.
            await self.async_set_unique_id(self._tenant_id)
            self._abort_if_unique_id_configured()
            try:
                self._profiles = (await self._client().get_catalog(self._token))["profiles"]
            except InvalidAuth:
                self._token = None
                self._session_expired = True
                return await self.async_step_user()
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except ApiError:
                errors["base"] = "unknown"
            else:
                return await self.async_step_profile()

        tenant_ids = sorted({t["tenant_id"] for t in self._tenants})
        return self.async_show_form(
            step_id="tenant",
            data_schema=vol.Schema({vol.Required("tenant_id"): _select(tenant_ids)}),
            errors=errors,
        )

    async def async_step_profile(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        verified = [p for p in self._profiles if p["verified"]]

        if not verified:
            return self.async_abort(reason="no_verified_profiles")

        if user_input is not None:
            match = _match_profile(
                verified, user_input["hersteller"], user_input["erzeuger_typ"], user_input["verteilsystem"]
            )
            if match is None:
                errors["base"] = "profile_combination_unsupported"
            else:
                self._profile_id = match["profile_id"]
                self._telemetry_capabilities = match.get("telemetry_capabilities")
                return await self.async_step_entities()

        herstellers = sorted({p["hersteller"] for p in verified})
        erzeuger_typen = sorted({p["erzeuger_typ"] for p in verified})
        verteilsysteme = sorted({p["verteilsystem"] for p in verified})
        return self.async_show_form(
            step_id="profile",
            data_schema=vol.Schema({
                vol.Required("hersteller"): _select(herstellers),
                vol.Required("erzeuger_typ"): _select(erzeuger_typen, ERZEUGER_TYP_LABELS),
                vol.Required("verteilsystem"): _select(verteilsysteme, VERTEILSYSTEM_LABELS),
            }),
            errors=errors,
        )

    async def async_step_entities(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            resolved, errors = _resolve_entities(self.hass, user_input)
            if not errors:
                self._entities = resolved
                return await self.async_step_kpi_metrics()

        return self.async_show_form(step_id="entities", data_schema=_entities_schema(), errors=errors)

    async def async_step_kpi_metrics(self, user_input: dict | None = None):
        """Optionaler Schritt: zeigt nur die KPI-Mappings, die das Profil unterstuetzt.
        Ohne (oder mit leeren) telemetry_capabilities entfaellt er komplett."""
        schema_fields = _kpi_metrics_schema_fields(self._telemetry_capabilities)
        if not schema_fields:
            self._kpi_entities = {}
            return await self.async_step_finish()

        errors: dict[str, str] = {}
        if user_input is not None:
            resolved, errors = _resolve_kpi_entities(self.hass, user_input, schema_fields)
            if not errors:
                self._kpi_entities = resolved
                return await self.async_step_finish()

        return self.async_show_form(
            step_id="kpi_metrics", data_schema=vol.Schema(schema_fields), errors=errors
        )

    async def async_step_finish(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        try:
            self._provisioning = await self._client().provision(
                self._token, self._tenant_id, self._profile_id
            )
        except InvalidAuth:
            self._token = None
            self._session_expired = True
            return await self.async_step_user()
        except ApiError:
            errors["base"] = "provisioning_failed"
            return self.async_show_form(
                step_id="entities", data_schema=_entities_schema(), errors=errors
            )

        return await self._push_config_and_finish()

    async def _push_config_and_finish(self):
        # Der Guard in async_step_user hat is_hassio(hass)/SUPERVISOR_TOKEN bereits als
        # vorhanden geprueft, bevor diese Methode (nach erfolgreichem provision()) je
        # erreicht werden kann -- AddonManager holt sich seinen eigenen Supervisor-Client
        # (get_supervisor_client(hass)) intern, kein manueller Token-Zugriff mehr hier.
        heizungsbruecke_options = {
            "tenant_id": self._tenant_id,
            "profile": self._profile_id,
            "mqtt_username": self._provisioning["username"],
            "mqtt_password": self._provisioning["password"],
            **self._entities,
            **self._kpi_entities,
        }
        cloudflared_options = {
            "hostname": self._provisioning["cloudflared_hostname"],
            "local_port": self._provisioning["cloudflared_local_port"],
            "service_token_id": self._provisioning["cloudflared_service_token_id"],
            "service_token_secret": self._provisioning["cloudflared_service_token_secret"],
        }
        try:
            heizungsbruecke, cloudflared = await async_get_addon_managers(
                self.hass,
                [
                    ("Heizungsbruecke", HEIZUNGSBRUECKE_ADDON_SLUG),
                    ("Cloudflared Access TCP-Bridge", CLOUDFLARED_ADDON_SLUG),
                ],
            )
            await heizungsbruecke.async_set_addon_options(heizungsbruecke_options)
            await cloudflared.async_set_addon_options(cloudflared_options)
            await cloudflared.async_restart_addon()
            await heizungsbruecke.async_restart_addon()
        except AmbiguousAddonMatchError:
            # Anders als die beiden generischen Faelle unten muss der Nutzer hier etwas
            # Konkretes tun (doppelte/veraltete Add-on-Installation im Supervisor entfernen),
            # nicht nur "erneut versuchen" -- daher ein eigener, spezifischer Hinweistext statt
            # des generischen (siehe supervisor_client.py's AmbiguousAddonMatchError-Docstring,
            # der diesen Hinweis bisher nirgends in der UI zeigte).
            #
            # Final-Review-Fix (Finding 1): dieser Text landet unveraendert als
            # description_placeholders["error_detail"] im Formular -- description_placeholders
            # werden von HA NIE selbst lokalisiert (siehe data_entry_flow.py: reine
            # Mapping[str, str]-Werte, straight durchgereicht), nur die umgebende
            # Step-Description (aus strings.json/translations/*.json) wird passend zur
            # hass.config.language gewaehlt. Ein Python-Literal hier ist also IMMER in
            # derselben Sprache, egal welche UI-Sprache eingestellt ist -- das war exakt der
            # Bug (deutscher Text auch in der englischen UI). Fix: den Text selbst aus den
            # Uebersetzungsdateien nachschlagen (translation.async_get_translations), genau
            # der Mechanismus, den HA intern fuer Config-Flow-Strings verwendet, nur eben
            # explizit von uns aufgerufen statt implizit vom Frontend.
            translations = await translation.async_get_translations(
                self.hass, self.hass.config.language, "config", integrations=[DOMAIN]
            )
            self._retry_error_detail = translations.get(
                f"component.{DOMAIN}.config.retry_push_hints.ambiguous_addon_match", ""
            )
            return self._show_retry_push_form()
        except AddonNotFoundError:
            self._retry_error_detail = ""
            return self._show_retry_push_form()
        except AddonError as err:
            # Die Supervisor-Meldung nennt typischerweise den abgelehnten Options-KEY
            # (z.B. ein Add-on < 0.13.0 lehnt entity_flow_temperature ab). Sicherheitsnetz:
            # Zugangsdaten werden vor der Anzeige geschwaerzt und der Text gekuerzt.
            self._retry_error_detail = self._sanitize_addon_error(str(err))
            return self._show_retry_push_form()

        return self.async_create_entry(
            title=self._tenant_id,
            data={"tenant_id": self._tenant_id, "profile_id": self._profile_id},
        )

    def _sanitize_addon_error(self, message: str) -> str:
        secrets = [
            self._provisioning.get("password"),
            self._provisioning.get("cloudflared_service_token_secret"),
            self._provisioning.get("cloudflared_service_token_id"),
        ]
        for secret in secrets:
            if secret:
                message = message.replace(str(secret), "***")
        return message[:300]

    def _show_retry_push_form(self):
        return self.async_show_form(
            step_id="retry_push",
            data_schema=vol.Schema({}),
            description_placeholders={
                "mqtt_username": self._provisioning["username"],
                "mqtt_password": self._provisioning["password"],
                "error_detail": self._retry_error_detail,
            },
        )

    async def async_step_retry_push(self, user_input: dict | None = None):
        if user_input is not None:
            return await self._push_config_and_finish()

        return self._show_retry_push_form()


def _match_profile(
    verified_profiles: list[dict], hersteller: str, erzeuger_typ: str, verteilsystem: str
) -> dict | None:
    """Loest die drei einzeln gewaehlten Profildimensionen zu einem konkreten,
    verifizierten Server-Profil auf. Nicht jede Kombination hat ein Profil (z.B.
    Vaillant+Waermepumpe existiert aktuell nicht) -- in diesem Fall None, der Aufrufer
    zeigt dann "profile_combination_unsupported" an, statt serverseitig ungueltige
    profile_ids zu raten oder still ein falsches Profil zu waehlen.
    """
    for profile in verified_profiles:
        if (
            profile["hersteller"] == hersteller
            and profile["erzeuger_typ"] == erzeuger_typ
            and profile["verteilsystem"] == verteilsystem
        ):
            return profile
    return None


def _select(values: list[str], labels: dict[str, str] | None = None) -> selector.SelectSelector:
    """Baut einen echten HA-SelectSelector (dieselbe Komponente wie der Entity-Picker im
    entities-Schritt) statt eines rohen vol.In() -- optional mit menschenlesbaren Labels
    pro Wert (z.B. 'Waermepumpe' -> 'Wärmepumpe'), waehrend der uebermittelte Wert der
    unveraenderte, zum Server passende Rohwert bleibt.
    """
    options = (
        [{"value": v, "label": labels.get(v, v)} for v in values] if labels else list(values)
    )
    return selector.selector({"select": {"options": options, "mode": "dropdown"}})


def _entities_schema() -> vol.Schema:
    return vol.Schema({
        vol.Required(role): selector.selector({"entity": {"domain": domains}})
        for role, domains in ROLE_DOMAINS.items()
    })


_WARNED_UNKNOWN_CHANNELS: set[str] = set()


def _kpi_metrics_schema_fields(telemetry_capabilities: dict | None) -> dict:
    if not telemetry_capabilities:
        return {}
    fields: dict = {}
    for capability_flag, role in KPI_SCALAR_ROLE_BY_CAPABILITY.items():
        if telemetry_capabilities.get(capability_flag):
            fields[vol.Optional(role)] = selector.selector({"entity": {"domain": ["sensor"]}})
    for channel in telemetry_capabilities.get("energy_channels") or []:
        if channel not in KPI_ENERGY_CHANNELS:
            if channel not in _WARNED_UNKNOWN_CHANNELS:
                _WARNED_UNKNOWN_CHANNELS.add(channel)
                _LOGGER.warning("Ignoring unknown energy channel %r from server profile", channel)
            continue
        fields[vol.Optional(kpi_energy_role(channel))] = selector.selector(
            {"entity": {"domain": ["sensor"]}}
        )
    return fields


def _resolve_kpi_entities(
    hass, user_input: dict, schema_fields: dict
) -> tuple[dict[str, str], dict[str, str]]:
    """Validiert nur die neuen KPI-Rollen (state_class); die Rollen des entities-Schritts
    und deren Einheitenpruefung bleiben unberuehrt."""
    resolved: dict[str, str] = {}
    errors: dict[str, str] = {}
    for role in (str(key) for key in schema_fields):
        entity_id = user_input.get(role)
        if not entity_id:
            continue
        state = hass.states.get(entity_id)
        if state is None:
            errors[role] = "entity_not_found"
            continue
        if role != "entity_operating_mode":
            expected = KPI_ROLE_STATE_CLASS_EXPECTATIONS.get(role, "total_increasing")
            if state.attributes.get("state_class") != expected:
                errors[role] = f"state_class_expected_{expected}"
                continue
        resolved[role] = entity_id
    return resolved, errors


def _resolve_entities(hass, user_input: dict) -> tuple[dict[str, str], dict[str, str]]:
    """Validiert und normalisiert die vom Nutzer gewaehlten Entities -- portiert 1:1 aus
    dem alten heizungsbruecke/web.py's complete()-Handler und wizard.js's
    CLIMATE_ATTRIBUTE_BY_ROLE-Flattening (siehe const.py fuer die Herkunft der Konstanten).
    """
    resolved: dict[str, str] = {}
    errors: dict[str, str] = {}
    for role in ROLE_DOMAINS:
        entity_id = user_input[role]
        state = hass.states.get(entity_id)
        if state is None:
            errors[role] = "entity_not_found"
            continue
        if entity_id.startswith("climate.") and role in CLIMATE_ATTRIBUTE_BY_ROLE:
            resolved[role] = f"{entity_id}::{CLIMATE_ATTRIBUTE_BY_ROLE[role]}"
            continue
        expected_unit = ROLE_UNIT_EXPECTATIONS.get(role)
        if expected_unit is not None:
            actual_unit = state.attributes.get("unit_of_measurement")
            if actual_unit != expected_unit:
                errors[role] = "unit_mismatch"
                continue
        resolved[role] = entity_id
    return resolved, errors
