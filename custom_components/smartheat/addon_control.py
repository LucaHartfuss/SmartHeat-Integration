"""Add-on-Steuerung der Integration. Neu ab 1.0.0 (Spec 5c 2.1): Heizungsbruecke finden, bei Bedarf installieren und
ohne Optionen starten, Watchdog/Autostart, Discovery und die Option bootstrap_url lesen; die Integration schreibt
keine Optionen. Der Rest (Optionen zusammenfuehren, Status-Event abwarten, Abmelden) ist der alte Pfad aus Spec TP7
2.5-2.7 und entfaellt in Task 7 (Plan 5c I). Nur das Add-on schreibt auf die Anlage."""
from __future__ import annotations

import asyncio
import logging
import urllib.parse
from dataclasses import dataclass, field

from aiohasupervisor.exceptions import SupervisorError
from aiohasupervisor.models import AddonBoot, AddonsOptions
from aiohasupervisor.models.addons import StoreAddRepository
from homeassistant.components import persistent_notification
from homeassistant.components.hassio import AddonError, AddonManager, AddonState, get_supervisor_client
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.hassio import is_hassio

from . import provisioning
from .api_client import HeizungsserverClient
from .const import (
    ADDON_REPOSITORY_URL,
    ADDON_STATUS_VALUES,
    BOOST_KEINER,
    BRIDGE_ACCESS_OPTIONS,
    CLOUDFLARED_ADDON_SLUG,
    DEFAULT_BASE_URL,
    DEFAULT_HEIZUNGSSERVER_BASE_URL,
    DISCOVERY_FIELDS,
    DISCOVERY_SERVICE,
    HEIZUNGSBRUECKE_ADDON_SLUG,
    LEGACY_ADDON_SPECS,
    OPTION_ABGEMELDET,
    OPTION_ACCOUNTS_API_BASE_URL,
    OPTION_BOOTSTRAP_URL,
    PROBLEM_CLEAR,
    PROBLEM_FOREIGN_TENANT,
    PROBLEM_NO_SIGN_OFF,
    PROBLEM_REVOKE,
    PROBLEM_STOP,
    PROBLEM_SUPERVISOR,
    SIGN_OFF_WAIT_SECONDS,
    STATUS_ABGEMELDET,
    STATUS_EVENT,
    STATUS_EVENT_SCHEMA,
    removal_notification_id,
    watchdog_notification_id,
)
from .supervisor_client import _matching_addons, async_find_addon_managers
from .texts import async_hint

_LOGGER = logging.getLogger(__name__)

WAIT_DONE = "done"
WAIT_FAILED = "failed"
WAIT_TIMEOUT = "timeout"

ADDON_NAME = "Heizungsbruecke"


async def _slug(hass: HomeAssistant) -> str | None:
    """Echter (praefigierter) Slug der installierten Heizungsbruecke, None wenn nicht installiert. Mehrfach
    installiert ist mehrdeutig und wird nicht geraten (AddonError)."""
    try:
        installed = await get_supervisor_client(hass).addons.list()
    except SupervisorError as error:
        raise AddonError(f"Supervisor nicht erreichbar: {type(error).__name__}") from None
    matches = _matching_addons(installed, ADDON_REPOSITORY_URL, HEIZUNGSBRUECKE_ADDON_SLUG)
    if len(matches) > 1:
        raise AddonError("Die Heizungsbruecke ist mehrfach installiert")
    return matches[0].slug if matches else None


def _manager(hass: HomeAssistant, slug: str) -> AddonManager:
    return AddonManager(hass, _LOGGER, ADDON_NAME, slug)


async def async_bridge_manager(hass: HomeAssistant) -> AddonManager | None:
    """Manager der installierten Heizungsbruecke, sonst None (nicht installiert, kein Supervisor, nicht lesbar)."""
    if not is_hassio(hass):
        return None
    try:
        slug = await _slug(hass)
    except AddonError as error:
        _LOGGER.warning("Add-on nicht auffindbar: %s", error)
        return None
    return _manager(hass, slug) if slug else None


def _same_repository(url: str | None) -> bool:
    return (url or "").rstrip("/").removesuffix(".git") == ADDON_REPOSITORY_URL


async def _repository_hash(hass: HomeAssistant) -> str:
    """Slug-Praefix des SmartHeat-Repositorys im Add-on-Store (der Supervisor praefigiert damit die Slugs). Fehlt das
    Repository im Store, wird es hinzugefuegt (aiohasupervisor kennt store.add_repository)."""
    store = get_supervisor_client(hass).store
    try:
        for attempt in (0, 1):
            for repository in await store.repositories_list():
                if _same_repository(repository.source) or _same_repository(repository.url):
                    return repository.slug
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
    slug = await _slug(hass) or f"{await _repository_hash(hass)}_{HEIZUNGSBRUECKE_ADDON_SLUG}"
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


class StatusListener:
    """Hoert auf smartheat_status eines Tenants und merkt sich das letzte Event. So sieht auch ein
    spaeteres "Erneut pruefen" ein Event, das zwischendurch kam."""

    def __init__(self, hass: HomeAssistant, tenant_id: str) -> None:
        self._tenant_id = tenant_id
        self._latest: dict | None = None
        self._changed = asyncio.Event()
        self._unsub = hass.bus.async_listen(STATUS_EVENT, self._handle)

    @callback
    def _handle(self, event: Event) -> None:
        data = event.data
        if (
            data.get("tenant_id") != self._tenant_id
            or data.get("schema") != STATUS_EVENT_SCHEMA
            or data.get("status") not in ADDON_STATUS_VALUES
        ):
            # Ein unbekannter Status wird verworfen wie ein fremder Tenant/Schema -- ein
            # kuenftiger, hier noch unbekannter Wert soll kein Warten faelschlich abschliessen.
            return
        self._latest = dict(data)
        self._changed.set()

    @property
    def latest(self) -> dict | None:
        """Letztes passendes Event (jede setup_id), None vor dem ersten."""
        return self._latest

    def _outcome(self, setup_id: str | None, done: frozenset, failed: frozenset) -> tuple[str, str | None] | None:
        data = self._latest
        if data is None or (setup_id is not None and data.get("setup_id") != setup_id):
            return None
        if data.get("status") in done:
            return WAIT_DONE, data.get("grund")
        if data.get("status") in failed:
            return WAIT_FAILED, str(data.get("grund") or "")
        return None

    async def async_wait(
        self, *, setup_id: str | None, done: frozenset, failed: frozenset, timeout: float,
    ) -> tuple[str, str | None]:
        """Wartet auf ein Event mit dieser setup_id (None = jede) und einem Status aus done oder
        failed; jeder andere Status laesst weiter warten, bis timeout."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while (outcome := self._outcome(setup_id, done, failed)) is None:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return WAIT_TIMEOUT, None
            self._changed.clear()
            try:
                async with asyncio.timeout(remaining):
                    await self._changed.wait()
            except TimeoutError:
                return WAIT_TIMEOUT, None
        return outcome

    def close(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None


async def async_update_addon_options(manager: AddonManager, updates: dict) -> dict:
    """Fuehrt `updates` mit den bestehenden Add-on-Optionen zusammen; alles andere bleibt."""
    existing = (await manager.async_get_addon_info()).options
    merged = {**existing, **updates}
    await manager.async_set_addon_options(merged)
    return merged


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


@dataclass(frozen=True)
class _ServerCredentials:
    base_url: str
    token: str = field(repr=False)


async def _async_read_bridge_options(bridge: AddonManager | None) -> dict | None:
    """Optionen der Heizungsbruecke vor jedem Schritt (Tenant-Pruefung, Widerruf); None ohne Add-on
    oder wenn sie nicht lesbar sind."""
    if bridge is None:
        return None
    try:
        return dict((await bridge.async_get_addon_info()).options)
    except AddonError as error:
        _LOGGER.warning("Entfernen: Optionen der Heizungsbruecke nicht lesbar, kein Widerruf auf dem Server: %s", type(error).__name__)
        return None


def _server_credentials(options: dict | None) -> _ServerCredentials | None:
    if options is None:
        return None
    token = provisioning.installation_token(options)
    if token is None:
        return None
    base_url = options.get(OPTION_ACCOUNTS_API_BASE_URL) or DEFAULT_HEIZUNGSSERVER_BASE_URL
    return _ServerCredentials(base_url, token)


async def _async_revoke_on_server(hass: HomeAssistant, tenant_id: str, credentials: _ServerCredentials | None) -> bool:
    """Spec TP8 4: best effort. Scheitert es, bleiben die Zugangsdaten bis zur naechsten
    Einrichtung gueltig; das Entfernen laeuft trotzdem weiter. Nie Zugangsdaten loggen. True, wenn
    nichts mehr offen ist (kein Zugang, 204, 401), False bei nicht erreichbarem Server, anderem
    Status oder unerwartetem Fehler."""
    if credentials is None:
        _LOGGER.info("Entfernen: keine Zugangsdaten in der Heizungsbruecke, kein Widerruf auf dem Server")
        return True
    try:
        client = HeizungsserverClient(async_get_clientsession(hass), credentials.base_url)
        status = await client.delete_installation(tenant_id, credentials.token)
    except Exception as error:  # noqa: BLE001 -- best effort (F8, finale Review TP8): api_client
        # faengt ClientError/TimeoutError bereits selbst ab, aber ein unerwarteter Fehler (z. B. ein
        # Bug in der Aufrufkette) darf das Leeren der Add-on-Zugangsdaten unten nicht verhindern.
        # Nie Zugangsdaten loggen.
        _LOGGER.warning("Entfernen: Widerruf auf dem Server unerwartet fehlgeschlagen: %s", error)
        return False
    if status == 204:
        _LOGGER.info("Entfernen: Zugangsdaten auf dem Server widerrufen")
    elif status == 401:
        _LOGGER.warning("Entfernen: Server kennt die Zugangsdaten nicht (bereits ersetzt?)")
    elif status is None:
        _LOGGER.warning("Entfernen: Server nicht erreichbar, die Zugangsdaten bleiben bis zur naechsten Einrichtung gueltig")
        return False
    else:
        _LOGGER.warning(
            "Entfernen: Widerruf auf dem Server fehlgeschlagen (HTTP %s), die Zugangsdaten bleiben bis zur "
            "naechsten Einrichtung gueltig", status,
        )
        return False
    return True


async def async_sign_off(hass: HomeAssistant, tenant_id: str, *, notify: bool = True) -> list[str]:
    """Entfernen (Spec TP7 2.7, TP12c 3.4), jeder Schritt best effort: Heizungsbruecke abmelden (ein laufender
    Boost wird zurueckgesetzt), hoechstens SIGN_OFF_WAIT_SECONDS auf `abgemeldet` warten, beide
    Add-ons stoppen (manuell, der Watchdog greift nicht), Watchdog/Boot aus, alle Zugangs-Optionen der
    Heizungsbruecke und das Ziel samt Service-Token von cloudflared leeren, eigene Benachrichtigung entfernen. Nach dem Stoppen widerruft der Server die
    Zugangsdaten (Spec TP8 4, best effort, mit dem Installations-Token aus den Optionen). Scheitert das Zuruecksetzen eines Boosts, bleibt die
    Heizungsbruecke samt Watchdog/Boot laufen: sie wiederholt es im Ruhezustand selbst, auch ohne
    Zugangsdaten und nach einem Neustart, und meldet es dem Kunden (TP7-Gates 2026-09-29).
    Gehoert die Heizungsbruecke laut ihren Optionen einem anderen Tenant (AU-011), wird keines der
    beiden Add-ons angefasst. Liefert die offenen Schritte; mit notify=True legt es dafuer eine
    Benachrichtigung an (der Rueckbau des Wizards meldet selbst)."""
    problems: list[str] = []
    try:
        managers = await async_find_addon_managers(hass, LEGACY_ADDON_SPECS)
    except AddonError as error:
        _LOGGER.warning("Entfernen: Supervisor nicht erreichbar, Add-ons nicht abgemeldet: %s", type(error).__name__)
        managers = {}
        problems.append(PROBLEM_SUPERVISOR)
    bridge = managers.get(HEIZUNGSBRUECKE_ADDON_SLUG)
    cloudflared = managers.get(CLOUDFLARED_ADDON_SLUG)
    options = await _async_read_bridge_options(bridge)
    # Heizungsbruecke da, Optionen nicht lesbar: die Zugangsdaten fuer den Widerruf sind unbekannt.
    options_unreadable = bridge is not None and options is None
    owner = (options or {}).get("tenant_id")
    if owner and owner != tenant_id:
        _LOGGER.warning("Entfernen: Heizungsbruecke gehoert einem anderen Tenant, Add-ons bleiben unberuehrt")
        problems.append(PROBLEM_FOREIGN_TENANT)
    else:
        problems += await _async_sign_off_addons(
            hass, tenant_id, bridge, cloudflared, _server_credentials(options), options_unreadable=options_unreadable,
        )
    persistent_notification.async_dismiss(hass, watchdog_notification_id(tenant_id))
    if notify and problems:
        await async_notify_open_steps(hass, removal_notification_id(tenant_id), "removal_incomplete", problems)
    return problems


async def _async_sign_off_addons(hass, tenant_id, bridge, cloudflared, credentials, *, options_unreadable: bool) -> list[str]:
    problems: list[str] = []
    keep_bridge, confirmed = (False, True) if bridge is None else await _async_sign_off_bridge(hass, bridge, tenant_id)
    if bridge is not None and not confirmed:
        problems.append(PROBLEM_NO_SIGN_OFF)
    to_stop = [bridge, cloudflared] if not keep_bridge else [cloudflared]
    present = [manager for manager in to_stop if manager is not None]
    for manager in present:
        try:
            await manager.async_stop_addon()
        except AddonError as error:
            _LOGGER.warning("Entfernen: Add-on %s nicht gestoppt: %s", manager.addon_slug, type(error).__name__)
            if PROBLEM_STOP not in problems:
                problems.append(PROBLEM_STOP)
    if present:
        await async_set_supervision(hass, [manager.addon_slug for manager in present], enabled=False)
    if options_unreadable:
        # Schon in _async_read_bridge_options protokolliert; der Widerruf bleibt offen.
        problems.append(PROBLEM_REVOKE)
    elif not await _async_revoke_on_server(hass, tenant_id, credentials):
        problems.append(PROBLEM_REVOKE)
    for manager, keys in ((bridge, BRIDGE_ACCESS_OPTIONS), (cloudflared, tuple(provisioning.CLOUDFLARED_CLEARED_OPTIONS))):
        if manager is None:
            continue
        try:
            await async_update_addon_options(manager, {key: "" for key in keys})
        except AddonError as error:
            _LOGGER.warning("Entfernen: Zugangsdaten von %s nicht geleert: %s", manager.addon_slug, type(error).__name__)
            if PROBLEM_CLEAR not in problems:
                problems.append(PROBLEM_CLEAR)
    return problems


async def async_notify_open_steps(hass: HomeAssistant, notification_id: str, headline_key: str, problems: list[str]) -> None:
    """Eine Benachrichtigung: Kopfzeile plus je offenem Schritt eine Handlungsanweisung (TP12c)."""
    lines = [await async_hint(hass, headline_key)]
    lines += [f"- {await async_hint(hass, f'open_step_{problem}')}" for problem in problems]
    persistent_notification.async_create(hass, "\n".join(lines), title="SmartHeat", notification_id=notification_id)


async def _async_sign_off_bridge(hass: HomeAssistant, bridge: AddonManager, tenant_id: str) -> tuple[bool, bool]:
    """(keep_running, confirmed): keep_running, wenn die Heizungsbruecke weiterlaufen muss
    (abgemeldet, aber ihr Boost steht noch auf der Anlage, das Zuruecksetzen scheitert, sie versucht
    es weiter); confirmed, wenn sie die Abmeldung bestaetigt hat."""
    listener = StatusListener(hass, tenant_id)
    try:
        await async_update_addon_options(bridge, {OPTION_ABGEMELDET: True})
        await bridge.async_restart_addon()
        outcome, grund = await listener.async_wait(
            setup_id=None, done=frozenset({STATUS_ABGEMELDET}), failed=frozenset(), timeout=SIGN_OFF_WAIT_SECONDS,
        )
        latest = listener.latest
    except AddonError as error:
        _LOGGER.warning("Entfernen: Heizungsbruecke nicht abgemeldet: %s", type(error).__name__)
        return False, False
    finally:
        listener.close()
    if outcome != WAIT_DONE:
        _LOGGER.warning("Entfernen: keine Abmeldung der Heizungsbruecke innerhalb von %s s", SIGN_OFF_WAIT_SECONDS)
        return False, False
    if latest is not None and latest.get("boost") != BOOST_KEINER:
        _LOGGER.warning(
            "Entfernen: Heizungsbruecke laeuft weiter, bis sie die Anlage zurueckgesetzt hat: %s", grund,
        )
        return True, True
    if grund:
        _LOGGER.warning("Entfernen: Heizungsbruecke abgemeldet, aber: %s", grund)
    return False, True


def _valid_url(value) -> bool:
    """Wie das Add-on (__main__._url_fehler): Text, http oder https, mit Host."""
    if not isinstance(value, str):
        return False
    try:
        parts = urllib.parse.urlsplit(value)
        return parts.scheme in ("http", "https") and bool(parts.hostname)
    except ValueError:
        return False


async def async_bootstrap_url(hass: HomeAssistant) -> str | None:
    """Bootstrap-Adresse, die das Add-on benutzt (Option bootstrap_url): gueltig = benutzt; fehlt oder null (oder das
    Add-on ist noch nicht installiert) = Produktion; sonst None -- nie ein Rueckfall auf die Produktion, die Aufrufer
    brechen mit einem Text ab (die Option laesst das Add-on im Ruhezustand). Wirft nie."""
    try:
        if not is_hassio(hass):
            return DEFAULT_BASE_URL
        slug = await _slug(hass)
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
        slug = await _slug(hass)
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
