"""Rueckbau eines abgebrochenen Wizard-Laufs (Spec TP12c 3.2): Wurde schon in die Add-ons
geschrieben und der Lauf nicht abgeschlossen, setzt die Ersteinrichtung alles zurueck (wie das
Entfernen) und Neu konfigurieren stellt den Stand vor dem ersten Schreiben wieder her. Hat der Lauf
nur den Server geaendert (Ersteinrichtung: Zugangsdaten ausgestellt; Neu konfigurieren: Profil gewechselt, ohne
neue Zugangsdaten), wird nur dort zurueckgenommen; die Add-ons bleiben unberuehrt. Mit neuen Zugangsdaten behaelt
Neu konfigurieren diese (async_rollback_reconfigure, nichts wird widerrufen). Jeder Schritt best effort und einzeln
protokolliert; am Ende eine Benachrichtigung mit den offenen Schritten."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from homeassistant.components.hassio import AddonError
from homeassistant.core import HomeAssistant

from . import provisioning
from .addon_control import async_notify_open_steps, async_set_supervision, async_sign_off
from .api_client import ApiError, HeizungsserverClient
from .const import (
    ADDON_SPECS,
    CLOUDFLARED_ADDON_SLUG,
    PROBLEM_ADDONS,
    PROBLEM_PROFILE,
    PROBLEM_REVOKE,
    setup_notification_id,
)
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
    hatte keins (dann bleibt das Profil auf dem Server, wie es ist). Die Optionen enthalten Geheimnisse
    (privater Schluessel, Token, Passwort, Service-Token): nie in die repr."""
    bridge_options: dict = field(repr=False)
    cloudflared_options: dict = field(repr=False)
    profile_id: str | None


def _headline(key: str, problems: list[str]) -> str:
    """Mit offenen Schritten eine eigene Kopfzeile: "wiederhergestellt" stimmt dann nicht."""
    return f"{key}_incomplete" if problems else key


async def async_rollback_first_setup(
    hass: HomeAssistant, tenant_id: str, *, client: HeizungsserverClient | None = None, new_token: str | None = None,
) -> None:
    """Ersteinrichtung: wie das Entfernen abmelden, die Benachrichtigung kommt von hier. Ein in diesem Lauf
    ausgestellter Zugang wird zusaetzlich direkt widerrufen: scheiterte das Schreiben, steht sein Token nicht in den
    Optionen (Audit 4, A4-39). Ein schon widerrufener Zugang antwortet 401, das zaehlt als erledigt."""
    _LOGGER.info("Rueckbau der abgebrochenen Ersteinrichtung gestartet")
    problems = await async_sign_off(hass, tenant_id, notify=False)
    if client is not None and new_token is not None:
        # Das direkte Widerrufen entscheidet: gelang es, ist ein Widerrufsproblem des Abmeldens erledigt (es konnte die
        # Optionen nicht lesen); scheiterte es, wird der Schritt nur einmal gelistet.
        if await _async_revoke(client, tenant_id, new_token):
            problems = [problem for problem in problems if problem != PROBLEM_REVOKE]
        elif PROBLEM_REVOKE not in problems:
            problems.append(PROBLEM_REVOKE)
    _LOGGER.info("Rueckbau der Ersteinrichtung beendet, offene Schritte: %s", problems or "keine")
    await async_notify_open_steps(
        hass, setup_notification_id(tenant_id), _headline("rollback_first_setup", problems), problems,
    )


async def async_rollback_reconfigure(
    hass: HomeAssistant, *, client: HeizungsserverClient, token: str | None, tenant_id: str,
    snapshot: ReconfigureSnapshot, profile_id: str, keep_access: provisioning.Access | None,
) -> None:
    """Neu konfigurieren: Profil und Add-on-Optionen auf den gesicherten Stand, beide Add-ons neu starten.
    Watchdog/Boot bleiben an (Spec 3.2). Hat der Lauf neue Zugangsdaten ausgestellt (keep_access), ersetzen sie die
    alten schon auf dem Server: sie bleiben und gehen mit dem gesicherten Stand in die Add-ons, nichts wird widerrufen
    (Audit 4, A4-13; Nutzer-Entscheidung E7)."""
    _LOGGER.info("Rueckbau des abgebrochenen Neu konfigurieren gestartet")
    problems = await _async_undo_server_changes(client, token, tenant_id, None, snapshot.profile_id, profile_id)
    if not await _async_restore_addons(hass, snapshot, keep_access):
        problems.append(PROBLEM_ADDONS)
    _LOGGER.info("Rueckbau des Neu konfigurieren beendet, offene Schritte: %s", problems or "keine")
    key = "rollback_reconfigure_new_access" if keep_access is not None else "rollback_reconfigure"
    await async_notify_open_steps(hass, setup_notification_id(tenant_id), _headline(key, problems), problems)


async def async_rollback_server_only(
    hass: HomeAssistant, *, client: HeizungsserverClient, token: str | None, tenant_id: str, first_setup: bool,
    new_token: str | None, previous_profile_id: str | None, profile_id: str | None,
) -> None:
    """Abbruch, bevor in die Add-ons geschrieben wurde: nur die Aenderungen auf dem Server
    zuruecknehmen (Ersteinrichtung: neu ausgestellte Zugangsdaten widerrufen; Neu konfigurieren ohne neue
    Zugangsdaten: das Profil zuruecksetzen). Mit neuen Zugangsdaten beim Neu konfigurieren ruft der Aufrufer stattdessen
    async_rollback_reconfigure (keep_access) auf. Kein Abmelden, kein Neustart. Dieselbe Benachrichtigung wie der volle
    Rueckbau."""
    _LOGGER.info("Rueckbau ohne geschriebene Add-ons gestartet (nur Server)")
    problems = await _async_undo_server_changes(client, token, tenant_id, new_token, previous_profile_id, profile_id)
    _LOGGER.info("Rueckbau ohne geschriebene Add-ons beendet, offene Schritte: %s", problems or "keine")
    key = "rollback_first_setup" if first_setup else "rollback_reconfigure"
    await async_notify_open_steps(hass, setup_notification_id(tenant_id), _headline(key, problems), problems)


async def _async_undo_server_changes(
    client: HeizungsserverClient, token: str | None, tenant_id: str, new_token: str | None,
    previous_profile_id: str | None, profile_id: str | None,
) -> list[str]:
    """Neu ausgestellte Zugangsdaten widerrufen, dann das Profil auf den gesicherten Stand. Ohne
    gesichertes Profil (Ersteinrichtung, Eintrag ohne Profil) bleibt es auf dem Server, wie es ist."""
    problems: list[str] = []
    if new_token is not None and not await _async_revoke(client, tenant_id, new_token):
        problems.append(PROBLEM_REVOKE)
    if previous_profile_id is not None and profile_id != previous_profile_id and not await _async_reset_profile(
        client, token, tenant_id, previous_profile_id,
    ):
        problems.append(PROBLEM_PROFILE)
    return problems


async def _async_revoke(client: HeizungsserverClient, tenant_id: str, installation_token: str) -> bool:
    # Nur der in diesem Lauf ausgestellte Zugang (Installations-Token); nie loggen.
    status = await client.delete_installation(tenant_id, installation_token)
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


async def _async_restore_addons(
    hass: HomeAssistant, snapshot: ReconfigureSnapshot, keep_access: provisioning.Access | None = None,
) -> bool:
    """Jeder Schritt fuer sich: ein gescheiterter Neustart von cloudflared darf nicht verhindern, dass
    die Heizungsbruecke mit den alten Optionen neu startet. Welche Add-ons laufen und ueberwacht
    werden, bestimmt der gesicherte Transport (provisioning.watched_addon_slugs): Beim Mosquitto-Stand
    (oder einem Stand ohne lesbaren Transport) schaltet der Rueckbau Watchdog/Boot beider Add-ons wieder
    ein und startet cloudflared neu -- auch wenn der abgebrochene Lauf auf iot_core umgestellt und die
    Ueberwachung abgeschaltet hatte. Bei iot_core bleibt cloudflared gestoppt und ohne Ueberwachung
    (Plan AWS-2, Praez. 1). Mit keep_access (neuer Zugang des abgebrochenen Laufs, E7) kommen dessen Zugangs-
    Optionen auf den gesicherten Stand; die Ueberwachung richtet sich dann nach dem neuen Transport."""
    bridge_options, cloudflared_options = snapshot.bridge_options, snapshot.cloudflared_options
    if keep_access is not None:
        bridge_options = {**bridge_options, **provisioning.bridge_access_options(keep_access)}
        cloudflared_options = {
            **cloudflared_options,
            **(keep_access.cloudflared if keep_access.cloudflared is not None
               else provisioning.CLOUDFLARED_CLEARED_OPTIONS),
        }
    try:
        heizungsbruecke, cloudflared = await async_get_addon_managers(hass, ADDON_SPECS)
    except (AddonNotFoundError, AmbiguousAddonMatchError, AddonOutdatedError, AddonError) as error:
        _LOGGER.warning("Rueckbau: Add-ons nicht gefunden: %s", type(error).__name__)
        return False
    cloudflared_watched = CLOUDFLARED_ADDON_SLUG in provisioning.watched_addon_slugs(bridge_options)

    async def restore_supervision() -> None:
        failed = await async_set_supervision(hass, [heizungsbruecke.addon_slug], enabled=True)
        failed += await async_set_supervision(hass, [cloudflared.addon_slug], enabled=cloudflared_watched)
        if failed:
            raise AddonError("Watchdog/Boot nicht gesetzt")

    steps: list[tuple[str, Callable[[], Awaitable[None]]]] = [
        ("Optionen der Heizungsbruecke wiederherstellen",
         lambda: heizungsbruecke.async_set_addon_options(bridge_options)),
        ("Optionen von cloudflared wiederherstellen",
         lambda: cloudflared.async_set_addon_options(cloudflared_options)),
        ("Watchdog und Boot wiederherstellen", restore_supervision),
        ("cloudflared neu starten" if cloudflared_watched else "cloudflared stoppen",
         cloudflared.async_restart_addon if cloudflared_watched else cloudflared.async_stop_addon),
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
