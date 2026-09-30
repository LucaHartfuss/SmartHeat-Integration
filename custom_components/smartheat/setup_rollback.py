"""Rueckbau eines abgebrochenen Wizard-Laufs (Spec TP12c 3.2): Wurde schon in die Add-ons
geschrieben und der Lauf nicht abgeschlossen, setzt die Ersteinrichtung alles zurueck (wie das
Entfernen) und Neu konfigurieren stellt den Stand vor dem ersten Schreiben wieder her. Hat der Lauf
nur den Server geaendert (Zugangsdaten ausgestellt, Profil gewechselt), wird nur dort
zurueckgenommen; die Add-ons bleiben unberuehrt. Jeder Schritt best effort und einzeln
protokolliert; am Ende eine Benachrichtigung mit den offenen Schritten."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
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
    """Stand vor dem ersten Schreiben eines Neu-konfigurieren-Laufs. profile_id None: der Eintrag
    hatte keins (dann bleibt das Profil auf dem Server, wie es ist)."""
    bridge_options: dict
    cloudflared_options: dict
    profile_id: str | None


def _headline(key: str, problems: list[str]) -> str:
    """Mit offenen Schritten eine eigene Kopfzeile: "wiederhergestellt" stimmt dann nicht."""
    return f"{key}_incomplete" if problems else key


async def async_rollback_first_setup(hass: HomeAssistant, tenant_id: str) -> None:
    """Ersteinrichtung: wie das Entfernen abmelden, die Benachrichtigung kommt von hier."""
    _LOGGER.info("Rueckbau der abgebrochenen Ersteinrichtung gestartet")
    problems = await async_sign_off(hass, tenant_id, notify=False)
    _LOGGER.info("Rueckbau der Ersteinrichtung beendet, offene Schritte: %s", problems or "keine")
    await async_notify_open_steps(
        hass, setup_notification_id(tenant_id), _headline("rollback_first_setup", problems), problems,
    )


async def async_rollback_reconfigure(
    hass: HomeAssistant, *, client: HeizungsserverClient, token: str | None, tenant_id: str,
    snapshot: ReconfigureSnapshot, profile_id: str, new_credentials: tuple[str, str] | None,
) -> None:
    """Neu konfigurieren: neu ausgestellte Zugangsdaten widerrufen, Profil und Add-on-Optionen auf
    den gesicherten Stand, beide Add-ons neu starten. Watchdog/Boot bleiben an (Spec 3.2)."""
    _LOGGER.info("Rueckbau des abgebrochenen Neu konfigurieren gestartet")
    problems = await _async_undo_server_changes(client, token, tenant_id, new_credentials, snapshot.profile_id, profile_id)
    if not await _async_restore_addons(hass, snapshot):
        problems.append(PROBLEM_ADDONS)
    _LOGGER.info("Rueckbau des Neu konfigurieren beendet, offene Schritte: %s", problems or "keine")
    await async_notify_open_steps(
        hass, setup_notification_id(tenant_id), _headline("rollback_reconfigure", problems), problems,
    )


async def async_rollback_server_only(
    hass: HomeAssistant, *, client: HeizungsserverClient, token: str | None, tenant_id: str, first_setup: bool,
    new_credentials: tuple[str, str] | None, previous_profile_id: str | None, profile_id: str | None,
) -> None:
    """Abbruch, bevor in die Add-ons geschrieben wurde: nur die Aenderungen auf dem Server
    zuruecknehmen (neu ausgestellte Zugangsdaten widerrufen, beim Neu konfigurieren das Profil
    zuruecksetzen). Kein Abmelden, kein Neustart. Dieselbe Benachrichtigung wie der volle Rueckbau."""
    _LOGGER.info("Rueckbau ohne geschriebene Add-ons gestartet (nur Server)")
    problems = await _async_undo_server_changes(client, token, tenant_id, new_credentials, previous_profile_id, profile_id)
    _LOGGER.info("Rueckbau ohne geschriebene Add-ons beendet, offene Schritte: %s", problems or "keine")
    key = "rollback_first_setup" if first_setup else "rollback_reconfigure"
    await async_notify_open_steps(hass, setup_notification_id(tenant_id), _headline(key, problems), problems)


async def _async_undo_server_changes(
    client: HeizungsserverClient, token: str | None, tenant_id: str, new_credentials: tuple[str, str] | None,
    previous_profile_id: str | None, profile_id: str | None,
) -> list[str]:
    """Neu ausgestellte Zugangsdaten widerrufen, dann das Profil auf den gesicherten Stand. Ohne
    gesichertes Profil (Ersteinrichtung, Eintrag ohne Profil) bleibt es auf dem Server, wie es ist."""
    problems: list[str] = []
    if new_credentials is not None and not await _async_revoke(client, tenant_id, new_credentials):
        problems.append(PROBLEM_REVOKE)
    if previous_profile_id is not None and profile_id != previous_profile_id and not await _async_reset_profile(
        client, token, tenant_id, previous_profile_id,
    ):
        problems.append(PROBLEM_PROFILE)
    return problems


async def _async_revoke(client: HeizungsserverClient, tenant_id: str, credentials: tuple[str, str]) -> bool:
    # Nur die in diesem Lauf ausgestellten Zugangsdaten; nie loggen.
    status = await client.delete_installation(tenant_id, *credentials)
    if status not in (204, 401):
        _LOGGER.warning("Rueckbau: neue Zugangsdaten nicht widerrufen (HTTP %s)", status)
        return False
    _LOGGER.info("Rueckbau: neue Zugangsdaten auf dem Server widerrufen")
    return True


async def _async_reset_profile(client: HeizungsserverClient, token: str | None, tenant_id: str, profile_id: str) -> bool:
    if token is None:
        _LOGGER.warning("Rueckbau: ohne Sitzung kein Zuruecksetzen des Profils")
        return False
    try:
        await client.update_profile(token, tenant_id, profile_id)
    except ApiError as error:
        _LOGGER.warning("Rueckbau: Profil nicht zurueckgesetzt: %s", type(error).__name__)
        return False
    _LOGGER.info("Rueckbau: Profil auf dem Server zurueckgesetzt")
    return True


async def _async_restore_addons(hass: HomeAssistant, snapshot: ReconfigureSnapshot) -> bool:
    """Jeder der vier Schritte fuer sich: ein gescheiterter Neustart von cloudflared darf nicht
    verhindern, dass die Heizungsbruecke mit den alten Optionen neu startet."""
    try:
        heizungsbruecke, cloudflared = await async_get_addon_managers(hass, ADDON_SPECS)
    except (AddonNotFoundError, AmbiguousAddonMatchError, AddonOutdatedError, AddonError) as error:
        _LOGGER.warning("Rueckbau: Add-ons nicht gefunden: %s", type(error).__name__)
        return False
    steps: list[tuple[str, Callable[[], Awaitable[None]]]] = [
        ("Optionen der Heizungsbruecke wiederherstellen",
         lambda: heizungsbruecke.async_set_addon_options(snapshot.bridge_options)),
        ("Optionen von cloudflared wiederherstellen",
         lambda: cloudflared.async_set_addon_options(snapshot.cloudflared_options)),
        ("cloudflared neu starten", cloudflared.async_restart_addon),
        ("Heizungsbruecke neu starten", heizungsbruecke.async_restart_addon),
    ]
    ok = True
    for what, step in steps:
        try:
            await step()
        except AddonError as error:
            # Nicht den Supervisor-Text loggen: er kann Optionswerte zitieren.
            _LOGGER.warning("Rueckbau: %s gescheitert: %s", what, type(error).__name__)
            ok = False
        else:
            _LOGGER.info("Rueckbau: %s erledigt", what)
    return ok
