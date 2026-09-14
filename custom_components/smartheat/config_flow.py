"""Config-Flow fuer die SmartHeat-Integration."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_client import ApiError, CannotConnect, HeizungsserverClient, InvalidAuth
from .const import DEFAULT_HEIZUNGSSERVER_BASE_URL, DOMAIN


class SmartHeatConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._token: str | None = None
        self._tenants: list[dict] = []
        self._tenant_id: str | None = None

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
        if user_input is not None:
            self._tenant_id = user_input["tenant_id"]
            return self.async_show_form(step_id="tenant", data_schema=vol.Schema({}))

        return self.async_show_form(
            step_id="tenant",
            data_schema=vol.Schema({
                vol.Required("tenant_id"): vol.In(
                    {t["tenant_id"]: t["tenant_id"] for t in self._tenants}
                ),
            }),
        )
