"""Rueckbau eines abgebrochenen Wizard-Laufs (Spec TP12c 3.2): Wurde schon in die Add-ons
geschrieben und der Lauf nicht abgeschlossen, setzt die Ersteinrichtung alles zurueck (wie das
Entfernen) und Neu konfigurieren stellt den Stand vor dem ersten Schreiben wieder her. Jeder Schritt
best effort; am Ende eine Benachrichtigung mit den offenen Schritten."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.hassio import AddonError
from homeassistant.core import HomeAssistant

from .addon_control import async_notify_open_steps, async_sign_off
from .api_client import ApiError, HeizungsserverClient
from .const import ADDON_SPECS, PROBLEM_ADDONS, PROBLEM_PROFILE, PROBLEM_REVOKE, setup_notification_id
from .supervisor_client import (
    AddonNotFoundError,
    AddonOutdatedError,
    AmbiguousAddonMatchError,
    async_get_addon_managers,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconfigureSnapshot:
    """Stand vor dem ersten Schreiben eines Neu-konfigurieren-Laufs."""
    bridge_options: dict
    cloudflared_options: dict
    profile_id: str


async def async_rollback_first_setup(hass: HomeAssistant, tenant_id: str) -> None:
    """Ersteinrichtung: wie das Entfernen abmelden, die Benachrichtigung kommt von hier."""
    problems = await async_sign_off(hass, tenant_id, notify=False)
    await async_notify_open_steps(hass, setup_notification_id(tenant_id), "rollback_first_setup", problems)


async def async_rollback_reconfigure(
    hass: HomeAssistant, *, client: HeizungsserverClient, token: str | None, tenant_id: str,
    snapshot: ReconfigureSnapshot, profile_id: str, new_credentials: tuple[str, str] | None,
) -> None:
    """Neu konfigurieren: neu ausgestellte Zugangsdaten widerrufen, Profil und Add-on-Optionen auf
    den gesicherten Stand, beide Add-ons neu starten. Watchdog/Boot bleiben an (Spec 3.2)."""
    problems: list[str] = []
    if new_credentials is not None:
        # Nur die in diesem Lauf ausgestellten Zugangsdaten; nie loggen.
        status = await client.delete_installation(tenant_id, *new_credentials)
        if status not in (204, 401):
            _LOGGER.warning("Rueckbau: neue Zugangsdaten nicht widerrufen (HTTP %s)", status)
            problems.append(PROBLEM_REVOKE)
    if profile_id != snapshot.profile_id:
        if token is None:
            _LOGGER.warning("Rueckbau: ohne Sitzung kein Zuruecksetzen des Profils")
            problems.append(PROBLEM_PROFILE)
        else:
            try:
                await client.update_profile(token, tenant_id, snapshot.profile_id)
            except ApiError as error:
                _LOGGER.warning("Rueckbau: Profil nicht zurueckgesetzt: %s", type(error).__name__)
                problems.append(PROBLEM_PROFILE)
    try:
        heizungsbruecke, cloudflared = await async_get_addon_managers(hass, ADDON_SPECS)
        await heizungsbruecke.async_set_addon_options(snapshot.bridge_options)
        await cloudflared.async_set_addon_options(snapshot.cloudflared_options)
        await cloudflared.async_restart_addon()
        await heizungsbruecke.async_restart_addon()
    except (AddonNotFoundError, AmbiguousAddonMatchError, AddonOutdatedError, AddonError) as error:
        # Nicht den Supervisor-Text loggen: er kann Optionswerte zitieren.
        _LOGGER.warning("Rueckbau: Add-on-Optionen nicht wiederhergestellt: %s", type(error).__name__)
        problems.append(PROBLEM_ADDONS)
    await async_notify_open_steps(hass, setup_notification_id(tenant_id), "rollback_reconfigure", problems)
