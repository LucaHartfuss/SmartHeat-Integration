"""Add-on-Steuerung der Integration: Heizungsbruecke finden, bei Bedarf installieren und ohne Optionen
starten, Watchdog/Autostart, Discovery und die Option bootstrap_url lesen, den Nachweis des Geraets anfordern. Die
Integration schreibt keine Optionen; nur das Add-on schreibt auf die Anlage."""
from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse
from dataclasses import dataclass

from aiohasupervisor.exceptions import SupervisorError
from aiohasupervisor.models import AddonBoot, AddonsOptions
from aiohasupervisor.models.addons import StoreAddRepository
from homeassistant.components.hassio import AddonError, AddonManager, AddonState, get_supervisor_client
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.hassio import is_hassio

from .const import (
    ADDON_NAME,
    ADDON_REPOSITORY_URL,
    DEFAULT_BASE_URL,
    DISCOVERY_FIELDS,
    DISCOVERY_SERVICE,
    GERAET_ID_RE,
    HEIZUNGSBRUECKE_ADDON_SLUG,
    NACHWEIS_EVENT,
    NACHWEIS_EVENT_FIELDS,
    NACHWEIS_EVENT_SCHEMA,
    NACHWEIS_SIGNATUR_MAX,
    NACHWEIS_WAIT_SECONDS,
    OPTION_BOOTSTRAP_URL,
    STDIN_BEFEHL_ENTFERNEN,
    STDIN_BEFEHL_NACHWEIS,
    STDIN_ZEILE_FORMAT,
)
from .supervisor_client import async_repository_slug, async_repository_slugs, fremde_bruecken, matching_addons

_LOGGER = logging.getLogger(__name__)
# Signatur im Ereignis: Zeichen von Base64 (der Geraetekern sendet base64url ohne Fuellzeichen); ein Zeilenumbruch
# oder anderes Zeichen wuerde als Kopfzeile der Anfrage an den Server gehen.
_SIGNATUR_RE = re.compile(r"[A-Za-z0-9_+/=-]+")


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
    if not await async_set_supervision(hass, slug):
        _LOGGER.warning("Watchdog und Autostart der Heizungsbruecke nicht gesetzt")
    return manager


async def async_set_supervision(hass: HomeAssistant, slug: str) -> bool:
    """Watchdog und Boot der Heizungsbruecke an: watchdog + boot auto. True, wenn es gesetzt wurde;
    scheitert es, ist das nur eine Warnung des Aufrufers."""
    try:
        await get_supervisor_client(hass).addons.set_addon_options(
            slug, AddonsOptions(watchdog=True, boot=AddonBoot.AUTO),
        )
    except SupervisorError as error:
        # Nicht den Supervisor-Text loggen: er kann Optionswerte zitieren.
        _LOGGER.warning("Watchdog/Boot fuer Add-on %s nicht gesetzt: %s", slug, type(error).__name__)
        return False
    return True


def _valid_url(value) -> bool:
    """Wie das Add-on (__main__._url_fehler): Option nur https mit Host (5c F/Q4)."""
    if not isinstance(value, str):
        return False
    try:
        parts = urllib.parse.urlsplit(value)
        return parts.scheme == "https" and bool(parts.hostname)
    except ValueError:
        return False


@dataclass(frozen=True)
class BootstrapUrl:
    """Ergebnis von async_bootstrap_url: entweder die Adresse (`url`) oder der Abbruchgrund (`grund` = Schluessel
    unter config.abort in strings.json): "bootstrap_url_ungueltig", "repository_mehrdeutig" oder
    "fremde_heizungsbruecke"; genau eines von beiden ist None."""

    url: str | None
    grund: str | None = None


_UNGUELTIG = BootstrapUrl(None, "bootstrap_url_ungueltig")


async def async_bootstrap_url(hass: HomeAssistant) -> BootstrapUrl:
    """Bootstrap-Adresse, die das Add-on benutzt (Option bootstrap_url). Die Produktion gilt nur, wenn gar keine
    Heizungsbruecke installiert ist oder die eigene eindeutig gefunden ist und keine (oder null als) Option hat;
    eine gueltige Option wird benutzt. Sonst nie ein Rueckfall auf die Produktion, sondern ein Grund, mit dem die
    Aufrufer abbrechen: unser Repository mehrfach im Store (`repository_mehrdeutig`), eine Heizungsbruecke ausserhalb
    unseres Repositorys installiert (`fremde_heizungsbruecke`, auch neben der eigenen), ungueltige oder nicht lesbare
    Option, doppelt installierte eigene Bruecke (`bootstrap_url_ungueltig`; die Option laesst das Add-on im
    Ruhezustand). Wirft nie."""
    try:
        if not is_hassio(hass):
            return BootstrapUrl(DEFAULT_BASE_URL)
        repository_slugs = await async_repository_slugs(hass)
        if len(repository_slugs) > 1:
            _LOGGER.warning("Das SmartHeat-Repository ist mehrfach im Add-on-Store eingetragen")
            return BootstrapUrl(None, "repository_mehrdeutig")
        repository_slug = repository_slugs[0] if repository_slugs else None
        client = get_supervisor_client(hass)
        installed = await client.addons.list()
        if fremde_bruecken(installed, repository_slug, HEIZUNGSBRUECKE_ADDON_SLUG):
            _LOGGER.warning("Eine Heizungsbruecke ausserhalb des SmartHeat-Repositorys ist installiert")
            return BootstrapUrl(None, "fremde_heizungsbruecke")
        own = matching_addons(installed, repository_slug, HEIZUNGSBRUECKE_ADDON_SLUG)
        if not own:
            return BootstrapUrl(DEFAULT_BASE_URL)
        if len(own) > 1:
            _LOGGER.warning("Die Heizungsbruecke ist mehrfach installiert")
            return _UNGUELTIG
        options = (await client.addons.addon_info(own[0].slug)).options
        value = options.get(OPTION_BOOTSTRAP_URL) if isinstance(options, dict) else None
    except Exception as error:  # noqa: BLE001 - Optionen nicht lesbar: nicht raten
        _LOGGER.warning("Add-on-Optionen nicht lesbar (%s)", type(error).__name__)
        return _UNGUELTIG
    if value is None:
        return BootstrapUrl(DEFAULT_BASE_URL)
    if _valid_url(value):
        return BootstrapUrl(value.rstrip("/"))
    _LOGGER.warning("Option bootstrap_url des Add-ons ist ungueltig")
    return _UNGUELTIG


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


async def async_entfernen_melden(hass: HomeAssistant, geraet_id: object) -> None:
    """Meldet das Geraet beim Loeschen des Eintrags ab: schreibt `entfernen <geraet_id>` als
    stdin-Zeile an die eigene Heizungsbruecke (Supervisor `write_addon_stdin`); die Bruecke meldet das Geraet lokal ab
    (Markierung, Ursprungswerte). Kein Server-Aufruf; aus dem Konto entfernt der Kunde das Geraet im Portal. Wirft nie:
    jeder Fehler (keine oder ungueltige Geraete-ID, kein Supervisor, Bruecke fehlt oder mehrfach, Supervisor-Fehler,
    Add-on gestoppt) ist eine WARNING ohne den Wert, der Eintrag wird trotzdem entfernt. `fullmatch`, damit kein
    Zeilenumbruch eine zweite Zeile einschmuggelt."""
    if not isinstance(geraet_id, str) or not GERAET_ID_RE.fullmatch(geraet_id):
        _LOGGER.warning("Eintrag ohne gueltige Geraete-ID geloescht: Geraet im Portal abmelden")
        return
    try:
        if not is_hassio(hass):
            _LOGGER.warning("Kein Supervisor: das Geraet laesst sich nicht lokal abmelden, Geraet im Portal abmelden")
            return
        slug = await async_bridge_slug(hass)
        if slug is None:
            _LOGGER.warning("Heizungsbruecke nicht gefunden: Geraet im Portal abmelden")
            return
        zeile = STDIN_ZEILE_FORMAT.format(befehl=STDIN_BEFEHL_ENTFERNEN, geraet_id=geraet_id)
        await get_supervisor_client(hass).addons.write_addon_stdin(slug, zeile.encode())
    except Exception as error:  # noqa: BLE001 - der Eintrag wird trotzdem entfernt
        _LOGGER.warning("Abmelden des Geraets bei der Heizungsbruecke gescheitert (%s): Geraet im Portal abmelden",
                        type(error).__name__)


class NachweisFehlt(Exception):
    """Das Add-on hat in der Wartefrist keinen Nachweis geliefert: es laeuft nicht, ist zu alt, nicht erreichbar oder
    die Geraete-ID ist ungueltig. Der Text nennt nie einen Wert."""


def _nachweis_aus(daten: object, geraet_id: str) -> dict | None:
    """Der Nachweis aus den Daten des Ereignisses, oder None, wenn sie nicht zu diesem Geraet und Schema passen oder ein
    Feld unbrauchbar ist (ein fremdes oder kaputtes Ereignis wird uebergangen, es bricht nichts ab)."""
    if not isinstance(daten, dict) or not all(feld in daten for feld in NACHWEIS_EVENT_FIELDS):
        return None
    schema, zeitstempel, signatur = daten.get("schema"), daten.get("zeitstempel"), daten.get("signatur")
    if (type(schema) is not int or schema != NACHWEIS_EVENT_SCHEMA or daten.get("geraet_id") != geraet_id
            or type(zeitstempel) is not int or zeitstempel <= 0
            or not isinstance(signatur, str) or len(signatur) > NACHWEIS_SIGNATUR_MAX
            or not _SIGNATUR_RE.fullmatch(signatur)):
        return None
    return {"geraet_id": geraet_id, "zeitstempel": zeitstempel, "signatur": signatur}


async def async_nachweis_anfordern(hass: HomeAssistant, geraet_id: object,
                                   wartefrist: float = NACHWEIS_WAIT_SECONDS) -> dict:
    """Fordert den Besitznachweis beim eigenen Add-on an: schreibt `nachweis <geraet_id>` als stdin-Zeile und wartet
    hoechstens `wartefrist` Sekunden auf das Ereignis `smartheat_nachweis`. Gibt `{geraet_id, zeitstempel, signatur}`
    zurueck. Der Lauscher steht vor dem Schreiben (die Antwort kann sofort kommen); nur ein Ereignis nach dem Befehl
    zaehlt, das erste gueltige gewinnt. Wirft `NachweisFehlt` ohne Wartefrist, wenn schon der Befehl nicht ankommt
    (keine oder ungueltige Geraete-ID, kein Supervisor, Bruecke fehlt oder mehrfach, Add-on gestoppt). Signatur und
    Geraete-ID stehen nie im Log; `fullmatch`, damit kein Zeilenumbruch eine zweite Zeile einschmuggelt."""
    if not isinstance(geraet_id, str) or not GERAET_ID_RE.fullmatch(geraet_id):
        raise NachweisFehlt("Keine gueltige Geraete-ID")
    antwort: asyncio.Future[dict] = hass.loop.create_future()

    @callback
    def _ereignis(event: Event) -> None:
        if not antwort.done() and (nachweis := _nachweis_aus(event.data, geraet_id)) is not None:
            antwort.set_result(nachweis)

    unsub = hass.bus.async_listen(NACHWEIS_EVENT, _ereignis)
    try:
        try:
            if not is_hassio(hass):
                raise NachweisFehlt("Kein Supervisor")
            slug = await async_bridge_slug(hass)
            if slug is None:
                raise NachweisFehlt("Heizungsbruecke nicht gefunden")
            zeile = STDIN_ZEILE_FORMAT.format(befehl=STDIN_BEFEHL_NACHWEIS, geraet_id=geraet_id)
            await get_supervisor_client(hass).addons.write_addon_stdin(slug, zeile.encode())
        except NachweisFehlt:
            raise
        except Exception as error:  # noqa: BLE001 - jeder Fehler hier heisst: kein Nachweis
            _LOGGER.warning("Nachweis beim Heizungsbruecke-Add-on nicht angefordert (%s)", type(error).__name__)
            raise NachweisFehlt("Befehl nicht zugestellt") from None
        try:
            async with asyncio.timeout(wartefrist):
                return await antwort
        except TimeoutError:
            _LOGGER.warning("Das Heizungsbruecke-Add-on hat keinen Nachweis geliefert (aus oder zu alt?)")
            raise NachweisFehlt("Kein Nachweis in der Wartefrist") from None
    finally:
        unsub()
