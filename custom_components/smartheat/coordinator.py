"""Status und zweiter Waechter je Config-Entry (Spec TP7 1.2, 1.3). Die Integration laeuft im
HA-Kern, unabhaengig vom Zustand der Add-ons: sie haelt den letzten Status-Event des Add-ons,
prueft alle WATCHDOG_INTERVAL_SECONDS beim Supervisor, ob beide Add-ons laufen und ob sich die
Heizungsbruecke meldet, meldet Befunde selbst (Push an die notify_services des Eintrags,
persistent_notification) und startet das Add-on (neu), hoechstens MAX_RESTARTS_PER_WINDOW mal je
Stunde. Sie schreibt nie auf die Anlage (keine Notbremse, Nutzer-Entscheidung)."""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.components import persistent_notification
from homeassistant.components.hassio import AddonError, AddonState
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    ADDON_DISPLAY_NAMES,
    ADDON_SPECS,
    ADDON_STATUS_VALUES,
    HEIZUNGSBRUECKE_ADDON_SLUG,
    MAX_RESTARTS_PER_WINDOW,
    OPTION_NOTIFY_SERVICES,
    RESTART_WINDOW_SECONDS,
    SILENCE_SECONDS,
    STATUS_ADDON_GESTOPPT,
    STATUS_EVENT,
    STATUS_EVENT_SCHEMA,
    STATUS_REAGIERT_NICHT,
    STATUS_ZUGANG_ABGELEHNT,
    STOPPED_AFTER_CHECKS,
    WATCHDOG_ALL_CLEAR_MESSAGE,
    WATCHDOG_INTERVAL_SECONDS,
    WATCHDOG_MESSAGES,
    WATCHDOG_REASONS,
    signal_update,
    watchdog_notification_id,
)
from .supervisor_client import async_find_addon_managers

_LOGGER = logging.getLogger(__name__)


def _now() -> float:
    """Eigene Zeitquelle: Tests ersetzen sie, ohne die Uhr der Event-Loop anzufassen."""
    return time.monotonic()


@dataclass
class Watchdog:
    """Reine Entscheidungslogik des zweiten Waechters, Zeiten in Sekunden (monoton).

    Waehrend ein Befund aktiv ist (`incident_since` gesetzt), bleibt der zuletzt ermittelte Status
    aktiv -- auch wenn zwischenzeitlich wieder alles laeuft --, bis NACH dem Beginn des Vorfalls ein
    Event kam UND alle Add-ons laufen. Ohne diese Regel gaebe es eine falsche Entwarnung, sobald der
    Supervisor-Neustart selbst schon "laeuft" meldet, obwohl das Add-on sich noch nicht gemeldet
    hat."""
    started_at: float
    last_event_at: float | None = None
    not_running: dict[str, int] = field(default_factory=dict)
    restarts: dict[str, deque] = field(default_factory=dict)
    incident_since: float | None = None
    incident_status: str | None = None
    incident_addons: list[str] = field(default_factory=list)

    def event_received(self, now: float) -> None:
        self.last_event_at = now

    def check(self, now: float, running: dict[str, bool]) -> tuple[str | None, list[str]]:
        """(Waechter-Status oder None, config-Slugs, die jetzt (neu) gestartet werden). `running`:
        config-Slug -> laeuft laut Supervisor. "Laeuft nicht" zaehlt erst ab STOPPED_AFTER_CHECKS
        Pruefungen in Folge (der Supervisor-Watchdog hat Vortritt); Stille zaehlt ab dem spaeteren
        von letztem Event und letztem eigenen (Neu-)Start eines Add-ons -- ein frisch gestartetes
        Add-on bekommt so eine Schonfrist, bevor es erneut als "reagiert nicht" gilt, statt sofort
        wieder gemeldet zu werden. Ohne Event und ohne eigenen Start zaehlt die
        Stille ab dem Setup (Anlaufschutz nach einem HA-Neustart). Ein einmal begonnener Vorfall
        bleibt aktiv, bis er nach `_incident_resolved` beendet ist (siehe dort)."""
        for config_slug, is_running in running.items():
            self.not_running[config_slug] = 0 if is_running else self.not_running.get(config_slug, 0) + 1
        if self._incident_resolved(running):
            self.incident_since = None
            self.incident_status = None
            self.incident_addons = []
            return None, []
        stopped = self.stopped_addons()
        if stopped:
            status, revive, addons = STATUS_ADDON_GESTOPPT, stopped, stopped
        else:
            silent_since = self._silence_reference()
            if running.get(HEIZUNGSBRUECKE_ADDON_SLUG) and now - silent_since >= SILENCE_SECONDS:
                status, revive, addons = STATUS_REAGIERT_NICHT, [HEIZUNGSBRUECKE_ADDON_SLUG], [HEIZUNGSBRUECKE_ADDON_SLUG]
            elif self.incident_since is not None:
                # Vorfall laeuft weiter (z. B. Add-on wieder gestartet, aber noch kein Event): der
                # zuletzt ermittelte Status UND die dazugehoerigen Add-on-Namen bleiben bestehen
                # (sonst waere der Grund leer, sobald der Zaehler zurueckgesetzt ist), ohne neuen
                # Startversuch.
                status, revive, addons = self.incident_status, [], self.incident_addons
            else:
                return None, []
        if self.incident_since is None:
            self.incident_since = now
        self.incident_status = status
        self.incident_addons = addons
        return status, self._allowed(now, revive)

    def _incident_resolved(self, running: dict[str, bool]) -> bool:
        """Entwarnung nur, wenn NACH dem Beginn des Vorfalls ein Event kam und dabei (bzw. seither)
        alle Add-ons laufen (Spec 1.3: "Kommt wieder ein Event und laufen beide Add-ons")."""
        if self.incident_since is None:
            return False
        if self.last_event_at is None or self.last_event_at <= self.incident_since:
            return False
        return all(running.values())

    def stopped_addons(self) -> list[str]:
        return [config_slug for config_slug, count in self.not_running.items() if count >= STOPPED_AFTER_CHECKS]

    def incident_addon_slugs(self) -> list[str]:
        """Die Add-on-Slugs, um die es im aktuell aktiven Vorfall geht -- fuer den Meldungstext.
        Anders als `stopped_addons()` bleibt das erhalten, waehrend der Vorfall (noch ohne
        bestaetigendes Event) weiterlaeuft, auch wenn das Add-on zwischenzeitlich wieder laeuft
        und der Zaehler dafuer schon zurueckgesetzt ist."""
        return list(self.incident_addons)

    def _silence_reference(self) -> float:
        reference = self.started_at if self.last_event_at is None else self.last_event_at
        own_restarts = self.restarts.get(HEIZUNGSBRUECKE_ADDON_SLUG)
        if own_restarts:
            reference = max(reference, own_restarts[-1])
        return reference

    def _allowed(self, now: float, config_slugs: list[str]) -> list[str]:
        allowed = []
        for config_slug in config_slugs:
            recent = self.restarts.setdefault(config_slug, deque())
            while recent and now - recent[0] >= RESTART_WINDOW_SECONDS:
                recent.popleft()
            if len(recent) < MAX_RESTARTS_PER_WINDOW:
                recent.append(now)
                allowed.append(config_slug)
        return allowed


class SmartHeatCoordinator:
    """Letzter Status-Event und Waechter-Befund eines Eintrags; verteilt Aenderungen per
    Dispatcher-Signal an die Entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.tenant_id: str = entry.data["tenant_id"]
        self.data: dict | None = None
        self.watchdog_status: str | None = None
        self._watchdog_reason: str | None = None
        self._watchdog = Watchdog(started_at=_now())
        self._checking = False

    @property
    def signal(self) -> str:
        return signal_update(self.entry.entry_id)

    @property
    def status(self) -> str | None:
        """Waechter-Befund vor dem Status des Add-ons; None, solange nichts bekannt ist."""
        if self.watchdog_status is not None:
            return self.watchdog_status
        return None if self.data is None else self.data["status"]

    @property
    def reason(self) -> str | None:
        if self.watchdog_status is not None:
            return self._watchdog_reason
        return None if self.data is None else self.data.get("grund")

    @callback
    def async_start(self) -> None:
        self._watchdog = Watchdog(started_at=_now())
        self.entry.async_on_unload(self.hass.bus.async_listen(STATUS_EVENT, self.async_handle_event))
        self.entry.async_on_unload(async_track_time_interval(
            self.hass, self.async_check, timedelta(seconds=WATCHDOG_INTERVAL_SECONDS),
        ))

    @callback
    def async_handle_event(self, event: Event) -> None:
        data = event.data
        if data.get("tenant_id") != self.tenant_id:
            return
        if data.get("schema") != STATUS_EVENT_SCHEMA or data.get("status") not in ADDON_STATUS_VALUES:
            _LOGGER.warning(
                "Status-Event mit unbekanntem schema/status ignoriert: %r/%r", data.get("schema"), data.get("status"),
            )
            return
        previous = None if self.data is None else self.data["status"]
        self.data = dict(data)
        self._watchdog.event_received(_now())
        if data["status"] == STATUS_ZUGANG_ABGELEHNT and previous != STATUS_ZUGANG_ABGELEHNT:
            self.entry.async_start_reauth(self.hass)
        if self.watchdog_status is not None:
            # Entwarnung pruefen: gilt erst, wenn auch beide Add-ons laufen. An den Eintrag
            # gebunden: wird beim Entladen/Entfernen storniert -- sonst koennte dieser Task nach
            # async_sign_off noch Add-ons (neu) starten, die absichtlich gestoppt wurden, oder
            # parallel zum Zeit-Takt laufen.
            self.entry.async_create_task(self.hass, self.async_check(), f"smartheat_recheck_{self.tenant_id}")
        async_dispatcher_send(self.hass, self.signal)

    async def async_check(self, _now_dt=None) -> None:
        """Eine Pruefung; nie zwei gleichzeitig (M1): der Zeit-Takt und der Event-ausgeloeste
        Recheck koennen sonst ueberlappen und den Waechter-Zustand doppelt fortschreiben."""
        if self._checking:
            return
        self._checking = True
        try:
            try:
                managers = await async_find_addon_managers(self.hass, ADDON_SPECS)
            except AddonError as error:
                _LOGGER.warning("Waechter: Supervisor nicht erreichbar, Pruefung faellt aus: %s", error)
                return
            running = {}
            for config_slug, manager in managers.items():
                state = await self._async_is_running(manager)
                if state is None:
                    return  # Zustand unbekannt: kein Zaehler, kein Alarm
                running[config_slug] = state
            status, revive = self._watchdog.check(_now(), running)
            for config_slug in revive:
                await self._async_revive(managers[config_slug], running[config_slug])
            self._set_watchdog_status(status)
        finally:
            self._checking = False

    async def _async_is_running(self, manager) -> bool | None:
        if manager is None:
            return False  # nicht (oder mehrfach) installiert
        try:
            info = await manager.async_get_addon_info()
        except AddonError as error:
            _LOGGER.warning("Waechter: Zustand von %s nicht abfragbar: %s", manager.addon_slug, error)
            return None
        return info.state == AddonState.RUNNING

    async def _async_revive(self, manager, running: bool) -> None:
        if manager is None:
            _LOGGER.warning("Waechter: Add-on nicht (eindeutig) installiert, kein Start moeglich")
            return
        try:
            if running:
                await manager.async_restart_addon()
            else:
                await manager.async_start_addon()
        except AddonError as error:
            _LOGGER.warning("Waechter: Add-on %s konnte nicht gestartet werden: %s", manager.addon_slug, error)

    @callback
    def _set_watchdog_status(self, status: str | None) -> None:
        """Push-Meldung und persistent_notification nur beim Beginn (None -> kritisch) und beim
        Ende (kritisch -> None) eines Vorfalls. Eskaliert ein laufender Vorfall (z. B. gestoppt ->
        reagiert nicht oder umgekehrt), wird nur der Text der bestehenden persistent_notification
        aktualisiert (gleiche notification_id, kein neuer Eintrag) -- keine zweite Push-Meldung,
        obwohl der Grund sich aendert: es ist derselbe Vorfall, keine zweite Stoerung."""
        if status == STATUS_ADDON_GESTOPPT:
            names = ", ".join(ADDON_DISPLAY_NAMES.get(s, s) for s in self._watchdog.incident_addon_slugs())
            reason = WATCHDOG_REASONS[status].format(addons=names)
        else:
            # status kann hier None sein (Entwarnung); WATCHDOG_REASONS kennt nur echte
            # Waechter-Status als Schluessel, ein None-Lookup ergaebe ohnehin None.
            reason = WATCHDOG_REASONS.get(status) if status is not None else None
        previous = self.watchdog_status
        changed = (status, reason) != (previous, self._watchdog_reason)
        self.watchdog_status, self._watchdog_reason = status, reason
        if previous is None and status is not None:
            self._notify(WATCHDOG_MESSAGES[status])
            persistent_notification.async_create(
                self.hass, WATCHDOG_MESSAGES[status], "SmartHeat", watchdog_notification_id(self.tenant_id),
            )
        elif previous is not None and status is None:
            self._notify(WATCHDOG_ALL_CLEAR_MESSAGE)
            persistent_notification.async_dismiss(self.hass, watchdog_notification_id(self.tenant_id))
        elif previous is not None and status is not None and status != previous:
            persistent_notification.async_create(
                self.hass, WATCHDOG_MESSAGES[status], "SmartHeat", watchdog_notification_id(self.tenant_id),
            )
        if changed:
            async_dispatcher_send(self.hass, self.signal)

    @callback
    def _notify(self, message: str) -> None:
        for service in self.entry.options.get(OPTION_NOTIFY_SERVICES, []):
            domain, _, name = service.partition(".")
            self.hass.async_create_task(self._async_push(domain, name, message))

    async def _async_push(self, domain: str, name: str, message: str) -> None:
        try:
            await self.hass.services.async_call(domain, name, {"title": "SmartHeat", "message": message}, blocking=True)
        except Exception:  # noqa: BLE001 - Push ist best effort
            _LOGGER.warning("Push-Meldung an %s.%s nicht gesendet", domain, name)
