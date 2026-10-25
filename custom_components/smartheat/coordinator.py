"""Status und zweiter Waechter je Config-Entry. Die Integration laeuft im
HA-Kern, unabhaengig vom Zustand des Add-ons: sie haelt das letzte Status-Ereignis (Schema 3) ihres Geraets
(Zuordnung ueber die Geraete-ID, der Tenant fehlt vor der Einrichtung), macht aus dessen Hinweisen
Reparatur-Hinweise, prueft alle WATCHDOG_INTERVAL_SECONDS beim Supervisor, ob die Heizungsbruecke laeuft und sich
meldet, meldet Befunde per persistent_notification (Push an die Handys ist Sache des Add-ons, Nutzer-Entscheidung
2026-10-11) und startet das Add-on (neu), hoechstens MAX_RESTARTS_PER_WINDOW mal je Stunde. Sie schreibt nie auf
die Anlage (keine Notbremse, Nutzer-Entscheidung) und ruft keinen Server."""
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

from . import repairs
from .const import (
    ADDON_STATUS_VALUES,
    MAX_RESTARTS_PER_WINDOW,
    RESTART_WINDOW_SECONDS,
    SILENCE_SECONDS,
    STATUS_ADDON_GESTOPPT,
    STATUS_EVENT,
    STATUS_EVENT_FIELDS,
    STATUS_EVENT_SCHEMA,
    STATUS_REAGIERT_NICHT,
    STOPPED_AFTER_CHECKS,
    WATCHDOG_INTERVAL_SECONDS,
    WATCHDOG_MESSAGES,
    WATCHDOG_REASONS,
    signal_update,
    watchdog_notification_id,
)
from .supervisor_client import async_find_bridge_manager

_LOGGER = logging.getLogger(__name__)


def _now() -> float:
    """Eigene Zeitquelle: Tests ersetzen sie, ohne die Uhr der Event-Loop anzufassen."""
    return time.monotonic()


@dataclass
class Watchdog:
    """Reine Entscheidungslogik des zweiten Waechters, Zeiten in Sekunden (monoton).

    Waehrend ein Befund aktiv ist (`incident_since` gesetzt), bleibt der zuletzt ermittelte Status
    aktiv -- auch wenn zwischenzeitlich wieder alles laeuft --, bis NACH dem Beginn des Vorfalls ein
    Event kam UND die Heizungsbruecke nicht als gestoppt gilt. Ohne diese Regel gaebe es eine falsche
    Entwarnung, sobald der Supervisor-Neustart selbst schon "laeuft" meldet, obwohl das Add-on sich
    noch nicht gemeldet hat."""
    started_at: float
    last_event_at: float | None = None
    not_running: int = 0
    restarts: deque = field(default_factory=deque)
    incident_since: float | None = None
    incident_status: str | None = None

    def event_received(self, now: float) -> None:
        self.last_event_at = now

    def check(self, now: float, running: bool | None) -> tuple[str | None, bool]:
        """(Waechter-Status oder None, ob die Heizungsbruecke jetzt (neu) gestartet wird). `running`: laeuft laut
        Supervisor; `None`, wenn sich die Bruecke nicht aufloesen laesst (nicht oder mehrfach installiert, Repository
        unter anderer Adresse): das zaehlt nie als "laeuft nicht" und fuehrt nie zu einem Start, nur die Stille
        meldet dann "reagiert nicht". "Laeuft nicht" zaehlt erst ab STOPPED_AFTER_CHECKS Pruefungen in
        Folge (der Supervisor-Watchdog hat Vortritt); Stille zaehlt ab dem spaeteren von letztem Event und letztem
        eigenen (Neu-)Start -- ein frisch gestartetes Add-on bekommt so eine Schonfrist, bevor es erneut als
        "reagiert nicht" gilt, statt sofort wieder gemeldet zu werden. Ohne Event und ohne eigenen Start zaehlt die
        Stille ab dem Setup (Anlaufschutz nach einem HA-Neustart). Ein einmal begonnener Vorfall bleibt aktiv, bis
        er nach `_incident_resolved` beendet ist (siehe dort)."""
        self.not_running = self.not_running + 1 if running is False else 0
        if self._incident_resolved(running):
            self.incident_since = None
            self.incident_status = None
            return None, False
        if self.not_running >= STOPPED_AFTER_CHECKS:
            status, revive = STATUS_ADDON_GESTOPPT, True
        elif running is not False and now - self._silence_reference() >= SILENCE_SECONDS:
            status, revive = STATUS_REAGIERT_NICHT, running is True
        elif self.incident_since is not None:
            # Vorfall laeuft weiter (z. B. Add-on wieder gestartet, aber noch kein Event): der zuletzt ermittelte
            # Status bleibt bestehen, ohne neuen Startversuch.
            status, revive = self.incident_status, False
        else:
            return None, False
        if self.incident_since is None:
            self.incident_since = now
        self.incident_status = status
        return status, revive and self._restart_allowed(now)

    def _incident_resolved(self, running: bool | None) -> bool:
        """Entwarnung nur, wenn NACH dem Beginn des Vorfalls ein Event kam und die Bruecke dabei (bzw. seither) nicht
        als gestoppt gilt."""
        if self.incident_since is None:
            return False
        if self.last_event_at is None or self.last_event_at <= self.incident_since:
            return False
        return running is not False

    def _silence_reference(self) -> float:
        reference = self.started_at if self.last_event_at is None else self.last_event_at
        if self.restarts:
            reference = max(reference, self.restarts[-1])
        return reference

    def _restart_allowed(self, now: float) -> bool:
        while self.restarts and now - self.restarts[0] >= RESTART_WINDOW_SECONDS:
            self.restarts.popleft()
        if len(self.restarts) < MAX_RESTARTS_PER_WINDOW:
            self.restarts.append(now)
            return True
        return False


class SmartHeatCoordinator:
    """Letzter Status-Event und Waechter-Befund eines Eintrags; verteilt Aenderungen per
    Dispatcher-Signal an die Entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.tenant_id: str = entry.data["tenant_id"]
        self.geraet_id: str = entry.data["geraet_id"]
        self.data: dict | None = None
        self.watchdog_status: str | None = None
        self._watchdog_reason: str | None = None
        self._watchdog = Watchdog(started_at=_now())
        self._checking = False
        self._stopped = False

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
        self._stopped = False
        self.entry.async_on_unload(self._async_stop)
        self.entry.async_on_unload(self.hass.bus.async_listen(STATUS_EVENT, self.async_handle_event))
        self.entry.async_on_unload(async_track_time_interval(
            self.hass, self.async_check, timedelta(seconds=WATCHDOG_INTERVAL_SECONDS),
        ))

    @callback
    def _async_stop(self) -> None:
        """Der Eintrag wird entladen: eine noch laufende Pruefung legt danach keine Meldung mehr an."""
        self._stopped = True

    @callback
    def async_handle_event(self, event: Event) -> None:
        """Nur Ereignisse des eigenen Geraets (geraet_id) im Schema 3. Vor der Einrichtung ist der Laufzeit-Teil leer
        (status startet, tenant_id None); den Zustand des Geraets traegt dann geraet_zustand."""
        data = event.data
        if data.get("geraet_id") != self.geraet_id:
            return
        if data.get("schema") != STATUS_EVENT_SCHEMA or data.get("status") not in ADDON_STATUS_VALUES:
            _LOGGER.warning(
                "Status-Event mit unbekanntem schema/status ignoriert: %r/%r", data.get("schema"), data.get("status"),
            )
            return
        # Nur die Felder des Vertrags (STATUS_EVENT_FIELDS); fehlende bleiben fehlend.
        self.data = {key: data[key] for key in STATUS_EVENT_FIELDS if key in data}
        self._watchdog.event_received(_now())
        hinweise = data.get("geraet_hinweise")
        repairs.async_update(self.hass, hinweise if isinstance(hinweise, list) else [])
        if self.watchdog_status is not None:
            # Entwarnung pruefen: gilt erst, wenn auch das Add-on laeuft. An den Eintrag gebunden: wird beim
            # Entladen/Entfernen storniert -- sonst koennte dieser Task danach noch das Add-on (neu) starten oder
            # parallel zum Zeit-Takt laufen.
            self.entry.async_create_task(self.hass, self.async_check(), f"smartheat_recheck_{self.tenant_id}")
        async_dispatcher_send(self.hass, self.signal)

    async def async_check(self, _now_dt=None) -> None:
        """Eine Pruefung; nie zwei gleichzeitig: der Zeit-Takt und der Event-ausgeloeste
        Recheck koennen sonst ueberlappen und den Waechter-Zustand doppelt fortschreiben."""
        if self._checking:
            return
        self._checking = True
        try:
            try:
                manager = await async_find_bridge_manager(self.hass)
            except AddonError as error:
                # Nicht den Supervisor-Text loggen: er kann Optionswerte zitieren.
                _LOGGER.warning("Waechter: Supervisor nicht erreichbar, Pruefung faellt aus: %s", type(error).__name__)
                return
            running: bool | None = None  # None: Bruecke nicht aufloesbar (siehe Watchdog.check)
            if manager is not None:
                try:
                    info = await manager.async_get_addon_info()
                except AddonError as error:
                    _LOGGER.warning(
                        "Waechter: Zustand von %s nicht abfragbar: %s", manager.addon_slug, type(error).__name__,
                    )
                    return  # Zustand unbekannt: kein Zaehler, kein Alarm
                running = info.state == AddonState.RUNNING
            status, revive = self._watchdog.check(_now(), running)
            if revive:
                await self._async_revive(manager, bool(running))
            if self._stopped:  # waehrend des Starts wurde der Eintrag entladen/geloescht: keine Meldung neu anlegen
                return
            self._set_watchdog_status(status)
        finally:
            self._checking = False

    async def _async_revive(self, manager, running: bool) -> None:
        """Startet die Heizungsbruecke (laeuft sie, aber meldet sich nicht: neu). `manager` ist hier nie None: ohne
        aufgeloeste Bruecke gibt `Watchdog.check` keinen Start frei."""
        try:
            if running:
                await manager.async_restart_addon()
            else:
                await manager.async_start_addon()
        except AddonError as error:
            _LOGGER.warning("Waechter: Add-on %s konnte nicht gestartet werden: %s", manager.addon_slug, type(error).__name__)

    @callback
    def _set_watchdog_status(self, status: str | None) -> None:
        """persistent_notification beim Beginn (None -> kritisch) eines Vorfalls, entfernt an seinem Ende
        (kritisch -> None). Eskaliert ein laufender Vorfall (z. B. gestoppt -> reagiert nicht oder umgekehrt), wird
        nur der Text der bestehenden Meldung aktualisiert (gleiche notification_id, kein neuer Eintrag): es ist
        derselbe Vorfall, keine zweite Stoerung."""
        reason = None if status is None else WATCHDOG_REASONS[status]  # None = Entwarnung
        previous = self.watchdog_status
        changed = (status, reason) != (previous, self._watchdog_reason)
        self.watchdog_status, self._watchdog_reason = status, reason
        if previous is None and status is not None:
            persistent_notification.async_create(
                self.hass, WATCHDOG_MESSAGES[status], "SmartHeat", watchdog_notification_id(self.tenant_id),
            )
        elif previous is not None and status is None:
            persistent_notification.async_dismiss(self.hass, watchdog_notification_id(self.tenant_id))
        elif previous is not None and status is not None and status != previous:
            persistent_notification.async_create(
                self.hass, WATCHDOG_MESSAGES[status], "SmartHeat", watchdog_notification_id(self.tenant_id),
            )
        if changed:
            async_dispatcher_send(self.hass, self.signal)
