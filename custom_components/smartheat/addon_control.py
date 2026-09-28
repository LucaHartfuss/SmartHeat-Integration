"""Add-on-Steuerung der Integration (Spec TP7 2.5-2.7): Optionen zusammenfuehren, Watchdog und
Boot setzen, auf das Status-Event warten, beim Entfernen abmelden. Nur das Add-on schreibt auf die
Anlage; die Integration steuert nur die Add-ons."""
from __future__ import annotations

import asyncio
import logging

from aiohasupervisor.exceptions import SupervisorError
from aiohasupervisor.models import AddonBoot, AddonsOptions
from homeassistant.components import persistent_notification
from homeassistant.components.hassio import AddonError, AddonManager, get_supervisor_client
from homeassistant.core import Event, HomeAssistant, callback

from .const import (
    ADDON_SPECS, BRIDGE_CREDENTIAL_OPTIONS, CLOUDFLARED_ADDON_SLUG, CLOUDFLARED_CREDENTIAL_OPTIONS,
    HEIZUNGSBRUECKE_ADDON_SLUG, OPTION_ABGEMELDET, SIGN_OFF_WAIT_SECONDS, STATUS_ABGEMELDET, STATUS_EVENT,
    STATUS_EVENT_SCHEMA, watchdog_notification_id,
)
from .supervisor_client import async_find_addon_managers

_LOGGER = logging.getLogger(__name__)

WAIT_DONE = "done"
WAIT_FAILED = "failed"
WAIT_TIMEOUT = "timeout"


class StatusListener:
    """Hoert auf smartheat_status eines Tenants und merkt sich das letzte Event. So sieht auch ein
    spaeteres "Erneut pruefen" ein Event, das zwischendurch kam (Praezisierung 12)."""

    def __init__(self, hass: HomeAssistant, tenant_id: str) -> None:
        self._tenant_id = tenant_id
        self._latest: dict | None = None
        self._changed = asyncio.Event()
        self._unsub = hass.bus.async_listen(STATUS_EVENT, self._handle)

    @callback
    def _handle(self, event: Event) -> None:
        data = event.data
        if data.get("tenant_id") != self._tenant_id or data.get("schema") != STATUS_EVENT_SCHEMA:
            return
        self._latest = dict(data)
        self._changed.set()

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
            _LOGGER.warning("Watchdog/Boot fuer Add-on %s nicht gesetzt: %s", slug, error)
            failed.append(slug)
    return failed


async def async_sign_off(hass: HomeAssistant, tenant_id: str) -> None:
    """Entfernen (Spec TP7 2.7), jeder Schritt best effort: Heizungsbruecke abmelden (ein laufender
    Boost wird zurueckgesetzt), hoechstens SIGN_OFF_WAIT_SECONDS auf `abgemeldet` warten, beide
    Add-ons stoppen (manuell, der Watchdog greift nicht), Watchdog/Boot aus, Zugangsdaten in den
    Optionen leeren, eigene Benachrichtigung entfernen. Serverseitig bleibt der MQTT-Benutzer bis
    zum naechsten provision() oder zur Kuendigung gueltig (TP8)."""
    try:
        managers = await async_find_addon_managers(hass, ADDON_SPECS)
    except AddonError as error:
        _LOGGER.warning("Entfernen: Supervisor nicht erreichbar, Add-ons nicht abgemeldet: %s", error)
        managers = {}
    bridge = managers.get(HEIZUNGSBRUECKE_ADDON_SLUG)
    cloudflared = managers.get(CLOUDFLARED_ADDON_SLUG)
    if bridge is not None:
        await _async_sign_off_bridge(hass, bridge, tenant_id)
    present = [manager for manager in (bridge, cloudflared) if manager is not None]
    for manager in present:
        try:
            await manager.async_stop_addon()
        except AddonError as error:
            _LOGGER.warning("Entfernen: Add-on %s nicht gestoppt: %s", manager.addon_slug, error)
    if present:
        await async_set_supervision(hass, [manager.addon_slug for manager in present], enabled=False)
    for manager, keys in ((bridge, BRIDGE_CREDENTIAL_OPTIONS), (cloudflared, CLOUDFLARED_CREDENTIAL_OPTIONS)):
        if manager is None:
            continue
        try:
            await async_update_addon_options(manager, {key: "" for key in keys})
        except AddonError as error:
            _LOGGER.warning("Entfernen: Zugangsdaten von %s nicht geleert: %s", manager.addon_slug, error)
    persistent_notification.async_dismiss(hass, watchdog_notification_id(tenant_id))


async def _async_sign_off_bridge(hass: HomeAssistant, bridge: AddonManager, tenant_id: str) -> None:
    listener = StatusListener(hass, tenant_id)
    try:
        await async_update_addon_options(bridge, {OPTION_ABGEMELDET: True})
        await bridge.async_restart_addon()
        outcome, grund = await listener.async_wait(
            setup_id=None, done=frozenset({STATUS_ABGEMELDET}), failed=frozenset(), timeout=SIGN_OFF_WAIT_SECONDS,
        )
    except AddonError as error:
        _LOGGER.warning("Entfernen: Heizungsbruecke nicht abgemeldet: %s", error)
        return
    finally:
        listener.close()
    if outcome != WAIT_DONE:
        _LOGGER.warning("Entfernen: keine Abmeldung der Heizungsbruecke innerhalb von %s s", SIGN_OFF_WAIT_SECONDS)
    elif grund:
        _LOGGER.warning("Entfernen: Heizungsbruecke abgemeldet, aber: %s", grund)
