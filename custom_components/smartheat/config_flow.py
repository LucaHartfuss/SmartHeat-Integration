"""Config-Flow fuer die SmartHeat-Integration."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_client import ApiError, CannotConnect, HeizungsserverClient, InvalidAuth
from .const import (
    CLIMATE_ATTRIBUTE_BY_ROLE, DEFAULT_HEIZUNGSSERVER_BASE_URL, DOMAIN,
    ROLE_DOMAINS, ROLE_UNIT_EXPECTATIONS,
)


class SmartHeatConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._token: str | None = None
        self._tenants: list[dict] = []
        self._tenant_id: str | None = None
        self._profiles: list[dict] = []
        self._profile_id: str | None = None
        self._entities: dict[str, str] = {}

    def _client(self) -> HeizungsserverClient:
        return HeizungsserverClient(async_get_clientsession(self.hass), DEFAULT_HEIZUNGSSERVER_BASE_URL)

    async def async_step_user(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
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
            try:
                self._profiles = await self._client().list_profiles(self._token)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except ApiError:
                errors["base"] = "unknown"
            else:
                return await self.async_step_profile()

        return self.async_show_form(
            step_id="tenant",
            data_schema=vol.Schema({
                vol.Required("tenant_id"): vol.In(
                    {t["tenant_id"]: t["tenant_id"] for t in self._tenants}
                ),
            }),
            errors=errors,
        )

    async def async_step_profile(self, user_input: dict | None = None):
        if user_input is not None:
            self._profile_id = user_input["profile_id"]
            return await self.async_step_entities()

        verified = [p for p in self._profiles if p["verified"]]
        return self.async_show_form(
            step_id="profile",
            data_schema=vol.Schema({
                vol.Required("profile_id"): vol.In({
                    p["profile_id"]: f"{p['hersteller']} {p['erzeuger_typ']} ({p['verteilsystem']})"
                    for p in verified
                }),
            }),
        )

    async def async_step_entities(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            resolved, errors = _resolve_entities(self.hass, user_input)
            if not errors:
                self._entities = resolved
                return await self.async_step_finish()

        schema = vol.Schema({
            vol.Required(role): selector.selector({"entity": {"domain": domains}})
            for role, domains in ROLE_DOMAINS.items()
        })
        return self.async_show_form(step_id="entities", data_schema=schema, errors=errors)

    async def async_step_finish(self, user_input: dict | None = None):
        return self.async_create_entry(title=self._tenant_id, data={"tenant_id": self._tenant_id})


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
