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


async def _reach_profile_step(hass, monkeypatch):
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.login",
        AsyncMock(return_value="tok123"),
    )
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.list_tenants",
        AsyncMock(return_value=[{"tenant_id": "wohnung1", "profile_id": "vaillant_gastherme_heizkoerper"}]),
    )
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.list_profiles",
        AsyncMock(return_value=[
            {"hersteller": "Vaillant", "erzeuger_typ": "Gastherme", "verteilsystem": "Heizkoerper",
             "profile_id": "vaillant_gastherme_heizkoerper", "verified": True},
            {"hersteller": "Weishaupt", "erzeuger_typ": "Waermepumpe", "verteilsystem": "Fussbodenheizung",
             "profile_id": "weishaupt_waermepumpe_fussbodenheizung", "verified": False},
        ]),
    )
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "a@b.de", "password": "geheim"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"tenant_id": "wohnung1"},
    )
    return result


async def test_tenant_step_proceeds_to_profile_step(hass, monkeypatch):
    result = await _reach_profile_step(hass, monkeypatch)

    assert result["type"] == "form"
    assert result["step_id"] == "profile"


async def test_profile_step_only_offers_verified_profiles(hass, monkeypatch):
    result = await _reach_profile_step(hass, monkeypatch)

    (validator,) = [
        v for k, v in result["data_schema"].schema.items() if str(k) == "profile_id"
    ]
    # vol.In() speichert die uebergebenen Choices auf .container -- direkter Beweis,
    # dass nur das verifizierte Profil (nicht auch weishaupt_..., verified=False) im
    # Formular waehlbar ist.
    assert set(validator.container) == {"vaillant_gastherme_heizkoerper"}


async def test_profile_step_proceeds_to_entities_step(hass, monkeypatch):
    result = await _reach_profile_step(hass, monkeypatch)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"profile_id": "vaillant_gastherme_heizkoerper"},
    )

    assert result["type"] == "form"
    assert result["step_id"] == "entities"


async def test_entities_step_rejects_unit_mismatch(hass, monkeypatch):
    hass.states.async_set("sensor.rt", "20.0", {"unit_of_measurement": "K"})
    hass.states.async_set("sensor.target_rt", "20.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.outdoor", "5.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("number.curve", "0.5", {})
    hass.states.async_set("number.offset", "25.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("number.heat_limit", "15.0", {"unit_of_measurement": "°C"})
    result = await _reach_profile_step(hass, monkeypatch)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"profile_id": "vaillant_gastherme_heizkoerper"},
    )

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "entity_room_actual": "sensor.rt",
        "entity_room_target": "sensor.target_rt",
        "entity_outdoor_temp": "sensor.outdoor",
        "entity_curve_current": "number.curve",
        "entity_offset_current": "number.offset",
        "entity_heat_limit": "number.heat_limit",
    })

    assert result["type"] == "form"
    assert result["step_id"] == "entities"
    assert result["errors"]["entity_room_actual"] == "unit_mismatch"


async def test_entities_step_flattens_climate_room_target():
    from custom_components.smartheat.config_flow import _resolve_entities
    from unittest.mock import MagicMock

    hass = MagicMock()
    def _get(entity_id):
        if entity_id == "climate.wohnzimmer":
            return MagicMock(attributes={})
        return MagicMock(attributes={"unit_of_measurement": "°C"})
    hass.states.get.side_effect = _get

    resolved, errors = _resolve_entities(hass, {
        "entity_room_actual": "sensor.rt",
        "entity_room_target": "climate.wohnzimmer",
        "entity_outdoor_temp": "sensor.outdoor",
        "entity_curve_current": "number.curve",
        "entity_offset_current": "number.offset",
        "entity_heat_limit": "number.heat_limit",
    })

    assert errors == {}
    assert resolved["entity_room_target"] == "climate.wohnzimmer::temperature"
