"""Push auf die Handys des Kunden (Companion-App) fuer Befunde der Integration selbst.

Rot geht sofort hinaus, auch in der Ruhezeit, wird nie gesammelt und zaehlt nicht gegen das Tageslimit; scheitert der Versand,
wird er bis zu PUSH_WIEDERHOLUNG_SECONDS lang wiederholt, und solange der Befund offen bleibt, kommt alle
PUSH_WIEDERHOLUNG_SECONDS eine Erinnerung. Orange geht einmal hinaus, in der Ruhezeit erst an deren Ende und nur, wenn der
Befund dann noch offen ist; ab dem Tageslimit geht je Tag eine Sammelnachricht mit allgemeinem Titel hinaus. Gelb gibt nie
einen Push. Der Text ist allgemein (Titel des Befundcodes in der Sprache von Home Assistant, der Hinweis auf die
Reparaturen): weder Dienst-, Kreis- noch Sensornamen noch Werte gehen in den Push oder ins Log.

Die Ziele (Notify-Dienste) und die Regeln (Ruhezeit, Tageslimit, stummgeschaltete Hinweise) kommen aus dem letzten gueltigen
Status-Ereignis und werden nie gespeichert; bis zum ersten Ereignis bleibt eine Meldung ausstehend, ohne Ziele
(leere Liste) ist der Push aus. Ruhezeit und Tageswechsel folgen der Zeitzone von Home Assistant. Der Store haelt den
Tageszaehler, aufgeschobene und offene Meldungen mit dem Zeitpunkt der letzten Sendung und die Fehlversuche -- damit
ein Neustart von Home Assistant keinen zweiten Push fuer denselben offenen Befund ausloest."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import translation
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CODE_RE,
    DOMAIN,
    EINRICHTUNG_MAX_HANDYS,
    EINRICHTUNG_MAX_KREISE,
    HA_DIENST_RE,
    PRODUKT_NAME,
    PUSH_EINTRAEGE_MAX,
    PUSH_HINWEISE_AUS_MAX,
    PUSH_RUHE_BIS,
    PUSH_RUHE_VON,
    PUSH_SENDE_TIMEOUT_SECONDS,
    PUSH_STORE_KEY,
    PUSH_STORE_VERSION,
    PUSH_STUFE_ORANGE,
    PUSH_STUFE_ROT,
    PUSH_TAGESLIMIT,
    PUSH_TAGESLIMIT_MAX,
    PUSH_TAGESLIMIT_MIN,
    PUSH_TAKT_SECONDS,
    PUSH_VERSUCH_ABSTAND_SECONDS,
    PUSH_WIEDERHOLUNG_SECONDS,
)

_LOGGER = logging.getLogger(__name__)
_SPRACHE_RUECKFALL = "en"
_STUFEN = (PUSH_STUFE_ROT, PUSH_STUFE_ORANGE)


async def async_store_loeschen(hass: HomeAssistant) -> None:
    """Der Store des Pushs verschwindet mit dem Eintrag."""
    await Store(hass, PUSH_STORE_VERSION, PUSH_STORE_KEY).async_remove()


def _zahl(wert: object) -> float | None:
    return float(wert) if isinstance(wert, (int, float)) and not isinstance(wert, bool) else None


def _ganz(wert: object, kleinste: int, groesste: int) -> int | None:
    if isinstance(wert, int) and not isinstance(wert, bool) and kleinste <= wert <= groesste:
        return wert
    return None


def _code(wert: object) -> str | None:
    return wert if isinstance(wert, str) and CODE_RE.fullmatch(wert) else None


def _kreis_gueltig(kreis: object) -> bool:
    return kreis is None or _ganz(kreis, 1, EINRICHTUNG_MAX_KREISE) is not None


def _schluessel(key: str, kreis: int | None) -> str:
    return f"{key}|{kreis or 0}"


def _eintrag_lesen(roh: object) -> dict[str, Any] | None:
    """Ein Eintrag aus dem Store; None, wenn er nicht gueltig ist."""
    if not isinstance(roh, dict):
        return None
    code, key, stufe = _code(roh.get("code")), _code(roh.get("key")), roh.get("stufe")
    kreis = roh.get("kreis")
    versuche = _ganz(roh.get("versuche"), 0, 10**6)
    if code is None or key is None or stufe not in _STUFEN or not _kreis_gueltig(kreis) or versuche is None:
        return None
    if not isinstance(roh.get("ausstehend"), bool):
        return None
    return {
        "code": code, "key": key, "kreis": kreis, "stufe": stufe, "ausstehend": roh["ausstehend"],
        "letzte": _zahl(roh.get("letzte")), "versuch": _zahl(roh.get("versuch")),
        "versuch_seit": _zahl(roh.get("versuch_seit")), "versuche": versuche,
    }


class Push:
    """Zustand und Versand des Pushs einer Integration; alle Aenderungen laufen unter einer Sperre."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, PUSH_STORE_VERSION, PUSH_STORE_KEY)
        self._sperre = asyncio.Lock()
        self._tag: str | None = None
        self._gesendet = 0
        self._sammel = False
        self._eintraege: dict[str, dict[str, Any]] = {}
        self._ziele: tuple[str, ...] | None = None  # None: noch kein Ereignis gelesen
        self._hinweise_aus: frozenset[str] = frozenset()
        self.ruhe_von, self.ruhe_bis, self.tageslimit = PUSH_RUHE_VON, PUSH_RUHE_BIS, PUSH_TAGESLIMIT

    # --- Zustand ---

    @property
    def ziele(self) -> tuple[str, ...]:
        return self._ziele or ()

    @property
    def eintraege(self) -> int:
        return len(self._eintraege)

    @property
    def ausstehend(self) -> int:
        return sum(1 for eintrag in self._eintraege.values() if eintrag["ausstehend"])

    async def async_laden(self) -> None:
        """Den Zustand aus dem Store lesen; ein unlesbarer Store gilt als leer."""
        roh = await self._store.async_load()
        if not isinstance(roh, dict) or not isinstance(roh.get("eintraege"), dict):
            return
        tag, gesendet = roh.get("tag"), _ganz(roh.get("gesendet"), 0, 10**6)
        if isinstance(tag, str) and len(tag) <= 10 and gesendet is not None:
            self._tag, self._gesendet = tag, gesendet
            self._sammel = roh.get("sammel") is True
        for wert in roh["eintraege"].values():
            eintrag = _eintrag_lesen(wert)
            if eintrag is not None and len(self._eintraege) < PUSH_EINTRAEGE_MAX:
                self._eintraege[_schluessel(eintrag["key"], eintrag["kreis"])] = eintrag

    async def _speichern(self) -> None:
        await self._store.async_save({
            "tag": self._tag, "gesendet": self._gesendet, "sammel": self._sammel,
            "eintraege": {schluessel: dict(eintrag) for schluessel, eintrag in self._eintraege.items()},
        })

    def async_start(self, entry: ConfigEntry) -> None:
        """Den Takt anlegen, der Erinnerungen, Wiederholungen und das Ende der Ruhezeit abarbeitet."""
        async def takt(_jetzt) -> None:
            await self.async_verarbeiten()

        entry.async_on_unload(async_track_time_interval(self._hass, takt, timedelta(seconds=PUSH_TAKT_SECONDS)))

    # --- Ziele und Regeln ---

    def ziele_setzen(self, ziele: object, hinweise_aus: object, regeln: object) -> bool:
        """Ziele, stummgeschaltete Hinweise und Regeln aus einem Ereignis uebernehmen; True, wenn sich die Ziele aenderten
        (dann lohnt eine Verarbeitung). Was nicht gueltig ist, gilt als leer bzw. als Vorgabe, nie als Fehler."""
        neu: list[str] = []
        for dienst in ziele if isinstance(ziele, list) else []:
            if isinstance(dienst, str) and HA_DIENST_RE.fullmatch(dienst) and dienst not in neu:
                neu.append(dienst)
        ergebnis = tuple(neu[:EINRICHTUNG_MAX_HANDYS])
        geaendert = ergebnis != self._ziele
        self._ziele = ergebnis
        codes = (_code(wert) for wert in hinweise_aus[:PUSH_HINWEISE_AUS_MAX]) if isinstance(hinweise_aus, list) else ()
        self._hinweise_aus = frozenset(code for code in codes if code is not None)
        self._regeln_setzen(regeln)
        return geaendert

    def _regeln_setzen(self, regeln: object) -> None:
        roh = regeln if isinstance(regeln, dict) else {}
        von, bis = _ganz(roh.get("ruhe_von"), 0, 23), _ganz(roh.get("ruhe_bis"), 0, 23)
        if von is None or bis is None or von == bis:
            von, bis = PUSH_RUHE_VON, PUSH_RUHE_BIS
        self.ruhe_von, self.ruhe_bis = von, bis
        limit = _ganz(roh.get("tageslimit"), PUSH_TAGESLIMIT_MIN, PUSH_TAGESLIMIT_MAX)
        self.tageslimit = PUSH_TAGESLIMIT if limit is None else limit

    def _in_ruhe(self, stunde: int) -> bool:
        if self.ruhe_von < self.ruhe_bis:
            return self.ruhe_von <= stunde < self.ruhe_bis
        return stunde >= self.ruhe_von or stunde < self.ruhe_bis

    # --- Meldungen ---

    async def melden(self, code: object, key: object, kreis: object, stufe: object, offen: bool) -> None:
        """Ein Befund (`key`, bei Kreisbefunden mit `kreis`) ist offen oder nicht mehr offen. Neu ist ein Befund ohne Eintrag
        oder mit anderem Code bzw. anderer Stufe; ein gleicher, noch offener tut nichts (keine Wiederholung beim
        Neustart). Ein beendeter oder auf Gelb zurueckgestufter Befund verschwindet samt seiner ausstehenden Meldung."""
        code, key = _code(code), _code(key)
        if code is None or key is None or not _kreis_gueltig(kreis):
            return
        schluessel = _schluessel(key, kreis if isinstance(kreis, int) else None)
        async with self._sperre:
            vorhanden = self._eintraege.get(schluessel)
            if not offen or stufe not in _STUFEN:
                if self._eintraege.pop(schluessel, None) is not None:
                    await self._speichern()
                return
            neu = vorhanden is None or vorhanden["code"] != code or vorhanden["stufe"] != stufe
            if neu and not self._aufnehmen(schluessel, code, key, kreis, stufe):
                return
            await self._verarbeiten()

    def _aufnehmen(self, schluessel: str, code: str, key: str, kreis: object, stufe: object) -> bool:
        """Einen neuen Eintrag anlegen; Orange mit stummgeschaltetem Code oder Schluessel gelten sofort als erledigt."""
        if schluessel not in self._eintraege and len(self._eintraege) >= PUSH_EINTRAEGE_MAX:
            erledigt = next((name for name, eintrag in self._eintraege.items() if not eintrag["ausstehend"]), None)
            if erledigt is None:
                _LOGGER.warning("Push: zu viele offene Meldungen, Befund %s wird nicht gemerkt", code)
                return False
            del self._eintraege[erledigt]
        stumm = stufe != PUSH_STUFE_ROT and (code in self._hinweise_aus or key in self._hinweise_aus)
        jetzt = dt_util.utcnow().timestamp()
        self._eintraege[schluessel] = {
            "code": code, "key": key, "kreis": kreis, "stufe": stufe, "ausstehend": not stumm,
            "letzte": jetzt if stumm else None, "versuch": None, "versuch_seit": None, "versuche": 0,
        }
        return True

    async def async_verarbeiten(self) -> None:
        """Faellige Meldungen senden: Erinnerungen, Wiederholungen, Ende der Ruhezeit, neue Ziele."""
        async with self._sperre:
            await self._verarbeiten()

    async def _verarbeiten(self) -> None:
        """Die Sperre ist gehalten."""
        jetzt_dt = dt_util.utcnow()
        jetzt = jetzt_dt.timestamp()
        zone = await dt_util.async_get_time_zone(self._hass.config.time_zone) or dt_util.UTC
        lokal = jetzt_dt.astimezone(zone)
        geaendert = self._tageswechsel(lokal.date().isoformat())
        ruhe = self._in_ruhe(lokal.hour)
        for eintrag in list(self._eintraege.values()):
            if eintrag["stufe"] == PUSH_STUFE_ROT and not eintrag["ausstehend"] and eintrag["letzte"] is not None \
                    and jetzt - eintrag["letzte"] >= PUSH_WIEDERHOLUNG_SECONDS:
                eintrag.update(ausstehend=True, versuch=None, versuch_seit=None, versuche=0)
                geaendert = True
            if not eintrag["ausstehend"] or (eintrag["stufe"] == PUSH_STUFE_ORANGE and ruhe):
                continue
            if eintrag["versuch_seit"] is None:
                eintrag["versuch_seit"] = jetzt
                geaendert = True
            if jetzt - eintrag["versuch_seit"] >= PUSH_WIEDERHOLUNG_SECONDS:
                _LOGGER.warning("Push: Meldung %s nach %s Versuchen aufgegeben", eintrag["code"], eintrag["versuche"])
                self._erledigen(eintrag, jetzt)
                geaendert = True
            elif self._ziele is None:
                continue
            elif not self._ziele:
                self._erledigen(eintrag, jetzt)
                geaendert = True
            elif eintrag["versuch"] is None or jetzt - eintrag["versuch"] >= PUSH_VERSUCH_ABSTAND_SECONDS:
                await self._senden_eintrag(eintrag, jetzt)
                geaendert = True
        if geaendert:
            await self._speichern()

    def _tageswechsel(self, heute: str) -> bool:
        if heute == self._tag:
            return False
        self._tag, self._gesendet, self._sammel = heute, 0, False
        return True

    @staticmethod
    def _erledigen(eintrag: dict[str, Any], jetzt: float) -> None:
        eintrag.update(ausstehend=False, letzte=jetzt, versuch=None, versuch_seit=None, versuche=0)

    async def _senden_eintrag(self, eintrag: dict[str, Any], jetzt: float) -> None:
        sammeln = eintrag["stufe"] == PUSH_STUFE_ORANGE and self._gesendet >= self.tageslimit
        if sammeln and self._sammel:
            self._erledigen(eintrag, jetzt)  # die Sammelnachricht dieses Tages ist schon hinaus
            return
        if sammeln:
            titel = await self._text("common", "push_sammel_titel")
            text = await self._text("common", "push_sammel_text")
        else:
            titel = await self._text("issues", f"{eintrag['code']}.title")
            text = await self._text("common", "push_text")
        if await self._senden(titel or PRODUKT_NAME, text, eintrag):
            if sammeln:
                self._sammel = True
            elif eintrag["stufe"] == PUSH_STUFE_ORANGE:
                self._gesendet += 1
            self._erledigen(eintrag, jetzt)
        else:
            eintrag["versuch"] = jetzt
            eintrag["versuche"] += 1

    async def _text(self, kategorie: str, schluessel: str) -> str:
        """Der Text in der Sprache von Home Assistant, sonst englisch; leer, wenn es ihn nicht gibt."""
        vollname = f"component.{DOMAIN}.{kategorie}.{schluessel}"
        for sprache in (self._hass.config.language, _SPRACHE_RUECKFALL):
            texte = await translation.async_get_translations(self._hass, sprache, kategorie, [DOMAIN])
            if texte.get(vollname):
                return texte[vollname]
        return ""

    async def _senden(self, titel: str, text: str, eintrag: dict[str, Any]) -> bool:
        """An alle Ziele; gelungen, wenn mindestens eines den Dienst angenommen hat. Im Log nur Code, Kreisnummer und
        Fehlerart, nie der Dienstname oder der Fehlertext (er kann den Dienstnamen nennen)."""
        gelungen = False
        for dienst in self.ziele:
            try:
                async with asyncio.timeout(PUSH_SENDE_TIMEOUT_SECONDS):
                    await self._hass.services.async_call(
                        "notify", dienst.partition(".")[2], {"title": titel, "message": text}, blocking=True,
                    )
                gelungen = True
            except Exception as fehler:  # noqa: BLE001 - ein Dienst von aussen darf alles werfen
                _LOGGER.warning(
                    "Push: Sendung gescheitert (Code %s, Kreis %s, Fehlerart %s)",
                    eintrag["code"], eintrag["kreis"], type(fehler).__name__,
                )
        return gelungen
