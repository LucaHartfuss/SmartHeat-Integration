"""Rueckbau abgebrochener Wizard-Laeufe (Spec TP12c 3.2)."""
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.hassio import AddonError

from custom_components.smartheat import setup_rollback
from custom_components.smartheat.api_client import ApiError
from custom_components.smartheat.setup_rollback import ReconfigureSnapshot
from custom_components.smartheat.texts import async_hint

from .addon_fakes import FakeAddon

SR = "custom_components.smartheat.setup_rollback"
PASSWORD = "geheim-pw-4711"
SNAPSHOT = ReconfigureSnapshot({"tenant_id": "wohnung1", "mqtt_username": "alt"}, {"hostname": "h"}, "altes_profil")


@pytest.fixture
def created(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "homeassistant.components.persistent_notification.async_create",
        lambda hass, message, title=None, notification_id=None: calls.append((notification_id, message)),
    )
    return calls


def _addons(monkeypatch, log, error=None):
    bridge, cloudflared = FakeAddon("heizungsbruecke", log, error=error), FakeAddon("cloudflared_access_mqtt", log)
    monkeypatch.setattr(f"{SR}.async_get_addon_managers", AsyncMock(return_value=(bridge, cloudflared)))
    return bridge, cloudflared


async def test_first_setup_rollback_signs_off_quietly_and_notifies(hass, monkeypatch, created):
    sign_off = AsyncMock(return_value=[])
    monkeypatch.setattr(f"{SR}.async_sign_off", sign_off)

    await setup_rollback.async_rollback_first_setup(hass, "wohnung1")

    sign_off.assert_awaited_once_with(hass, "wohnung1", notify=False)
    assert created[0] == ("smartheat_wohnung1_setup", await async_hint(hass, "rollback_first_setup"))


async def test_reconfigure_rollback_restores_options_and_profile(hass, monkeypatch, created):
    log = []
    bridge, cloudflared = _addons(monkeypatch, log)
    client = AsyncMock()

    await setup_rollback.async_rollback_reconfigure(
        hass, client=client, token="tok", tenant_id="wohnung1", snapshot=SNAPSHOT, profile_id="neues_profil",
        new_credentials=None,
    )

    client.update_profile.assert_awaited_once_with("tok", "wohnung1", "altes_profil")
    client.delete_installation.assert_not_awaited()
    assert bridge.options == SNAPSHOT.bridge_options and cloudflared.options == SNAPSHOT.cloudflared_options
    assert [entry[:2] for entry in log][-2:] == [("restart", "cloudflared_access_mqtt"), ("restart", "heizungsbruecke")]
    assert len(created[0][1].splitlines()) == 1  # nur die Kopfzeile, keine offenen Schritte


async def test_unchanged_profile_is_not_reset(hass, monkeypatch, created):
    _addons(monkeypatch, [])
    client = AsyncMock()

    await setup_rollback.async_rollback_reconfigure(
        hass, client=client, token="tok", tenant_id="wohnung1", snapshot=SNAPSHOT, profile_id="altes_profil",
        new_credentials=None,
    )

    client.update_profile.assert_not_awaited()


async def test_new_credentials_are_revoked(hass, monkeypatch, created):
    _addons(monkeypatch, [])
    client = AsyncMock()
    client.delete_installation.return_value = 204

    await setup_rollback.async_rollback_reconfigure(
        hass, client=client, token="tok", tenant_id="wohnung1", snapshot=SNAPSHOT, profile_id="altes_profil",
        new_credentials=("neu_user", "neu_pw"),
    )

    client.delete_installation.assert_awaited_once_with("wohnung1", "neu_user", "neu_pw")


async def test_failed_steps_are_listed(hass, monkeypatch, created, caplog):
    _addons(monkeypatch, [], error=AddonError("kaputt geheim-pw-4711"))
    client = AsyncMock()
    client.update_profile.side_effect = ApiError("weg")
    client.delete_installation.return_value = None

    await setup_rollback.async_rollback_reconfigure(
        hass, client=client, token="tok", tenant_id="wohnung1", snapshot=SNAPSHOT, profile_id="neu",
        new_credentials=("u", PASSWORD),
    )

    message = created[0][1]
    lines = message.splitlines()
    assert len(lines) == 4  # Kopfzeile + revoke, profile, addons
    assert lines[0] == await async_hint(hass, "rollback_reconfigure_incomplete")
    assert PASSWORD not in message
    assert PASSWORD not in caplog.text


async def test_one_failing_addon_step_does_not_skip_the_others(hass, monkeypatch, created):
    log = []
    bridge, cloudflared = _addons(monkeypatch, log)
    cloudflared.async_restart_addon = AsyncMock(side_effect=AddonError("weg"))

    await setup_rollback.async_rollback_reconfigure(
        hass, client=AsyncMock(), token="tok", tenant_id="wohnung1", snapshot=SNAPSHOT, profile_id="altes_profil",
        new_credentials=None,
    )

    assert bridge.options == SNAPSHOT.bridge_options and cloudflared.options == SNAPSHOT.cloudflared_options
    assert ("restart", "heizungsbruecke") in log  # die Heizungsbruecke startet trotzdem mit den alten Optionen
    lines = created[0][1].splitlines()
    assert lines[1:] == ["- " + await async_hint(hass, "open_step_addons")]


async def test_missing_addons_skip_the_restore(hass, monkeypatch, created):
    monkeypatch.setattr(f"{SR}.async_get_addon_managers", AsyncMock(side_effect=AddonError("weg")))

    await setup_rollback.async_rollback_reconfigure(
        hass, client=AsyncMock(), token="tok", tenant_id="wohnung1", snapshot=SNAPSHOT, profile_id="altes_profil",
        new_credentials=None,
    )

    assert created[0][1].splitlines()[1:] == ["- " + await async_hint(hass, "open_step_addons")]


async def test_snapshot_without_profile_keeps_the_server_profile(hass, monkeypatch, created):
    _addons(monkeypatch, [])
    client = AsyncMock()

    await setup_rollback.async_rollback_reconfigure(
        hass, client=client, token="tok", tenant_id="wohnung1",
        snapshot=ReconfigureSnapshot({}, {}, None), profile_id="neu", new_credentials=None,
    )

    client.update_profile.assert_not_awaited()
    assert created[0][1] == await async_hint(hass, "rollback_reconfigure")


async def test_first_setup_rollback_with_open_steps_uses_the_incomplete_headline(hass, monkeypatch, created):
    monkeypatch.setattr(f"{SR}.async_sign_off", AsyncMock(return_value=["stop"]))

    await setup_rollback.async_rollback_first_setup(hass, "wohnung1")

    assert created[0][1].splitlines()[0] == await async_hint(hass, "rollback_first_setup_incomplete")


async def test_rollback_without_token_reports_the_profile(hass, monkeypatch, created):
    _addons(monkeypatch, [])

    await setup_rollback.async_rollback_reconfigure(
        hass, client=AsyncMock(), token=None, tenant_id="wohnung1", snapshot=SNAPSHOT, profile_id="neu",
        new_credentials=None,
    )

    assert len(created[0][1].splitlines()) == 2
