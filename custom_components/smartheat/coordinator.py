"""Status und zweiter Waechter je Config-Entry. Die Integration laeuft im
HA-Kern, unabhaengig vom Zustand des Add-ons: sie haelt das letzte lesbare Status-Ereignis ihres Geraets
(Zuordnung ueber die Geraete-ID, der Tenant fehlt vor der Einrichtung), macht aus dessen Hinweisen
Reparatur-Hinweise, prueft alle WATCHDOG_INTERVAL_SECONDS beim Supervisor, ob die Heizungsbruecke laeuft und sich
meldet, meldet Befunde per persistent_notification (Push an die Handys ist Sache des Add-ons, Nutzer-Entscheidung
2026-10-11) und startet das Add-on (neu), hoechstens MAX_RESTARTS_PER_WINDOW mal je Stunde. Jedes Ereignis des
eigenen Geraets zaehlt als Lebenszeichen des Add-ons, gleich welches Schema; umgekehrt meldet die Integration dem
Add-on ihre Version und gelesenen Schemata (INTEGRATION_EVENT). Sie schreibt nie auf die Anlage (keine Notbremse,
Nutzer-Entscheidung) und ruft keinen Server."""
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
    ERSTER_KREIS,
    INTEGRATION_EVENT,
    INTEGRATION_EVENT_FIELDS,
    INTEGRATION_EVENT_SCHEMA,
    MAX_RESTARTS_PER_WINDOW,
    RESTART_WINDOW_SECONDS,
    SILENCE_SECONDS,
    STATUS_ADDON_GESTOPPT,
    STATUS_ADDON_VERALTET,
    STATUS_EVENT,
    STATUS_EVENT_FIELDS,
    STATUS_INTEGRATION_VERALTET,
    STATUS_KREIS_FIELDS,
    STATUS_REAGIERT_NICHT,
    STATUS_STARTET,
    STOPPED_AFTER_CHECKS,
    V5_SCHEMAS,
    WATCHDOG_INTERVAL_SECONDS,
    WATCHDOG_MESSAGES,
    WATCHDOG_REASONS,
    WUNSCH_EVENT_SCHEMA,
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


def _kreise(liste) -> dict[int, dict] | None:
    """Die Kreise des Ereignisses nach Nummer; None, wenn die Liste fehlt, ein Eintrag kein Objekt mit gueltiger
    Nummer (ganze Zahl ab 1, kein bool) ist oder eine Nummer doppelt vorkommt."""
    if not isinstance(liste, list):
        return None
    kreise: dict[int, dict] = {}
    for eintrag in liste:
        nummer = eintrag.get("kreis") if isinstance(eintrag, dict) else None
        if not isinstance(nummer, int) or isinstance(nummer, bool) or nummer < 1 or nummer in kreise:
            return None
        kreise[nummer] = {key: eintrag[key] for key in STATUS_KREIS_FIELDS if key in eintrag}
    return kreise


def _versionsbefund(schema: object) -> str | None:
    """Der Versionsbefund zu einem Schema: ueber allen gelesenen Schemata `integration_veraltet` (das Add-on ist
    neuer), unter allen `addon_veraltet`; ein gelesenes, ein dazwischen liegendes oder kein ganzzahliges Schema gibt
    keinen Befund."""
    if isinstance(schema, bool) or not isinstance(schema, int):
        return None
    if schema > max(V5_SCHEMAS):
        return STATUS_INTEGRATION_VERALTET
    if schema < min(V5_SCHEMAS):
        return STATUS_ADDON_VERALTET
    return None


class SmartHeatCoordinator:
    """Letzter Status-Event und Waechter-Befund eines Eintrags; verteilt Aenderungen per
    Dispatcher-Signal an die Entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.tenant_id: str = entry.data["tenant_id"]
        self.geraet_id: str = entry.data["geraet_id"]
        self.data: dict | None = None  # Felder der Anlage (ohne `kreise`)
        self.kreise: dict[int, dict] = {}
        self.watchdog_status: str | None = None
        # Versionsbefund (integration_veraltet/addon_veraltet) aus dem Schema des letzten eigenen Ereignisses; None, wenn
        # die Integration es liest. `wunsch_schema`: das zuletzt gelesene Schema, in dem die Integration antwortet.
        self.version_status: str | None = None
        self.wunsch_schema: int = WUNSCH_EVENT_SCHEMA
        self._watchdog_reason: str | None = None
        self._watchdog = Watchdog(started_at=_now())
        self._checking = False
        self._stopped = False
        self._version = ""

    @property
    def signal(self) -> str:
        return signal_update(self.entry.entry_id)

    def kreis(self, nummer: int) -> dict | None:
        """Die Felder eines Kreises aus dem letzten Ereignis; None, wenn die Anlage ihn nicht meldet."""
        return self.kreise.get(nummer)

    @property
    def sicht(self) -> dict | None:
        """Anlage und erster Kreis in einem Dict: das lesen die Entities, bis sie je Kreis angelegt werden."""
        if self.data is None:
            return None
        return {**self.data, **(self.kreis(ERSTER_KREIS) or {})}

    @property
    def status(self) -> str | None:
        """Waechter-Befund, dann Versionsbefund, dann Status des Add-ons; None, solange nichts bekannt ist. Der Status des
        Add-ons ist unveraendert der des Ereignisses, auch ein unbekannter oder fehlender."""
        if self.watchdog_status is not None:
            return self.watchdog_status
        if self.version_status is not None:
            return self.version_status
        return None if self.data is None else self.data.get("status")

    @property
    def reason(self) -> str | None:
        if self.watchdog_status is not None:
            return self._watchdog_reason
        if self.version_status is not None:
            return self.version_status
        return None if self.data is None else self.data.get("grund")

    @callback
    def async_start(self, version: str) -> None:
        """Startet Zuhoeren und Waechter-Takt und meldet dem Add-on die Version der Integration."""
        self._version = version
        self._watchdog = Watchdog(started_at=_now())
        self._stopped = False
        self.entry.async_on_unload(self._async_stop)
        self.entry.async_on_unload(self.hass.bus.async_listen(STATUS_EVENT, self.async_handle_event))
        self.entry.async_on_unload(async_track_time_interval(
            self.hass, self.async_check, timedelta(seconds=WATCHDOG_INTERVAL_SECONDS),
        ))
        self._async_send_integration_event()

    @callback
    def _async_send_integration_event(self) -> None:
        """Lebenszeichen der Integration: nur Schema, Version und gelesene Schemata, ohne Zeit und ohne Kennung."""
        werte = {"schema": INTEGRATION_EVENT_SCHEMA, "version": self._version, "v5_schemas": list(V5_SCHEMAS)}
        self.hass.bus.async_fire(INTEGRATION_EVENT, {feld: werte[feld] for feld in INTEGRATION_EVENT_FIELDS})

    @callback
    def _async_stop(self) -> None:
        """Der Eintrag wird entladen: eine noch laufende Pruefung legt danach keine Meldung mehr an."""
        self._stopped = True

    @callback
    def async_handle_event(self, event: Event) -> None:
        """Nur Ereignisse des eigenen Geraets (geraet_id). Ein Schema ausserhalb der gelesenen (V5_SCHEMAS) setzt den
        Versionsbefund, ein gelesenes hebt ihn auf; ein Schema dazwischen oder ohne Ganzzahl aendert nichts (Warnung).
        Vor der Einrichtung ist der Laufzeit-Teil leer (status startet, tenant_id None, keine Kreise); den Zustand des
        Geraets traegt dann geraet_zustand. Ein Kreis ohne gueltige Nummer oder eine doppelte Nummer verwirft das ganze
        Ereignis (Warnung ohne Kreisnamen); ein unbekannter Status nicht."""
        data = event.data
        if data.get("geraet_id") != self.geraet_id:
            return
        # Jedes Ereignis des eigenen Geraets ist ein Lebenszeichen des Add-ons, gleich welches Schema und welcher Inhalt:
        # ein neueres Add-on darf nicht als stumm gelten und neu gestartet werden, nur weil die Integration es nicht liest.
        self._watchdog.event_received(_now())
        if data.get("status") == STATUS_STARTET:
            self._async_send_integration_event()  # frisch gestartetes Add-on: Version und Schemata gleich nennen
        if self.watchdog_status is not None:
            # Entwarnung pruefen: gilt erst, wenn auch das Add-on laeuft. An den Eintrag gebunden: wird beim
            # Entladen/Entfernen storniert -- sonst koennte dieser Task danach noch das Add-on (neu) starten oder
            # parallel zum Zeit-Takt laufen.
            self.entry.async_create_task(self.hass, self.async_check(), f"smartheat_recheck_{self.tenant_id}")
        schema = data.get("schema")
        befund = _versionsbefund(schema)
        if befund is not None:
            # Das Add-on spricht ein Schema, das diese Integration nicht liest: Befund statt Stillstand. Die zuletzt
            # gelesenen Werte bleiben stehen, nur der Zustand wechselt; den Text der Mindestversion liefert der Kern.
            self._set_version_status(befund, data.get("mindest_integration"))
            return
        if isinstance(schema, bool) or schema not in V5_SCHEMAS:
            _LOGGER.warning("Status-Event mit nicht aushandelbarem schema ignoriert: %r", schema)
            return
        # Ein unbekannter Status oder Hinweis verwirft das Ereignis nicht: er gilt als das, was das Add-on gemeldet hat.
        kreise = _kreise(data.get("kreise"))
        if kreise is None:
            _LOGGER.warning("Status-Event mit ungueltiger Kreisliste ignoriert")
            return
        self.wunsch_schema = schema
        # Nur die Felder des Vertrags (STATUS_EVENT_FIELDS, STATUS_KREIS_FIELDS); fehlende bleiben fehlend.
        self.data = {key: data[key] for key in STATUS_EVENT_FIELDS if key in data and key != "kreise"}
        self.kreise = kreise
        hinweise = data.get("geraet_hinweise")
        repairs.async_update(self.hass, hinweise if isinstance(hinweise, list) else [])
        self._set_version_status(None)  # sendet das Signal in jedem Fall

    @callback
    def _set_version_status(self, befund: str | None, mindest_integration: object = None) -> None:
        """Versionsbefund setzen oder (None) aufheben, Repair nachziehen und die Entities benachrichtigen."""
        if befund != self.version_status:
            _LOGGER.info("Versionsbefund des Add-ons: %s", befund or "keiner")
        self.version_status = befund
        repairs.async_set_version(self.hass, befund, mindest_integration)
        async_dispatcher_send(self.hass, self.signal)

    async def async_check(self, _now_dt=None) -> None:
        """Eine Pruefung; nie zwei gleichzeitig: der Zeit-Takt und der Event-ausgeloeste
        Recheck koennen sonst ueberlappen und den Waechter-Zustand doppelt fortschreiben."""
        if self._checking:
            return
        self._checking = True
        try:
            self._async_send_integration_event()  # auch wenn der Supervisor gleich nicht antwortet
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
            if self._stopped:  # der Eintrag wurde waehrend der Abfragen entladen/geloescht: nichts mehr starten
                return
            if revive:
                await self._async_revive(manager, bool(running))
                if not self._stopped:
                    self._async_send_integration_event()  # das neu gestartete Add-on erfaehrt Version und Schemata
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
