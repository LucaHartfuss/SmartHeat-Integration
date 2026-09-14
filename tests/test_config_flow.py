from unittest.mock import AsyncMock

import voluptuous as vol

from custom_components.smartheat.api_client import InvalidAuth
from custom_components.smartheat.const import DOMAIN


async def test_user_step_shows_form_initially(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    assert result["type"] == "form"
    assert result["step_id"] == "user"


async def test_user_step_shows_invalid_auth_error(hass, monkeypatch):
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.login",
        AsyncMock(side_effect=InvalidAuth("nope")),
    )

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "a@b.de", "password": "falsch"},
    )

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_step_proceeds_to_tenant_step_on_success(hass, monkeypatch):
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.login",
        AsyncMock(return_value="tok123"),
    )
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.list_tenants",
        AsyncMock(return_value=[{"tenant_id": "wohnung1", "profile_id": "vaillant_gastherme_heizkoerper"}]),
    )

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "a@b.de", "password": "geheim"},
    )

    assert result["type"] == "form"
    assert result["step_id"] == "tenant"
