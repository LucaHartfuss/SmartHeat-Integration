"""Add-on-Steuerung der Integration (Spec 5c 2.1): Heizungsbruecke finden, bei Bedarf installieren und ohne Optionen
starten, Watchdog/Autostart, Discovery und die Option bootstrap_url lesen. Die Integration schreibt keine Optionen;
nur das Add-on schreibt auf die Anlage."""
from __future__ import annotations

import logging
import urllib.parse

from aiohasupervisor.exceptions import SupervisorError
from aiohasupervisor.models import AddonBoot, AddonsOptions
from aiohasupervisor.models.addons import StoreAddRepository
from homeassistant.components.hassio import AddonError, AddonManager, AddonState, get_supervisor_client
from homeassistant.core import HomeAssistant
from homeassistant.helpers.hassio import is_hassio

from .const import (
    ADDON_REPOSITORY_URL,
    DEFAULT_BASE_URL,
    DISCOVERY_FIELDS,
    DISCOVERY_SERVICE,
    HEIZUNGSBRUECKE_ADDON_SLUG,
    OPTION_BOOTSTRAP_URL,
)
from .supervisor_client import async_repository_slug, matching_addons

_LOGGER = logging.getLogger(__name__)

ADDON_NAME = "Heizungsbruecke"


async def async_bridge_slug(hass: HomeAssistant) -> str | None:
    """Echter Slug der installierten Heizungsbruecke aus dem SmartHeat-Repository: das Repository erkannt an seiner
    Store-Adresse (`source`), das Add-on an Repository und exaktem Slug (supervisor_client._matching_addons). None,
    wenn das Repository nicht eingetragen oder die Heizungsbruecke nicht installiert ist; ein Add-on mit unserer
    `url` oder einem Slug *_heizungsbruecke aus einem anderen Repository zaehlt nie. Mehrfach installiert ist
    mehrdeutig und wird nicht geraten. Wirft AddonError."""
    repository_slug = await async_repository_slug(hass)
    if repository_slug is None:
        return None
    try:
        installed = await get_supervisor_client(hass).addons.list()
    except SupervisorError as error:
        raise AddonError(f"Supervisor nicht erreichbar: {type(error).__name__}") from None
    matches = matching_addons(installed, repository_slug, HEIZUNGSBRUECKE_ADDON_SLUG)
    if len(matches) > 1:
        raise AddonError("Die Heizungsbruecke ist mehrfach installiert")
    return matches[0].slug if matches else None


def _manager(hass: HomeAssistant, slug: str) -> AddonManager:
    return AddonManager(hass, _LOGGER, ADDON_NAME, slug)


async def _repository_hash(hass: HomeAssistant) -> str:
    """Slug-Praefix des SmartHeat-Repositorys im Add-on-Store (der Supervisor praefigiert damit die Slugs), erkannt
    nur an `source`. Fehlt das Repository im Store, wird es hinzugefuegt (nur auf dem Installationsweg)."""
    store = get_supervisor_client(hass).store
    try:
        for attempt in (0, 1):
            if (repository_slug := await async_repository_slug(hass)) is not None:
                return repository_slug
            if attempt == 0:
                await store.add_repository(StoreAddRepository(repository=ADDON_REPOSITORY_URL))
    except SupervisorError as error:
        raise AddonError(f"Add-on-Repository nicht verfuegbar: {type(error).__name__}") from None
    raise AddonError(f"Das Add-on-Repository {ADDON_REPOSITORY_URL} ist im Add-on-Store nicht eingetragen")


async def async_install_and_start(hass: HomeAssistant) -> AddonManager:
    """Installiert die Heizungsbruecke aus dem Repository (falls noetig) und startet sie, ohne Optionen zu schreiben;
    danach Watchdog und Autostart an. Wirft AddonError."""
    if not is_hassio(hass):
        raise AddonError("Kein Supervisor: das Add-on laesst sich hier nicht installieren")
    slug = await async_bridge_slug(hass) or f"{await _repository_hash(hass)}_{HEIZUNGSBRUECKE_ADDON_SLUG}"
    manager = _manager(hass, slug)
    info = await manager.async_get_addon_info()
    if info.state == AddonState.NOT_INSTALLED:
        await manager.async_install_addon()
        info = await manager.async_get_addon_info()
    if info.state == AddonState.NOT_RUNNING:
        await manager.async_start_addon()
    failed = await async_set_supervision(hass, [slug], True)
    if failed:
        _LOGGER.warning("Watchdog und Autostart der Heizungsbruecke nicht gesetzt")
    return manager


async def async_set_supervision(hass: HomeAssistant, slugs: list[str], enabled: bool) -> list[str]:
    """Watchdog und Boot (Spec TP7 2.6): an = watchdog + boot auto, aus = manual. Gibt die (echten,
    aufgeloesten) Slugs zurueck, bei denen es scheiterte -- das ist nur eine Warnung."""
    options = AddonsOptions(watchdog=enabled, boot=AddonBoot.AUTO if enabled else AddonBoot.MANUAL)
    client = get_supervisor_client(hass)
    failed = []
    for slug in slugs:
        try:
            await client.addons.set_addon_options(slug, options)
        except SupervisorError as error:
            # Nicht den Supervisor-Text loggen: er kann Optionswerte zitieren (Audit 4, A4-40).
            _LOGGER.warning("Watchdog/Boot fuer Add-on %s nicht gesetzt: %s", slug, type(error).__name__)
            failed.append(slug)
    return failed


def _valid_url(value) -> bool:
    """Wie das Add-on (__main__._url_fehler): Option nur https mit Host (5c F/Q4)."""
    if not isinstance(value, str):
        return False
    try:
        parts = urllib.parse.urlsplit(value)
        return parts.scheme == "https" and bool(parts.hostname)
    except ValueError:
        return False


async def async_bootstrap_url(hass: HomeAssistant) -> str | None:
    """Bootstrap-Adresse, die das Add-on benutzt (Option bootstrap_url): gueltig = benutzt; fehlt oder null (oder das
    Add-on ist noch nicht installiert) = Produktion; sonst None -- nie ein Rueckfall auf die Produktion, die Aufrufer
    brechen mit einem Text ab (die Option laesst das Add-on im Ruhezustand). Wirft nie."""
    try:
        if not is_hassio(hass):
            return DEFAULT_BASE_URL
        slug = await async_bridge_slug(hass)
        if slug is None:
            return DEFAULT_BASE_URL
        options = (await get_supervisor_client(hass).addons.addon_info(slug)).options
        value = options.get(OPTION_BOOTSTRAP_URL) if isinstance(options, dict) else None
    except Exception as error:  # noqa: BLE001 - Optionen nicht lesbar: nicht raten
        _LOGGER.warning("Add-on-Optionen nicht lesbar (%s)", type(error).__name__)
        return None
    if value is None:
        return DEFAULT_BASE_URL
    if _valid_url(value):
        return value.rstrip("/")
    _LOGGER.warning("Option bootstrap_url des Add-ons ist ungueltig")
    return None


async def async_discovery(hass: HomeAssistant) -> dict | None:
    """Config der Discovery des Add-ons (nur DISCOVERY_FIELDS): Dienst smartheat von der Heizungsbruecke; bei mehreren
    Nachrichten (kurz nebeneinander moeglich) die mit Uebernahme-Code, sonst die juengste. Wirft nie."""
    try:
        if not is_hassio(hass):
            return None
        slug = await async_bridge_slug(hass)
        if slug is None:
            return None
        messages = await get_supervisor_client(hass).discovery.list()
    except Exception as error:  # noqa: BLE001
        _LOGGER.warning("Discovery nicht lesbar (%s)", type(error).__name__)
        return None
    candidates = [m.config for m in messages
                  if m.service == DISCOVERY_SERVICE and m.addon == slug and isinstance(m.config, dict)]
    with_code = [config for config in candidates if config.get("claim_code")]
    chosen = (with_code or candidates or [None])[-1]
    if chosen is None:
        return None
    return {key: chosen[key] for key in DISCOVERY_FIELDS if key in chosen}
