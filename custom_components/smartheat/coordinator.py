"""Status und zweiter Waechter je Config-Entry. Die Integration laeuft im
HA-Kern, unabhaengig vom Zustand des Add-ons: sie haelt das letzte lesbare Status-Ereignis ihres Geraets
(Zuordnung ueber die Geraete-ID, der Tenant fehlt vor der Einrichtung), macht aus dessen Hinweisen
Reparatur-Hinweise, prueft alle WATCHDOG_INTERVAL_SECONDS beim Supervisor, ob die Heizungsbruecke laeuft und sich
meldet, meldet Befunde als Repair (Rot-Befunde des Waechters zusaetzlich als Push, siehe push.py) und startet das Add-on (neu), hoechstens MAX_RESTARTS_PER_WINDOW mal je Stunde. Jedes Ereignis des
eigenen Geraets zaehlt als Lebenszeichen des Add-ons, gleich welches Schema; umgekehrt meldet die Integration dem
Add-on ihre Version und gelesenen Schemata (INTEGRATION_EVENT). Sie schreibt nie auf die Anlage (keine Notbremse,
Nutzer-Entscheidung) und ruft keinen Server."""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.components.hassio import AddonError, AddonState
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from . import geraete, repairs
from .const import (
    BRUECKE_FEHLT,
    BRUECKE_GEFUNDEN,
    BRUECKE_MEHRFACH,
    EINRICHTUNG_MAX_KREISE,
    INTEGRATION_EVENT,
    INTEGRATION_EVENT_FIELDS,
    INTEGRATION_EVENT_SCHEMA,
    MAX_RESTARTS_PER_WINDOW,
    MISSING_AFTER_CHECKS,
    PUSH_ABGLEICH_SECONDS,
    PUSH_CODES,
    PUSH_STUFE_ROT,
    RESTART_WINDOW_SECONDS,
    SILENCE_SECONDS,
    STATUS_ADDON_FEHLT,
    STATUS_ADDON_GESTOPPT,
    STATUS_ADDON_MEHRFACH,
    STATUS_ADDON_NICHT_BEREIT,
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
    WATCHDOG_ISSUE_ID,
    WUNSCH_EVENT_SCHEMA,
    signal_update,
)
from .push import Push
from .supervisor_client import async_bruecke_stand

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
    missing: int = 0
    restarts: deque = field(default_factory=deque)
    incident_since: float | None = None
    incident_status: str | None = None

    def event_received(self, now: float) -> None:
        self.last_event_at = now

    def check(self, now: float, running: bool | None, bruecke: str = BRUECKE_GEFUNDEN) -> tuple[str | None, bool]:
        """(Waechter-Status oder None, ob die Heizungsbruecke jetzt (neu) gestartet wird). `running`: laeuft laut
        Supervisor; `None`, wenn sich die Bruecke nicht aufloesen laesst (nicht oder mehrfach installiert, Repository
        unter anderer Adresse): das zaehlt nie als "laeuft nicht" und fuehrt nie zu einem Start, nur die Stille
        meldet dann "reagiert nicht". "Laeuft nicht" zaehlt erst ab STOPPED_AFTER_CHECKS Pruefungen in
        Folge (der Supervisor-Watchdog hat Vortritt); Stille zaehlt ab dem spaeteren von letztem Event und letztem
        eigenen (Neu-)Start -- ein frisch gestartetes Add-on bekommt so eine Schonfrist, bevor es erneut als
        "reagiert nicht" gilt, statt sofort wieder gemeldet zu werden. Ohne Event und ohne eigenen Start zaehlt die
        Stille ab dem Setup (Anlaufschutz nach einem HA-Neustart). Ein einmal begonnener Vorfall bleibt aktiv, bis
        er nach `_incident_resolved` beendet ist (siehe dort).

        `bruecke` ist der Stand laut Supervisor (`BRUECKE_*`). `fehlt` meldet erst ab MISSING_AFTER_CHECKS Pruefungen in
        Folge `addon_fehlt` (jeder andere Stand setzt den Zaehler zurueck), ohne Startversuch; `mehrfach` meldet sofort
        `addon_mehrfach`, ohne Vorfall, Wartezeit und Start. Stille und Stopp haben bei laufendem Vorfall Vortritt vor
        `mehrfach`."""
        self.not_running = self.not_running + 1 if running is False else 0
        self.missing = self.missing + 1 if bruecke == BRUECKE_FEHLT else 0
        if self._incident_resolved(running, bruecke):
            self.incident_since = None
            self.incident_status = None
            return None, False
        if self.not_running >= STOPPED_AFTER_CHECKS:
            status, revive = STATUS_ADDON_GESTOPPT, True
        elif self.missing >= MISSING_AFTER_CHECKS:
            status, revive = STATUS_ADDON_FEHLT, False
        elif running is not False and now - self._silence_reference() >= SILENCE_SECONDS:
            status, revive = STATUS_REAGIERT_NICHT, running is True
        elif self.incident_since is not None:
            # Vorfall laeuft weiter (z. B. Add-on wieder gestartet, aber noch kein Event): der zuletzt ermittelte
            # Status bleibt bestehen, ohne neuen Startversuch.
            status, revive = self.incident_status, False
        elif bruecke == BRUECKE_MEHRFACH:
            return STATUS_ADDON_MEHRFACH, False
        else:
            return None, False
        if self.incident_since is None:
            self.incident_since = now
        self.incident_status = status
        return status, revive and self._restart_allowed(now)

    def _incident_resolved(self, running: bool | None, bruecke: str) -> bool:
        """Entwarnung nur, wenn NACH dem Beginn des Vorfalls ein Event kam, die Bruecke dabei (bzw. seither) nicht als
        gestoppt gilt und nicht als fehlend belegt ist."""
        if self.incident_since is None:
            return False
        if self.last_event_at is None or self.last_event_at <= self.incident_since:
            return False
        return running is not False and bruecke != BRUECKE_FEHLT

    def _silence_reference(self) -> float:
        reference = self.started_at if self.last_event_at is None else self.last_event_at
        if self.restarts:
            reference = max(reference, self.restarts[-1])
        return reference

    def stand(self, now: float) -> dict[str, int | None]:
        """Zaehler fuer die Diagnose: Pruefungen in Folge ohne laufendes Add-on, Neustarts in der letzten Stunde und
        das Alter des letzten Ereignisses in ganzen Sekunden (None, solange keines kam). Aendert nichts."""
        return {
            "nicht_laufend_in_folge": self.not_running,
            "neustarts_letzte_stunde": sum(1 for start in self.restarts if now - start < RESTART_WINDOW_SECONDS),
            "alter_letztes_ereignis_s": None if self.last_event_at is None else int(now - self.last_event_at),
        }

    def _restart_allowed(self, now: float) -> bool:
        while self.restarts and now - self.restarts[0] >= RESTART_WINDOW_SECONDS:
            self.restarts.popleft()
        if len(self.restarts) < MAX_RESTARTS_PER_WINDOW:
            self.restarts.append(now)
            return True
        return False


def _kreise(liste) -> dict[int, dict] | None:
    """Die Kreise des Ereignisses nach Nummer; None, wenn die Liste fehlt, ein Eintrag kein Objekt mit gueltiger
    Nummer (ganze Zahl von 1 bis EINRICHTUNG_MAX_KREISE, kein bool) ist, eine Nummer doppelt vorkommt oder mehr Kreise
    gemeldet werden als erlaubt."""
    if not isinstance(liste, list) or len(liste) > EINRICHTUNG_MAX_KREISE:
        return None
    kreise: dict[int, dict] = {}
    for eintrag in liste:
        nummer = eintrag.get("kreis") if isinstance(eintrag, dict) else None
        if (not isinstance(nummer, int) or isinstance(nummer, bool) or not 1 <= nummer <= EINRICHTUNG_MAX_KREISE
                or nummer in kreise):
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
        # Kreise, die im letzten gueltigen Ereignis fehlten, und dessen Konfigurationsversion: fehlt ein Kreis im
        # naechsten Ereignis mit derselben Version erneut, faellt sein Geraet weg (kein Abraeumen auf einen Ausreisser).
        self._fehlende_kreise: set[int] = set()
        self._konfig_version: object = None
        self.watchdog_status: str | None = None
        # Versionsbefund (integration_veraltet/addon_veraltet) aus dem Schema des letzten eigenen Ereignisses; None, wenn
        # die Integration es liest. `wunsch_schema`: das zuletzt gelesene Schema, in dem die Integration antwortet.
        self.version_status: str | None = None
        self.wunsch_schema: int = WUNSCH_EVENT_SCHEMA
        self._verfuegbar_gemeldet = True
        self._watchdog = Watchdog(started_at=_now())
        self.push = Push(hass)
        # Ob der Store des Pushs gegen den Waechter abgeglichen ist: ein Rot-Eintrag aus der Zeit vor dem Neustart, den der
        # neue Waechter nicht wieder findet, ist erledigt und wird nicht weiter erinnert.
        self._push_abgeglichen = False
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
    def version(self) -> str:
        """Die Version dieser Integration, die das Lebenszeichen an das Add-on nennt; leer vor dem Start."""
        return self._version

    def waechter_stand(self) -> dict[str, int | None]:
        """Zaehler des Waechters (siehe `Watchdog.stand`) zur aktuellen monotonen Zeit."""
        return self._watchdog.stand(_now())

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
    def verfuegbar(self) -> bool:
        """Ob die Entities ausser dem Status ihre Werte zeigen: nicht bei einem Waechterbefund, der das Add-on als nicht
        bereit meldet (gestoppt, reagiert nicht, fehlt; `addon_mehrfach` gehoert nicht dazu) und nicht bei einem
        Versionsbefund."""
        return self.watchdog_status not in STATUS_ADDON_NICHT_BEREIT and self.version_status is None

    @callback
    def _verfuegbarkeit_melden(self) -> None:
        """Den Uebergang nach "nicht verfuegbar" und zurueck je einmal ins Log (INFO), nur mit dem Befundcode."""
        verfuegbar = self.verfuegbar
        if verfuegbar == self._verfuegbar_gemeldet:
            return
        self._verfuegbar_gemeldet = verfuegbar
        if verfuegbar:
            _LOGGER.info("Entities wieder verfuegbar")
        else:
            _LOGGER.info("Entities nicht verfuegbar (Befund %s)", self.status)

    @property
    def reason(self) -> str | None:
        """Der Grund des Status-Sensors: bei einem Waechter- oder Versionsbefund dessen Code, sonst der Grund des Add-ons."""
        if self.watchdog_status is not None:
            return self.watchdog_status
        if self.version_status is not None:
            return self.version_status
        return None if self.data is None else self.data.get("grund")

    @callback
    def async_start(self, version: str) -> None:
        """Startet Zuhoeren und Waechter-Takt und meldet dem Add-on die Version der Integration."""
        self._version = version
        self._watchdog = Watchdog(started_at=_now())
        self._push_abgeglichen = False
        self._stopped = False
        self.push.async_start(self.entry)
        # Das Repair des Waechters ueberdauert einen Neustart von Home Assistant; der neue Waechter beginnt ohne Befund
        # und legt es bei Bedarf neu an.
        repairs.async_set_watchdog(self.hass, None)
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
        Geraets traegt dann geraet_zustand. Ein Kreis ohne gueltige Nummer, eine doppelte Nummer oder mehr Kreise als
        erlaubt verwirft das ganze Ereignis (Warnung ohne Kreisnamen); ein unbekannter Status nicht. Ein gueltiges
        Ereignis zieht auch den Geraetebaum nach (Anlage, Kreise)."""
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
            # gelesenen Werte bleiben stehen, nur der Zustand wechselt; die Mindestversion fuer den Text steht im Anlagenfeld `mindest_integration`.
            self._set_version_status(befund, data.get("mindest_integration"))
            return
        if isinstance(schema, bool) or schema not in V5_SCHEMAS:
            # Nur der Typ und eine ganze Zahl: ein Fremdereignis soll keinen frei waehlbaren Text ins Log bringen.
            _LOGGER.warning("Status-Event mit nicht aushandelbarem schema ignoriert: %s",
                            schema if isinstance(schema, int) and not isinstance(schema, bool) and abs(schema) < 10**6
                            else type(schema).__name__)
            return
        # Ein unbekannter Status oder Hinweis verwirft das Ereignis nicht: er gilt als das, was das Add-on gemeldet hat.
        kreise = _kreise(data.get("kreise"))
        if kreise is None:
            _LOGGER.warning("Status-Event mit ungueltiger Kreisliste (Form, Nummern oder Anzahl) ignoriert")
            return
        self.wunsch_schema = schema
        # Nur die Felder des Vertrags (STATUS_EVENT_FIELDS, STATUS_KREIS_FIELDS); fehlende bleiben fehlend.
        self.data = {key: data[key] for key in STATUS_EVENT_FIELDS if key in data and key != "kreise"}
        self.kreise = kreise
        # Push-Ziele und -Regeln stehen (noch) nicht in STATUS_EVENT_FIELDS: sie kommen unveraendert aus dem Ereignis und
        # werden in push.py auf Form und Muster geprueft.
        if self.push.ziele_setzen(data.get("benachrichtigung"), data.get("hinweise_aus"), data.get("push_regeln")):
            self.entry.async_create_task(self.hass, self.push.async_verarbeiten(), f"smartheat_push_{self.tenant_id}")
        self._async_geraete_nachziehen(data.get("konfig_version"))
        hinweise = data.get("geraet_hinweise")
        repairs.async_update(self.hass, hinweise if isinstance(hinweise, list) else [])
        self._set_version_status(None)  # sendet das Signal in jedem Fall

    @callback
    def _async_geraete_nachziehen(self, konfig_version: object) -> None:
        """Geraete der Anlage und der gemeldeten Kreise anlegen oder aktualisieren; Kreis-Geraete, deren Kreis in zwei
        aufeinanderfolgenden Ereignissen mit derselben Konfigurationsversion fehlt, entfernen. Ein Ereignis ohne
        Konfigurationsversion oder mit leerer Kreisliste belegt keinen Wegfall: die Zaehlung beginnt neu, das Geraet
        bleibt, und entfernen kann es dann nur der Kunde von Hand."""
        anlage = geraete.async_anlage(self.hass, self.entry, self.data)
        for nummer, kreis in self.kreise.items():
            geraete.async_kreis(self.hass, self.entry, anlage, nummer, kreis.get("name"))
        if konfig_version is None or not self.kreise:
            self._fehlende_kreise = set()
            self._konfig_version = None
            return
        registriert = geraete.registrierte_kreise(self.hass, self.entry)
        fehlend = set(registriert) - set(self.kreise)
        weg = fehlend & self._fehlende_kreise if konfig_version == self._konfig_version else set()
        for nummer in weg:
            geraete.async_kreis_entfernen(self.hass, self.entry, registriert[nummer])
        self._fehlende_kreise = fehlend - weg
        self._konfig_version = konfig_version

    @callback
    def _set_version_status(self, befund: str | None, mindest_integration: object = None) -> None:
        """Versionsbefund setzen oder (None) aufheben, Repair nachziehen und die Entities benachrichtigen."""
        if befund != self.version_status:
            _LOGGER.info("Versionsbefund des Add-ons: %s", befund or "keiner")
        self.version_status = befund
        repairs.async_set_version(self.hass, befund, mindest_integration)
        self._verfuegbarkeit_melden()
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
                stand = await async_bruecke_stand(self.hass)
            except AddonError as error:
                # Nicht den Supervisor-Text loggen: er kann Optionswerte zitieren.
                _LOGGER.warning("Waechter: Supervisor nicht erreichbar, Pruefung faellt aus: %s", type(error).__name__)
                return
            manager = stand.manager
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
            status, revive = self._watchdog.check(_now(), running, stand.zustand)
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
        """Das Repair des Waechters beim Beginn (None -> Befund) eines Vorfalls anlegen, an seinem Ende (Befund -> None)
        entfernen. Eskaliert ein laufender Vorfall (z. B. gestoppt -> reagiert nicht oder umgekehrt), bekommt das
        bestehende Repair nur den neuen Text-Code (gleiche ID, kein neuer Eintrag): es ist derselbe Vorfall, keine
        zweite Stoerung."""
        changed = status != self.watchdog_status
        alt = self.watchdog_status
        self.watchdog_status = status
        self._verfuegbarkeit_melden()
        self._push_nachziehen(status, alt, changed)
        if changed:
            repairs.async_set_watchdog(self.hass, status)
            async_dispatcher_send(self.hass, self.signal)

    @callback
    def _push_nachziehen(self, status: str | None, alt: str | None, changed: bool) -> None:
        """Rot-Befunde des Waechters (PUSH_CODES) dem Push melden, ihr Ende ebenfalls. Nach dem Start schliesst ein Waechter
        ohne Befund einen Eintrag aus der Zeit davor, sobald er lange genug laeuft, um denselben Befund wieder gefunden zu
        haben (PUSH_ABGLEICH_SECONDS); findet er ihn wieder, gibt es keinen zweiten Push."""
        if changed:
            self._push_abgeglichen = True
            if status in PUSH_CODES:
                self._push_melden(status, True)
            elif alt in PUSH_CODES:
                self._push_melden(alt, False)
        elif status is None and not self._push_abgeglichen and _now() - self._watchdog.started_at >= PUSH_ABGLEICH_SECONDS:
            self._push_abgeglichen = True
            self._push_melden(WATCHDOG_ISSUE_ID, False)

    @callback
    def _push_melden(self, code: str, offen: bool) -> None:
        self.entry.async_create_task(
            self.hass, self.push.melden(code, WATCHDOG_ISSUE_ID, None, PUSH_CODES.get(code, PUSH_STUFE_ROT), offen),
            f"smartheat_push_{self.tenant_id}",
        )
