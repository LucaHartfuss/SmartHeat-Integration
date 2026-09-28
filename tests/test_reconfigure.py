"""Neu konfigurieren und Reauth (Spec TP7 2.3, 2.4)."""
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartheat.const import DOMAIN

from .addon_fakes import make_entry, status_event
from .flow_helpers import (
    BRIDGE_OPTIONS, CF_OPTIONS, CURVE, MQTT_PASSWORD, CF_SECRET, PLANT_INPUT, PROFILE_PARAMS, PROVISIONING, ROOMS_INPUT,
    SYSTEM_INPUT, TENANT, configure, enable_supervisor, fast_status_wait, finish_progress, has_default, login,
    marker, mock_addons, mock_server, register_phones, setup_mypyllant, setup_rooms, suggested,
)


def _prepare(hass, monkeypatch, *, tenants=(TENANT,), **addons):
    enable_supervisor(hass, monkeypatch)
    mocks = mock_server(monkeypatch, tenants=tenants)
    mypyllant = setup_mypyllant(hass)
    setup_rooms(hass)
    register_phones(hass, "mobile_app_pixel")
    fast_status_wait(monkeypatch)
    calls = mock_addons(hass, monkeypatch, **addons)
    return mypyllant, mocks, calls


async def _through_the_wizard(hass, result, rooms=ROOMS_INPUT):
    for data in (SYSTEM_INPUT, rooms, PLANT_INPUT):
        result = await configure(hass, result, data)
    return await configure(hass, result, {"notify_services": suggested(result, "notify_services")})


async def test_reconfigure_keeps_the_credentials_and_changes_only_the_profile(hass, monkeypatch):
    mypyllant, mocks, calls = _prepare(
        hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS,
    )
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)

    result = await login(hass, await entry.start_reconfigure_flow(hass))

    assert result["step_id"] == "system"
    assert marker(result, "verteilsystem").default() == "heizkoerper"
    result = await configure(hass, result, SYSTEM_INPUT)
    assert suggested(result, "room_sensors") == ["sensor.wz_temperatur"]
    assert suggested(result, "entity_room_target") == "climate.wz"
    result = await configure(hass, result, ROOMS_INPUT)
    assert suggested(result, "entity_curve_current") == CURVE
    result = await configure(hass, result, PLANT_INPUT)
    assert suggested(result, "notify_services") == ["notify.mobile_app_pixel"]
    result = await configure(hass, result, {"notify_services": ["notify.mobile_app_pixel"]})
    assert result["step_id"] == "summary"
    assert result["description_placeholders"]["credentials_note"] == ""

    result = await finish_progress(hass, await configure(hass, result, {}))
    await hass.async_block_till_done()

    assert (result["type"], result["reason"]) == ("abort", "reconfigure_successful")
    mocks.provision.assert_not_awaited()
    mocks.update_profile.assert_awaited_once_with("tok123", TENANT, "vaillant_gastherme_heizkoerper")
    options = calls.options["heizungsbruecke"]
    assert (options["mqtt_username"], options["mqtt_password"], options["abgemeldet"]) == (
        "wohnung1_alt", "alt-geheim", False,
    )
    assert options["local_check_interval_seconds"] == 120
    assert calls.options["cloudflared_access_mqtt"] == CF_OPTIONS
    assert entry.options["room_sensors"] == ["sensor.wz_temperatur", "sensor.kz_temperatur"]
    assert entry.data["integration_domain"] == "mypyllant"
    assert [slug for _, slug, _ in calls.supervision] == ["heizungsbruecke", "cloudflared_access_mqtt"]


async def test_reconfigure_without_credentials_in_the_addon_issues_new_ones(hass, monkeypatch):
    mypyllant, mocks, calls = _prepare(
        hass, monkeypatch, existing_options={**BRIDGE_OPTIONS, "mqtt_username": "", "mqtt_password": "", "abgemeldet": True},
        cloudflared_options={**CF_OPTIONS, "service_token_id": "", "service_token_secret": ""},
    )
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)

    result = await _through_the_wizard(hass, await login(hass, await entry.start_reconfigure_flow(hass)))

    assert result["step_id"] == "summary"
    assert result["description_placeholders"]["credentials_note"] == "New access credentials will be issued."
    result = await finish_progress(hass, await configure(hass, result, {}))
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    mocks.provision.assert_awaited_once_with("tok123", TENANT, "vaillant_gastherme_heizkoerper")
    mocks.update_profile.assert_not_awaited()
    options = calls.options["heizungsbruecke"]
    assert (options["mqtt_password"], options["abgemeldet"]) == (MQTT_PASSWORD, False)
    assert calls.options["cloudflared_access_mqtt"]["service_token_secret"] == CF_SECRET


async def test_reconfigure_of_an_incomplete_entry_uses_the_detection_and_clears_the_repair_issue(hass, monkeypatch):
    """client1-Migration (Spec TP7 7, Schritt 4.3)."""
    mypyllant, mocks, calls = _prepare(
        hass, monkeypatch,
        existing_options={**BRIDGE_OPTIONS, "entity_room_actual": "sensor.alt", "room_sensors": None},
        cloudflared_options=CF_OPTIONS,
    )
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, unique_id=TENANT, title=TENANT, options={},
        data={"tenant_id": TENANT, "profile_id": "vaillant_gastherme_heizkoerper", "unvollstaendig": True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"complete_setup_{entry.entry_id}") is not None

    result = await login(hass, await entry.start_reconfigure_flow(hass))
    assert result["step_id"] == "system"
    assert has_default(result, "verteilsystem") is False
    result = await configure(hass, result, SYSTEM_INPUT)
    assert suggested(result, "room_sensors") is None
    for data in (ROOMS_INPUT, PLANT_INPUT):
        result = await configure(hass, result, data)
    result = await configure(hass, result, {"notify_services": suggested(result, "notify_services")})
    result = await finish_progress(hass, await configure(hass, result, {}))
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    mocks.provision.assert_not_awaited()
    assert "unvollstaendig" not in entry.data
    assert entry.data["circuit"]["config_entry_id"] == mypyllant.entry_id
    assert "entity_room_actual" not in calls.options["heizungsbruecke"]
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"complete_setup_{entry.entry_id}") is None


async def test_reconfigure_with_an_account_without_the_tenant_aborts(hass, monkeypatch):
    mypyllant, _, _ = _prepare(hass, monkeypatch, tenants=("andere",))
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)

    result = await login(hass, await entry.start_reconfigure_flow(hass))

    assert (result["type"], result["reason"]) == ("abort", "wrong_account")


async def test_reauth_issues_new_credentials_and_keeps_everything_else(hass, monkeypatch):
    mypyllant, mocks, calls = _prepare(
        hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS,
    )
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "user"
    result = await finish_progress(hass, await login(hass, result))
    await hass.async_block_till_done()

    assert (result["type"], result["reason"]) == ("abort", "reauth_successful")
    mocks.provision.assert_awaited_once_with("tok123", TENANT, "vaillant_gastherme_heizkoerper")
    options = calls.options["heizungsbruecke"]
    assert options == {
        **BRIDGE_OPTIONS, "mqtt_username": PROVISIONING["username"], "mqtt_password": MQTT_PASSWORD,
        "setup_id": options["setup_id"], "abgemeldet": False,
    }
    assert calls.options["cloudflared_access_mqtt"]["service_token_secret"] == CF_SECRET
    mocks.logout.assert_awaited_once_with("tok123")
    assert [slug for _, slug, _ in calls.supervision] == ["heizungsbruecke", "cloudflared_access_mqtt"]


async def test_reauth_of_an_incomplete_entry_asks_for_reconfigure(hass, monkeypatch):
    _prepare(hass, monkeypatch)
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, unique_id=TENANT, options={},
        data={"tenant_id": TENANT, "profile_id": "p", "unvollstaendig": True},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)

    assert (result["type"], result["reason"]) == ("abort", "reconfigure_first")


async def test_zugang_abgelehnt_event_starts_a_reauth_flow(hass, monkeypatch):
    mypyllant, _, _ = _prepare(hass, monkeypatch)
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)

    hass.bus.async_fire("smartheat_status", status_event(TENANT, "zugang_abgelehnt"))
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]
