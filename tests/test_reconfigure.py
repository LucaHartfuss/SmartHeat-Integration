"""Neu konfigurieren und Reauth (Spec TP7 2.3, 2.4)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohasupervisor.exceptions import SupervisorError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartheat.api_client import ApiError, InvalidAuth, ProfileRejected
from custom_components.smartheat.const import DOMAIN
from custom_components.smartheat.texts import async_hint

from .addon_fakes import make_entry, status_event
from .flow_helpers import (
    BRIDGE_OPTIONS,
    CATALOG,
    CF_OPTIONS,
    CF_SECRET,
    CURVE,
    FLOW_SETPOINT,
    MIN_FLOW,
    MQTT_PASSWORD,
    PLANT_INPUT,
    PROVISIONING,
    ROOMS_INPUT,
    SYSTEM_INPUT,
    TENANT,
    ZONE,
    configure,
    enable_supervisor,
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
    suggested,
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
    mocks.provision.assert_awaited_once_with("tok123", TENANT, "vaillant_gastherme_heizkoerper")
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
    assert (kwargs["token"], kwargs["new_credentials"]) == ("tok123", None)
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

    assert rollback.reconfigure.await_args.kwargs["new_credentials"] == (PROVISIONING["username"], MQTT_PASSWORD)


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


def reconfigure_snapshot(rollback):
    rollback.reconfigure.assert_awaited_once()
    return rollback.reconfigure.await_args.kwargs["snapshot"]
