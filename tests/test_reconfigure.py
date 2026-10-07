"""Neu konfigurieren und Reauth (Spec TP7 2.3, 2.4)."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

from aiohasupervisor.exceptions import SupervisorError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartheat import provisioning, setup_rollback
from custom_components.smartheat.api_client import ApiError, InvalidAuth, ProfileRejected
from custom_components.smartheat.const import DOMAIN, entry_incomplete
from custom_components.smartheat.texts import async_hint

from .addon_fakes import make_entry, status_event
from .flow_helpers import (
    BRIDGE_OPTIONS,
    CATALOG,
    CF_OPTIONS,
    CF_SECRET,
    CURVE,
    FLOW,
    FLOW_SETPOINT,
    INSTALLATION_TOKEN,
    IOT_PROVISIONING,
    MIN_FLOW,
    MOSQUITTO_TRANSPORT,
    MQTT_PASSWORD,
    PLANT_INPUT,
    PROVISIONING,
    ROOMS_INPUT,
    SYSTEM_INPUT,
    SYSTEM_INPUT_WP,
    TENANT,
    ZONE,
    _default,
    configure,
    enable_supervisor,
    fail_addon_reads_after,
    fast_status_wait,
    finish_progress,
    has_default,
    login,
    marker,
    mock_addons,
    mock_server,
    register_phones,
    setup_mypyllant,
    setup_rooms,
    setup_weishaupt,
    suggested,
    weishaupt_catalog,
)


def _prepare(hass, monkeypatch, *, tenants=(TENANT,), server=None, **addons):
    enable_supervisor(hass, monkeypatch)
    mocks = mock_server(monkeypatch, tenants=tenants, **(server or {}))
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


async def test_reconfigure_with_notbetrieb_is_done_with_notes(hass, monkeypatch):
    mypyllant, mocks, calls = _prepare(
        hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS, status="notbetrieb",
    )
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)
    result = await _through_the_wizard(hass, await login(hass, await entry.start_reconfigure_flow(hass)))
    result = await finish_progress(hass, await configure(hass, result, {}))
    await hass.async_block_till_done()
    assert (result["type"], result["reason"]) == ("abort", "reconfigure_successful")
    assert result["description_placeholders"]["notes"].strip() == await async_hint(hass, "done_status_notbetrieb", grund="-")


async def test_reconfigure_with_failed_watchdog_names_it_in_the_notes(hass, monkeypatch):
    mypyllant, mocks, calls = _prepare(
        hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS,
        supervision_error=SupervisorError("weg"),
    )
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)
    result = await _through_the_wizard(hass, await login(hass, await entry.start_reconfigure_flow(hass)))
    result = await finish_progress(hass, await configure(hass, result, {}))
    await hass.async_block_till_done()
    assert (result["type"], result["reason"]) == ("abort", "reconfigure_successful")
    assert result["description_placeholders"]["notes"] == "\n\n" + await async_hint(hass, "done_supervision")


async def test_reconfigure_from_a_07x_entry(hass, monkeypatch):
    """client1-Weg (TP11): Eintrag und Add-on-Optionen aus 0.7.x mit `entity_offset_current` und den
    Mittelungsfenstern. Neu konfigurieren schreibt nur noch die neuen Rollen, keine Altschluessel."""
    legacy_options = {
        **BRIDGE_OPTIONS, "entity_offset_current": MIN_FLOW,
        "day_avg_window_start": "10:00", "day_avg_window_end": "16:00",
        "night_avg_window_start": "22:00", "night_avg_window_end": "06:00",
    }
    mypyllant, _, calls = _prepare(
        hass, monkeypatch, existing_options=legacy_options, cloudflared_options=CF_OPTIONS,
    )
    entities = {
        "entity_curve_current": CURVE, "entity_offset_current": MIN_FLOW,
        "entity_heat_limit": "number.zuhause_circuit_0_heat_limit",
        "entity_outdoor_temp": "sensor.zuhause_outdoor_temperature",
    }
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "entities": entities})

    result = await login(hass, await entry.start_reconfigure_flow(hass))
    result = await configure(hass, result, SYSTEM_INPUT)
    result = await configure(hass, result, ROOMS_INPUT)
    assert suggested(result, "entity_shift_current") == ZONE
    assert suggested(result, "entity_min_flow") == MIN_FLOW
    result = await configure(hass, result, PLANT_INPUT)
    result = await configure(hass, result, {"notify_services": suggested(result, "notify_services")})
    result = await finish_progress(hass, await configure(hass, result, {}))
    await hass.async_block_till_done()

    assert (result["type"], result["reason"]) == ("abort", "reconfigure_successful")
    options = calls.options["heizungsbruecke"]
    assert (options["entity_shift_current"], options["entity_min_flow"], options["entity_flow_setpoint"]) == (
        ZONE, MIN_FLOW, FLOW_SETPOINT,
    )
    assert "entity_offset_current" not in options
    assert not [key for key in options if key.startswith(("day_avg_", "night_avg_"))]
    assert "entity_offset_current" not in entry.data["entities"]
    assert entry.data["entities"]["entity_shift_current"] == ZONE


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

    assert result["reason"] == "reconfigure_successful_new_credentials"
    mocks.provision.assert_awaited_once()
    assert mocks.provision.call_args.args[:3] == ("tok123", TENANT, "vaillant_gastherme_heizkoerper")
    mocks.update_profile.assert_not_awaited()
    options = calls.options["heizungsbruecke"]
    assert (options["mqtt_password"], options["abgemeldet"]) == (MQTT_PASSWORD, False)
    assert calls.options["cloudflared_access_mqtt"]["service_token_secret"] == CF_SECRET


async def _reconfigure(hass, monkeypatch, *, server=None, **addons):
    mypyllant, mocks, calls = _prepare(hass, monkeypatch, server=server, **addons)
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)
    result = await _through_the_wizard(hass, await login(hass, await entry.start_reconfigure_flow(hass)))
    assert result["step_id"] == "summary"
    note = result["description_placeholders"]["credentials_note"]
    result = await finish_progress(hass, await configure(hass, result, {}))
    await hass.async_block_till_done()
    return result, mocks, calls, note


async def test_reconfigure_keeps_a_complete_access_and_only_changes_the_profile(hass, monkeypatch):
    result, server, calls, note = await _reconfigure(
        hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS,
    )

    assert (result["reason"], note) == ("reconfigure_successful", "")
    server.provision.assert_not_called()
    options = calls.options["heizungsbruecke"]
    assert options["installation_token"] == "alt-token"
    assert json.loads(options["transport"]) == MOSQUITTO_TRANSPORT
    assert calls.options["cloudflared_access_mqtt"] == CF_OPTIONS and calls.stops == []


async def test_reconfigure_with_a_pre_aws2_configuration_provisions_again(hass, monkeypatch):
    old = {key: value for key, value in BRIDGE_OPTIONS.items()
           if key not in ("transport", "installation_token", "tls_certificate", "tls_private_key")}

    result, server, calls, note = await _reconfigure(
        hass, monkeypatch, existing_options=old, cloudflared_options=CF_OPTIONS,
    )

    assert result["reason"] == "reconfigure_successful_new_credentials"
    assert note == "New access credentials will be issued."
    server.provision.assert_awaited_once()
    options = calls.options["heizungsbruecke"]
    assert options["installation_token"] == INSTALLATION_TOKEN
    assert json.loads(options["transport"]) == MOSQUITTO_TRANSPORT
    assert options["mqtt_password"] == MQTT_PASSWORD


async def test_reconfigure_provisions_again_when_the_server_changed_the_transport(hass, monkeypatch):
    result, server, calls, note = await _reconfigure(
        hass, monkeypatch, server={"provisioning": IOT_PROVISIONING, "transport_kind": "iot_core"},
        existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS,
    )

    assert result["reason"] == "reconfigure_successful_new_credentials" and note
    server.provision.assert_awaited_once()
    options = calls.options["heizungsbruecke"]
    assert json.loads(options["transport"])["kind"] == "iot_core"
    assert options["mqtt_username"] == "" and options["mqtt_password"] == ""
    assert options["tls_private_key"].startswith("-----BEGIN PRIVATE KEY-----")
    assert "cloudflared_access_mqtt" in calls.stops and "cloudflared_access_mqtt" not in calls.restarts
    assert calls.options["cloudflared_access_mqtt"]["service_token_secret"] == ""
    assert _supervision_by_slug(calls)["cloudflared_access_mqtt"] == {"boot": "manual", "watchdog": False}


async def test_reconfigure_keeps_a_complete_iot_core_access_and_leaves_cloudflared_stopped(hass, monkeypatch):
    access = provisioning.parse_provisioning(IOT_PROVISIONING, "KEY-PEM")
    iot_options = {**BRIDGE_OPTIONS, **provisioning.bridge_access_options(access)}
    cleared = {**CF_OPTIONS, **provisioning.CLOUDFLARED_CLEARED_OPTIONS}

    result, server, calls, note = await _reconfigure(
        hass, monkeypatch, server={"provisioning": IOT_PROVISIONING, "transport_kind": "iot_core"},
        existing_options=iot_options, cloudflared_options=cleared,
    )

    assert (result["reason"], note) == ("reconfigure_successful", "")
    server.provision.assert_not_called()
    assert calls.options["heizungsbruecke"]["tls_private_key"] == "KEY-PEM"
    assert calls.options["heizungsbruecke"]["installation_token"] == INSTALLATION_TOKEN
    assert "cloudflared_access_mqtt" in calls.stops and "cloudflared_access_mqtt" not in calls.restarts


async def test_reconfigure_keeps_the_access_when_the_server_names_no_transport(hass, monkeypatch):
    """Aeltere Server melden kein transport_kind: dann gilt der laufende Zugang."""
    result, server, calls, _ = await _reconfigure(
        hass, monkeypatch, server={"transport_kind": None}, existing_options=BRIDGE_OPTIONS,
        cloudflared_options=CF_OPTIONS,
    )

    assert result["reason"] == "reconfigure_successful"
    server.provision.assert_not_called()


def _supervision_by_slug(calls) -> dict:
    return {slug: options for _, slug, options in calls.supervision}


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
    mypyllant, mocks, _ = _prepare(hass, monkeypatch, tenants=("andere",))
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)

    result = await login(hass, await entry.start_reconfigure_flow(hass))

    assert (result["type"], result["reason"]) == ("abort", "wrong_account")
    # Fund 3, Fix-Runde 1: die erfolgreiche Sitzung nicht offen lassen, obwohl der Flow abbricht.
    mocks.logout.assert_awaited_once_with("tok123")


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
    mocks.provision.assert_awaited_once()
    assert mocks.provision.call_args.args[:3] == ("tok123", TENANT, "vaillant_gastherme_heizkoerper")
    options = calls.options["heizungsbruecke"]
    assert options == {
        **BRIDGE_OPTIONS, "mqtt_username": PROVISIONING["credential"]["username"], "mqtt_password": MQTT_PASSWORD,
        "installation_token": INSTALLATION_TOKEN, "setup_id": options["setup_id"], "abgemeldet": False,
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


async def test_reauth_of_an_entry_without_a_current_plant_field_asks_for_reconfigure(hass, monkeypatch):
    _prepare(hass, monkeypatch)
    entry = make_entry(hass)
    entities = {k: v for k, v in entry.data["entities"].items() if k != "entity_shift_current"}
    hass.config_entries.async_update_entry(entry, data={**entry.data, "entities": entities})

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


async def test_reauth_with_an_account_without_the_tenant_aborts(hass, monkeypatch):
    """Fund 3/4e, Fix-Runde 1: wrong_account gilt auch auf dem Reauth-Weg, mit Logout."""
    mypyllant, mocks, _ = _prepare(hass, monkeypatch, tenants=("andere",))
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)

    result = await login(hass, await entry.start_reauth_flow(hass))

    assert (result["type"], result["reason"]) == ("abort", "wrong_account")
    mocks.logout.assert_awaited_once_with("tok123")


# --- Fix-Runde 1 (Review von a05371d) ---

async def test_reconfigure_with_two_integrations_shows_the_heating_form_prefilled(hass, monkeypatch):
    """Ruling 1: kein automatisches Ueberspringen bei mehreren erkannten Integrationen, auch wenn
    die gespeicherte Integration noch installiert ist -- nur vorbelegt (Spec 2.3: "vorausgefuellt")."""
    catalog = {**CATALOG, "integrations": CATALOG["integrations"] + [
        {**CATALOG["integrations"][0], "domain": "andere", "label": "Andere Heizung"},
    ]}
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch, catalog=catalog)
    mypyllant = setup_mypyllant(hass)
    setup_rooms(hass)
    MockConfigEntry(domain="andere").add_to_hass(hass)
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)

    result = await login(hass, await entry.start_reconfigure_flow(hass))

    assert result["step_id"] == "heating"
    assert marker(result, "integration").default() == "mypyllant"
    result = await configure(hass, result, {"integration": "mypyllant"})
    assert result["step_id"] == "system"


async def test_reconfigure_with_missing_cloudflared_credentials_issues_new_ones(hass, monkeypatch):
    """Ruling 2: fehlende Tunnel-Token zaehlen wie fehlende MQTT-Zugangsdaten -> provision(), nicht
    leere Token aus _keep_access."""
    mypyllant, mocks, calls = _prepare(
        hass, monkeypatch, existing_options=BRIDGE_OPTIONS,
        cloudflared_options={**CF_OPTIONS, "service_token_id": "", "service_token_secret": ""},
    )
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)

    result = await _through_the_wizard(hass, await login(hass, await entry.start_reconfigure_flow(hass)))
    assert result["step_id"] == "summary"
    assert result["description_placeholders"]["credentials_note"] == "New access credentials will be issued."
    result = await finish_progress(hass, await configure(hass, result, {}))
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful_new_credentials"
    mocks.provision.assert_awaited_once()
    assert mocks.provision.call_args.args[:3] == ("tok123", TENANT, "vaillant_gastherme_heizkoerper")
    mocks.update_profile.assert_not_awaited()
    assert calls.options["heizungsbruecke"]["mqtt_password"] == MQTT_PASSWORD
    assert calls.options["cloudflared_access_mqtt"]["service_token_secret"] == CF_SECRET


async def test_reauth_setup_failure_menu_offers_only_cancel(hass, monkeypatch):
    """Praez. 19: kein 'Zurueck zur Auswahl' im Reauth, dort gibt es keine Auswahl."""
    mypyllant, _, _ = _prepare(hass, monkeypatch, status="konfigurationsfehler", grund="x")
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)

    result = await finish_progress(hass, await login(hass, await entry.start_reauth_flow(hass)))

    assert (result["type"], result["step_id"]) == ("menu", "setup_failed")
    assert result["menu_options"] == ["cancel"]


async def test_status_listener_is_unsubscribed_after_a_successful_reconfigure(hass, monkeypatch):
    """Der Flow meldet seinen eigenen StatusListener beim Ende ab (async_remove); nur der
    dauerhafte Listener des (neu geladenen) Coordinators darf uebrig bleiben, sonst bliebe ein
    toter Listener je abgeschlossenem Flow zurueck."""
    mypyllant, mocks, calls = _prepare(
        hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS,
    )
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)
    baseline = hass.bus.async_listeners().get("smartheat_status", 0)

    result = await _through_the_wizard(hass, await login(hass, await entry.start_reconfigure_flow(hass)))
    result = await finish_progress(hass, await configure(hass, result, {}))
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    # +1: der Coordinator des (durch async_update_reload_and_abort neu geladenen) Eintrags
    # meldet sich dauerhaft an; genau ein Listener, nicht zwei, beweist, dass der Flow seinen
    # eigenen StatusListener wieder abgemeldet hat.
    assert hass.bus.async_listeners().get("smartheat_status", 0) == baseline + 1


async def test_reconfigure_profile_update_failure_is_a_setup_failure(hass, monkeypatch):
    mypyllant, mocks, _ = _prepare(
        hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS,
    )
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)
    mocks.update_profile.side_effect = ApiError("500")

    result = await _through_the_wizard(hass, await login(hass, await entry.start_reconfigure_flow(hass)))
    result = await finish_progress(hass, await configure(hass, result, {}))

    assert result["step_id"] == "setup_failed"
    assert result["description_placeholders"]["grund"]


async def test_profile_rejected_is_a_setup_failure_with_its_own_text(hass, monkeypatch):
    mypyllant, mocks, _ = _prepare(
        hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS,
    )
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)
    mocks.update_profile.side_effect = ProfileRejected("400")

    result = await _through_the_wizard(hass, await login(hass, await entry.start_reconfigure_flow(hass)))
    result = await finish_progress(hass, await configure(hass, result, {}))

    assert result["step_id"] == "setup_failed"
    assert result["description_placeholders"]["grund"] == await async_hint(hass, "profile_rejected")


async def test_reconfigure_session_expiry_during_profile_update_goes_back_to_login(hass, monkeypatch):
    mypyllant, mocks, _ = _prepare(
        hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS,
    )
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)
    mocks.update_profile.side_effect = InvalidAuth("abgelaufen")

    result = await _through_the_wizard(hass, await login(hass, await entry.start_reconfigure_flow(hass)))
    result = await finish_progress(hass, await configure(hass, result, {}))

    assert (result["step_id"], result["errors"]) == ("user", {"base": "session_expired"})


async def test_reconfigure_keeps_the_battery_selection_from_the_options(hass, monkeypatch):
    """Final-Review M2: eine in den Optionen gewaehlte Batterieliste darf "Neu konfigurieren"
    nicht durch die Erkennung ersetzen."""
    mypyllant, _, calls = _prepare(hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS)
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id, options={
        "room_sensors": ["sensor.wz_temperatur"], "entity_room_target": "climate.wz::temperature",
        "notify_services": ["notify.mobile_app_pixel"], "battery_entities": ["sensor.kz_batterie"],
        "notify_hints_off": [],
    })

    result = await _through_the_wizard(hass, await login(hass, await entry.start_reconfigure_flow(hass)))
    assert result["description_placeholders"]["batteries"] == "sensor.kz_batterie"
    result = await finish_progress(hass, await configure(hass, result, {}))
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert entry.options["battery_entities"] == ["sensor.kz_batterie"]
    assert calls.options["heizungsbruecke"]["battery_entities"] == ["sensor.kz_batterie"]


async def test_reconfigure_keeps_a_stored_phone_that_is_not_registered_right_now(hass, monkeypatch):
    """Final-Review M2 (wie der Options-Flow-Fix aus Task 16): ein gespeichertes, gerade nicht
    registriertes Handy bleibt waehlbar und vorbelegt, sonst verstummten die Waechter-Meldungen."""
    mypyllant, _, calls = _prepare(hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS)
    stored = ["notify.mobile_app_pixel", "notify.mobile_app_tablet"]
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id, options={
        "room_sensors": ["sensor.wz_temperatur"], "entity_room_target": "climate.wz::temperature",
        "notify_services": stored, "battery_entities": ["sensor.wz_batterie"], "notify_hints_off": [],
    })

    result = await login(hass, await entry.start_reconfigure_flow(hass))
    for data in (SYSTEM_INPUT, ROOMS_INPUT, PLANT_INPUT):
        result = await configure(hass, result, data)
    assert result["step_id"] == "notifications"
    assert suggested(result, "notify_services") == stored
    result = await configure(hass, result, {"notify_services": stored})
    result = await finish_progress(hass, await configure(hass, result, {}))
    await hass.async_block_till_done()

    assert entry.options["notify_services"] == stored
    assert calls.options["heizungsbruecke"]["notify_services"] == stored


async def test_reconfigure_keeps_stored_phones_when_none_is_registered(hass, monkeypatch):
    mypyllant, _, _ = _prepare(hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS)
    hass.services.async_remove("notify", "mobile_app_pixel")
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)

    result = await _through_the_wizard(hass, await login(hass, await entry.start_reconfigure_flow(hass)))
    result = await finish_progress(hass, await configure(hass, result, {}))
    await hass.async_block_till_done()

    assert entry.options["notify_services"] == ["notify.mobile_app_pixel"]


# --- Rueckbau bei Abbruch (TP12c 3.2) ---

async def test_reconfigure_cancel_restores_through_the_rollback(hass, monkeypatch, rollback):
    mypyllant, mocks, calls = _prepare(hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS,
                                       status="konfigurationsfehler", grund="x")
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)
    result = await _through_the_wizard(hass, await login(hass, await entry.start_reconfigure_flow(hass)))
    result = await finish_progress(hass, await configure(hass, result, {}))

    await configure(hass, result, {"next_step_id": "cancel"})
    await hass.async_block_till_done()

    kwargs = rollback.reconfigure.await_args.kwargs
    assert kwargs["snapshot"].bridge_options == BRIDGE_OPTIONS
    assert kwargs["snapshot"].cloudflared_options == CF_OPTIONS
    assert kwargs["snapshot"].profile_id == "vaillant_gastherme_heizkoerper"
    assert (kwargs["token"], kwargs["keep_access"]) == ("tok123", None)
    rollback.reconfigure.assert_awaited_once()
    mocks.logout.assert_awaited_once()


async def test_reconfigure_rollback_restores_the_state_before_the_first_attempt(hass, monkeypatch, rollback):
    """Review Focus 2: Zurueck zur Auswahl, zweiter Versuch, dann Abbrechen."""
    mypyllant, _, calls = _prepare(hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS,
                                   status="konfigurationsfehler", grund="x")
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)
    result = await _through_the_wizard(hass, await login(hass, await entry.start_reconfigure_flow(hass)))
    result = await finish_progress(hass, await configure(hass, result, {}))
    # Das Add-on liefert ab jetzt die im ersten Versuch geschriebenen Optionen.
    written = calls.options["heizungsbruecke"]
    monkeypatch.setattr(
        "homeassistant.components.hassio.AddonManager.async_get_addon_info",
        AsyncMock(return_value=SimpleNamespace(options=dict(written))),
    )
    result = await configure(hass, result, {"next_step_id": "rooms"})
    for data in (ROOMS_INPUT, PLANT_INPUT, {"notify_services": ["notify.mobile_app_pixel"]}):
        result = await configure(hass, result, data)
    result = await finish_progress(hass, await configure(hass, result, {}))
    assert result["step_id"] == "setup_failed"

    await configure(hass, result, {"next_step_id": "cancel"})
    await hass.async_block_till_done()

    assert reconfigure_snapshot(rollback).bridge_options == BRIDGE_OPTIONS


async def test_reconfigure_with_new_credentials_hands_them_to_the_rollback(hass, monkeypatch, rollback):
    mypyllant, _, _ = _prepare(hass, monkeypatch, existing_options={"tenant_id": TENANT}, cloudflared_options={},
                               status="konfigurationsfehler", grund="x")
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)
    result = await _through_the_wizard(hass, await login(hass, await entry.start_reconfigure_flow(hass)))
    result = await finish_progress(hass, await configure(hass, result, {}))

    await configure(hass, result, {"next_step_id": "cancel"})
    await hass.async_block_till_done()

    assert rollback.reconfigure.await_args.kwargs["keep_access"].installation_token == INSTALLATION_TOKEN


async def test_reconfigure_aborted_before_writing_keeps_the_new_credentials(hass, monkeypatch, rollback):
    """A4-13 (E7): provision() lief, vor dem Schreiben abgebrochen -> voller Rueckbau mit dem neuen Zugang
    (nicht server_only, das ihn widerrufen wuerde)."""
    mypyllant, mocks, calls = _prepare(hass, monkeypatch, existing_options={"tenant_id": TENANT},
                                       cloudflared_options={})
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)
    result = await _through_the_wizard(hass, await login(hass, await entry.start_reconfigure_flow(hass)))
    fail_addon_reads_after(monkeypatch, mocks.provision)
    result = await finish_progress(hass, await configure(hass, result, {}))
    assert result["step_id"] == "setup_failed"
    mocks.provision.assert_awaited_once()
    assert calls.options == {}  # nichts geschrieben

    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done()

    rollback.reconfigure.assert_awaited_once()
    kwargs = rollback.reconfigure.await_args.kwargs
    assert kwargs["keep_access"].installation_token == INSTALLATION_TOKEN
    assert kwargs["snapshot"].profile_id == "vaillant_gastherme_heizkoerper"
    rollback.server_only.assert_not_awaited()
    mocks.delete_installation.assert_not_awaited()
    mocks.logout.assert_awaited_once()


async def test_closing_the_dialog_after_a_failed_reconfigure_rolls_back_once(hass, monkeypatch, rollback):
    mypyllant, mocks, _ = _prepare(hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS,
                                   status="konfigurationsfehler", grund="x")
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)
    result = await _through_the_wizard(hass, await login(hass, await entry.start_reconfigure_flow(hass)))
    result = await finish_progress(hass, await configure(hass, result, {}))
    assert result["step_id"] == "setup_failed"

    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done()

    assert reconfigure_snapshot(rollback).bridge_options == BRIDGE_OPTIONS
    assert rollback.reconfigure.await_args.kwargs["token"] == "tok123"  # Rueckbau vor dem Logout
    mocks.logout.assert_awaited_once()


async def test_reauth_cancel_is_not_rolled_back(hass, monkeypatch, rollback):
    mypyllant, _, _ = _prepare(hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS,
                               status="konfigurationsfehler", grund="x")
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)
    result = await finish_progress(hass, await login(hass, await entry.start_reauth_flow(hass)))
    assert result["step_id"] == "setup_failed"

    await configure(hass, result, {"next_step_id": "cancel"})
    await hass.async_block_till_done()

    rollback.first.assert_not_awaited()
    rollback.reconfigure.assert_not_awaited()


def _entry_with_old_profile(hass, mypyllant):
    """Eintrag mit einem anderen Profil als dem, das der Wizard waehlt: der Profilwechsel aendert den Server."""
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "profile_id": "altes_profil"})
    return entry


async def test_reconfigure_cancel_after_the_profile_change_resets_only_the_server(hass, monkeypatch, rollback):
    """F4 (finale Review): update_profile lief, die Add-ons wurden nicht beschrieben. Echter Rueckbau:
    Profil zuruecksetzen (vor dem Logout), keine Add-on-Optionen, kein Neustart."""
    monkeypatch.setattr(f"{FLOW}.async_rollback_reconfigure", setup_rollback.async_rollback_reconfigure)
    monkeypatch.setattr(f"{FLOW}.async_rollback_server_only", setup_rollback.async_rollback_server_only)
    mypyllant, mocks, calls = _prepare(hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS)
    entry = _entry_with_old_profile(hass, mypyllant)
    result = await _through_the_wizard(hass, await login(hass, await entry.start_reconfigure_flow(hass)))
    fail_addon_reads_after(monkeypatch, mocks.update_profile)
    result = await finish_progress(hass, await configure(hass, result, {}))
    assert result["step_id"] == "setup_failed"

    await configure(hass, result, {"next_step_id": "cancel"})
    await hass.async_block_till_done()

    assert mocks.update_profile.await_args_list == [
        call("tok123", TENANT, "vaillant_gastherme_heizkoerper"), call("tok123", TENANT, "altes_profil"),
    ]
    mocks.delete_installation.assert_not_awaited()
    assert (calls.options, calls.restarts, calls.supervision) == ({}, [], [])
    mocks.logout.assert_awaited_once()


async def test_closing_the_dialog_during_the_profile_change_resets_the_profile(hass, monkeypatch, rollback):
    """Das Profil gilt schon waehrend des Aufrufs als geaendert: ein Abbruch mitten darin wird zurueckgenommen."""
    mypyllant, mocks, calls = _prepare(hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS)
    entry = _entry_with_old_profile(hass, mypyllant)
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_update(*args):
        entered.set()
        await release.wait()

    mocks.update_profile.side_effect = slow_update
    result = await _through_the_wizard(hass, await login(hass, await entry.start_reconfigure_flow(hass)))
    result = await configure(hass, result, {})
    await asyncio.wait_for(entered.wait(), 5)

    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done()

    rollback.server_only.assert_awaited_once()
    kwargs = rollback.server_only.await_args.kwargs
    assert (kwargs["first_setup"], kwargs["previous_profile_id"], kwargs["profile_id"], kwargs["token"]) == (
        False, "altes_profil", "vaillant_gastherme_heizkoerper", "tok123",
    )
    assert kwargs["new_token"] is None
    rollback.reconfigure.assert_not_awaited()
    assert calls.options == {}


async def test_rejected_profile_change_is_not_rolled_back(hass, monkeypatch, rollback):
    mypyllant, mocks, _ = _prepare(hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS)
    entry = _entry_with_old_profile(hass, mypyllant)
    mocks.update_profile.side_effect = ProfileRejected("400")
    result = await _through_the_wizard(hass, await login(hass, await entry.start_reconfigure_flow(hass)))
    result = await finish_progress(hass, await configure(hass, result, {}))
    assert result["step_id"] == "setup_failed"

    await configure(hass, result, {"next_step_id": "cancel"})
    await hass.async_block_till_done()

    rollback.server_only.assert_not_awaited()
    rollback.reconfigure.assert_not_awaited()
    mocks.logout.assert_awaited_once()


async def test_reconfigure_finish_dismisses_a_leftover_rollback_notification(hass, monkeypatch):
    dismissed = []
    monkeypatch.setattr(
        "homeassistant.components.persistent_notification.async_dismiss",
        lambda hass, notification_id: dismissed.append(notification_id),
    )
    mypyllant, _, _ = _prepare(hass, monkeypatch, existing_options=BRIDGE_OPTIONS, cloudflared_options=CF_OPTIONS)
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id)
    result = await _through_the_wizard(hass, await login(hass, await entry.start_reconfigure_flow(hass)))

    result = await finish_progress(hass, await configure(hass, result, {}))
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert "smartheat_wohnung1_setup" in dismissed


# --- Hebelsatz (Plan 3c, Review Focus 1 und 3) ---

async def test_client1_entry_without_lever_set_is_incomplete_and_reconfigure_completes_it(hass, monkeypatch):
    # Eintrag wie client1 vor dem Update: alle Vaillant-Felder, aber kein lever_set/shift_lever.
    addon_before_update = {key: value for key, value in BRIDGE_OPTIONS.items() if key != "lever_set"}
    mypyllant, mocks, calls = _prepare(
        hass, monkeypatch, existing_options=addon_before_update, cloudflared_options=CF_OPTIONS,
    )
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id, data_without=("lever_set", "shift_lever"))
    assert entry_incomplete(entry.data)

    result = await login(hass, await entry.start_reconfigure_flow(hass))
    assert result["step_id"] == "system"
    assert has_default(result, "verteilsystem") is False       # unvollstaendig: Erkennung wie Ersteinrichtung
    result = await configure(hass, result, SYSTEM_INPUT)
    assert result["step_id"] == "rooms"                       # ein Hebelsatz: ohne Rueckfrage
    for data in (ROOMS_INPUT, PLANT_INPUT):
        result = await configure(hass, result, data)
    result = await configure(hass, result, {"notify_services": suggested(result, "notify_services")})
    result = await finish_progress(hass, await configure(hass, result, {}))
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    mocks.provision.assert_not_awaited()
    assert entry.data["lever_set"] == "vaillant_vrc720"
    assert entry.data["shift_lever"] == "room_setpoint"
    assert not entry_incomplete(entry.data)
    options = calls.options["heizungsbruecke"]
    assert (options["lever_set"], options["entity_shift_current"]) == ("vaillant_vrc720", ZONE)


async def test_reconfigure_of_an_entry_without_lever_set_keeps_the_customer_options(hass, monkeypatch):
    """Schluss-Review Plan 3c: das Pflicht-"Neu konfigurieren" von client1 nach dem Update belegt Raeume,
    Raum-Soll, Handys, Batterien und abgeschaltete Hinweise aus dem Eintrag vor; nur die Anlage kommt aus der
    Erkennung."""
    addon_before_update = {key: value for key, value in BRIDGE_OPTIONS.items() if key != "lever_set"}
    mypyllant, _, calls = _prepare(hass, monkeypatch, existing_options=addon_before_update, cloudflared_options=CF_OPTIONS)
    register_phones(hass, "mobile_app_tablet")
    stored = {
        "room_sensors": ["sensor.kz_temperatur"], "entity_room_target": "climate.wz::temperature",
        "notify_services": ["notify.mobile_app_tablet"], "battery_entities": ["sensor.kz_batterie"],
        "notify_hints_off": ["batterie", "schreibzaehler"],
    }
    entry = make_entry(hass, circuit_entry_id=mypyllant.entry_id, options=stored, data_without=("lever_set", "shift_lever"))
    assert entry_incomplete(entry.data)

    result = await login(hass, await entry.start_reconfigure_flow(hass))
    assert has_default(result, "verteilsystem") is False       # Anlage weiter aus der Erkennung
    result = await configure(hass, result, SYSTEM_INPUT)
    assert result["step_id"] == "rooms"
    assert (suggested(result, "room_sensors"), suggested(result, "entity_room_target")) == (
        ["sensor.kz_temperatur"], "climate.wz",
    )
    result = await configure(hass, result, {
        "room_sensors": suggested(result, "room_sensors"), "entity_room_target": suggested(result, "entity_room_target"),
    })
    result = await configure(hass, result, PLANT_INPUT)
    assert suggested(result, "notify_services") == ["notify.mobile_app_tablet"]
    result = await configure(hass, result, {"notify_services": suggested(result, "notify_services")})
    assert result["description_placeholders"]["batteries"] == "sensor.kz_batterie"
    result = await finish_progress(hass, await configure(hass, result, {}))
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert dict(entry.options) == stored
    options = calls.options["heizungsbruecke"]
    assert {key: options[key] for key in stored} == stored


WEISHAUPT_FULL = {
    "entity_curve_current": "number.weishaupt_wbb_heizkennlinie",
    "entity_shift_current": "number.weishaupt_wbb_raumsolltemperatur_normal",
    "entity_heat_limit": "number.weishaupt_wbb_sommer_winter_umschaltung",
    "entity_mode_select": "select.weishaupt_wbb_betriebsart",
    "entity_setpoint_comfort": "number.weishaupt_wbb_raumsolltemperatur_komfort",
    "entity_setpoint_setback": "number.weishaupt_wbb_raumsolltemperatur_absenk",
    "entity_outdoor_temp": "sensor.weishaupt_wbb_aussentemperatur",
}


async def test_switching_lever_set_drops_fields_of_the_old_one(hass, monkeypatch):
    # Weishaupt voll eingerichtet (Optionen mit entity_curve_current/entity_heat_limit), Neu konfigurieren mit Basis:
    # die geschriebenen Optionen enthalten keine Felder des alten Hebelsatzes mehr (Review Focus 3).
    enable_supervisor(hass, monkeypatch)
    mock_server(monkeypatch, catalog=weishaupt_catalog())
    weishaupt = setup_weishaupt(hass)
    setup_rooms(hass)
    register_phones(hass, "mobile_app_pixel")
    fast_status_wait(monkeypatch)
    addons = mock_addons(
        hass, monkeypatch, existing_options={**BRIDGE_OPTIONS, **WEISHAUPT_FULL, "lever_set": "weishaupt_wwp"},
        cloudflared_options=CF_OPTIONS,
    )
    entry = make_entry(hass, data={
        "tenant_id": TENANT, "profile_id": "weishaupt_waermepumpe_heizkoerper", "integration_domain": "weishaupt_modbus",
        "circuit": {"config_entry_id": weishaupt.entry_id, "system_key": "weishaupt_wbb", "circuit": "1"},
        "entities": WEISHAUPT_FULL, "lever_set": "weishaupt_wwp", "shift_lever": "room_setpoint",
    })
    assert not entry_incomplete(entry.data)

    result = await login(hass, await entry.start_reconfigure_flow(hass))
    result = await configure(hass, result, SYSTEM_INPUT_WP)
    assert result["step_id"] == "lever_set"
    assert _default(result, "lever_set") == "weishaupt_wwp"     # vorbelegt aus dem Eintrag
    result = await configure(hass, result, {"lever_set": "weishaupt_wwp_basis"})
    result = await configure(hass, result, ROOMS_INPUT)
    assert result["step_id"] == "plant_values"
    result = await configure(hass, result, {
        **{field: WEISHAUPT_FULL[field] for field in (
            "entity_shift_current", "entity_mode_select", "entity_setpoint_comfort", "entity_setpoint_setback",
            "entity_outdoor_temp",
        )},
        "advanced": {},
    })
    result = await configure(hass, result, {"notify_services": ["notify.mobile_app_pixel"]})
    result = await finish_progress(hass, await configure(hass, result, {}))
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert "entity_heat_limit" not in addons.options["heizungsbruecke"]
    assert "entity_curve_current" not in addons.options["heizungsbruecke"]
    assert addons.options["heizungsbruecke"]["lever_set"] == "weishaupt_wwp_basis"
    assert entry.data["lever_set"] == "weishaupt_wwp_basis"
    assert "entity_heat_limit" not in entry.data["entities"]


def reconfigure_snapshot(rollback):
    rollback.reconfigure.assert_awaited_once()
    return rollback.reconfigure.await_args.kwargs["snapshot"]
