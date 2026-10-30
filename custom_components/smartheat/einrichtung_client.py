"""HTTP-Client der Einrichtungs-API (Server: einrichtung/api.py). Das Einrichtungs-Token lebt nur in
dieser Instanz, also im laufenden Assistenten; es wird nie gespeichert und nie geloggt (Regel 6), ebenso wenig
Nachweis, Geraetecode und Uebernahme-Code. Logs nennen nur Route, Status und Fehlerart.

Anmeldung ohne Zugangsdaten: `geraetecode_anfordern` weist mit der Signatur des Add-ons nach, dass das Geraet gemeint ist,
und liefert den Code, den der Kunde im Portal bestaetigt; `geraetecode_abholen` holt danach das Token ab (der Client haelt
es in `token`, die Antwort traegt es nie).

Fehler, wie der Assistent sie unterscheidet:

* `CannotConnect`: Server nicht erreichbar oder Zeitueberschreitung (nichts vom Server gehoert).
* `ServerError`: Server erreichbar, aber 5xx.
* `TokenUngueltig` (Unterklasse von `InvalidAuth`): 401 auf einer Token-Route, das Token ist abgelaufen oder
  widerrufen; neu anmelden. `InvalidAuth` ohne Unterklasse: 401 beim Anfordern des Codes, der Nachweis zaehlt nicht.
* `ErneutAnmelden`: 403 mit `zustand` `erneut_anmelden`, die Bestaetigung im Portal ist zu alt; neuer Ablauf mit neuem Code.
* `PortalBestaetigung`: Antwort mit `zustand` `portal_bestaetigung`, der Eigentuemer muss im Portal bestaetigen.
* `Rejected`, `Conflict`, `NotFound`, `TooMany`: der Server hat mit Status und (meist) einem Text in Kundensprache
  geantwortet; `.text` anzeigen. `NotFound` auf einer Geraete-Route heisst: Geraet gesperrt, freigegeben oder
  anderes Konto (Geraete-Tor des Servers).
* `InvalidResponse`: 200, aber unbrauchbare Antwort.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote

import aiohttp

from . import anfrage as v3_anfrage
from . import antwort as v3_antwort
from . import const, vertrag_pin
from .const import (
    ANMELDECODE_RE,
    EINRICHTUNG_ANFRAGE_FELDER,
    EINRICHTUNG_ANTWORT_FELDER,
    EINRICHTUNG_PREFIX,
    EINRICHTUNG_ROUTEN,
    ERROR_TEXT_MAX,
    FEHLER_BODY_MAX_BYTES,
    FEHLER_WERT_MAX,
    GERAETECODE_MAX_ZEICHEN,
    GERAETECODE_SEKUNDEN_MAX,
    GERAETECODE_ZUSTAENDE,
    GERAETECODE_ZUSTAND_BESTAETIGT,
    GERAETECODE_ZUSTAND_LANGSAMER,
    GERAETECODE_ZUSTAND_WARTET,
    LINK_RE,
    NACHWEIS_KOPF_GERAET,
    NACHWEIS_KOPF_SIGNATUR,
    NACHWEIS_KOPF_ZEITSTEMPEL,
    REQUEST_TIMEOUT_SECONDS,
    ZUSTAND_ERNEUT_ANMELDEN,
    ZUSTAND_PORTAL_BESTAETIGUNG,
)

_LOGGER = logging.getLogger(__name__)
# Pin: die Felder, die dieser Client direkt liest (Code, Token, Auftrags- und Befehls-ID).
_VERTRAG_GEPINNT = {
    "ANTWORT_FELDER[geraetecode]": (EINRICHTUNG_ANTWORT_FELDER["geraetecode"],
                                    ("geraetecode", "anmeldecode", "link", "link_komplett", "gueltig_s", "abfrage_s")),
    "ANTWORT_FELDER[geraetecode_token]": (EINRICHTUNG_ANTWORT_FELDER["geraetecode_token"],
                                          ("zustand", "abfrage_s", "token")),
    "ANTWORT_FELDER[inventur]": (EINRICHTUNG_ANTWORT_FELDER["inventur"], ("command_id",)),
    "ANTWORT_FELDER[einrichten]": (EINRICHTUNG_ANTWORT_FELDER["einrichten"], ("job_id",)),
    "ANTWORT_FELDER[neu_konfigurieren]": (EINRICHTUNG_ANTWORT_FELDER["neu_konfigurieren"], ("job_id",)),
}
vertrag_pin.enthalten(__name__, _VERTRAG_GEPINNT)
# Felder der verschachtelten Objekte (V3) fuer `antwort.verschachtelt`.
_OBJEKT_FELDER = v3_antwort.felder_aus(const)


class ApiError(Exception):
    """Basisklasse; `text` ist der Text des Servers (Kundensprache) oder leer, `status` der HTTP-Status oder None."""

    def __init__(self, text: str = "", status: int | None = None) -> None:
        super().__init__(text or type(self).__name__)
        self.text = text
        self.status = status


class CannotConnect(ApiError):
    """Nicht erreichbar oder Zeitueberschreitung."""


class ServerError(ApiError):
    """5xx: der Server ist erreichbar, kann aber gerade nicht antworten."""


class InvalidAuth(ApiError):
    """401 auf einer Route ohne Token: beim Anfordern des Codes zaehlt der Nachweis nicht (`text` ist der Servertext)."""


class TokenUngueltig(InvalidAuth):
    """401 auf einer Token-Route: Token abgelaufen oder widerrufen. Auch die Antwort von `auftrag` nach einem
    angenommenen `entfernen` (die Freigabe widerruft das Token): der Assistent wertet das dann als „entfernt“."""


class ErneutAnmelden(ApiError):
    """403 mit `zustand` `erneut_anmelden`: die Bestaetigung im Portal ist laenger als zehn Minuten her."""


class PortalBestaetigung(ApiError):
    """Der Schritt wirkt erst, wenn der Eigentuemer ihn im Portal bestaetigt (`zustand` `portal_bestaetigung`)."""


class InvalidResponse(ApiError):
    pass


class NotFound(ApiError):
    pass


class Conflict(ApiError):
    pass


class TooMany(ApiError):
    pass


class Rejected(ApiError):
    """400 mit Text des Servers und optional Feldfehlern (`fehler`: Feld -> Fehlercode, z. B. aus `pruefen`)."""

    def __init__(self, text: str = "", fehler: dict | None = None, status: int | None = 400) -> None:
        super().__init__(text, status)
        self.fehler = fehler or {}


_ERRORS: dict[int, type[ApiError]] = {404: NotFound, 409: Conflict, 429: TooMany}
# Antworten, die der Server im Normalbetrieb gibt (Token abgelaufen oder Nachweis abgelehnt, Geraet unbekannt,
# Konflikt, Sperre): nur INFO; alles andere ist unerwartet und eine WARNING. Ebenso die beiden Zustaende, die zur
# Bestaetigung im Portal fuehren.
_ERWARTETE_STATUS = frozenset({401, 404, 409, 429})
_ERWARTETE_ZUSTAENDE = frozenset({ZUSTAND_ERNEUT_ANMELDEN, ZUSTAND_PORTAL_BESTAETIGUNG})


def _sekunden(wert: Any) -> bool:
    """Eine Zeitangabe des Servers: ganze Sekunden in sinnvollen Grenzen (kein bool, keine Zeichenkette)."""
    return isinstance(wert, int) and not isinstance(wert, bool) and 1 <= wert <= GERAETECODE_SEKUNDEN_MAX


class EinrichtungClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str) -> None:
        self._session, self._base_url = session, base_url.rstrip("/")
        self.token: str | None = None

    async def geraetecode_anfordern(self, nachweis: dict, sprache: str) -> dict:
        """Fordert den Anmeldecode an. `nachweis`: `geraet_id`, `zeitstempel`, `signatur` aus dem Ereignis des Add-ons;
        der Koerper ist `{}`, genau das hat der Geraetekern signiert. Antwort: `geraetecode`, `anmeldecode`, `link`,
        `link_komplett`, `gueltig_s`, `abfrage_s`, jedes Feld geprueft (Code und Link stehen dem Kunden auf dem
        Bildschirm, die Zeiten steuern das Polling)."""
        kopfe = {NACHWEIS_KOPF_GERAET: nachweis["geraet_id"], NACHWEIS_KOPF_ZEITSTEMPEL: str(nachweis["zeitstempel"]),
                 NACHWEIS_KOPF_SIGNATUR: nachweis["signatur"], "Accept-Language": sprache}
        antwort = await self._dict("geraetecode", json={}, auth=False, headers=kopfe)
        code = antwort.get("geraetecode")
        if not isinstance(code, str) or not code or len(code) > GERAETECODE_MAX_ZEICHEN:
            raise InvalidResponse("geraetecode: Antwort ohne geraetecode")
        for feld, muster in (("anmeldecode", ANMELDECODE_RE), ("link", LINK_RE), ("link_komplett", LINK_RE)):
            wert = antwort.get(feld)
            if not isinstance(wert, str) or not muster.fullmatch(wert):
                raise InvalidResponse(f"geraetecode: {feld} unbrauchbar")
        for feld in ("gueltig_s", "abfrage_s"):
            if not _sekunden(antwort.get(feld)):
                raise InvalidResponse(f"geraetecode: {feld} unbrauchbar")
        return antwort

    async def geraetecode_abholen(self, geraetecode: str) -> dict:
        """Fragt den Stand des Codes ab. Antwort `{zustand}` (bei `wartet` und `langsamer` mit `abfrage_s`). Bei
        `bestaetigt` merkt sich der Client das Token in `token`; die Antwort traegt es nicht."""
        antwort = await self._dict("geraetecode_token", json={"geraetecode": geraetecode}, auth=False)
        zustand = antwort.get("zustand")
        if not isinstance(zustand, str) or zustand not in GERAETECODE_ZUSTAENDE:
            raise InvalidResponse("geraetecode_token: unbekannter Zustand")
        abfrage = antwort.get("abfrage_s")
        if abfrage is not None and not _sekunden(abfrage):
            raise InvalidResponse("geraetecode_token: abfrage_s unbrauchbar")
        ergebnis: dict[str, Any] = {"zustand": zustand}
        if zustand == GERAETECODE_ZUSTAND_BESTAETIGT:
            token = antwort.get("token")
            if not isinstance(token, str) or not token:
                raise InvalidResponse("geraetecode_token: bestaetigt ohne Token")
            self.token = token
        elif zustand in (GERAETECODE_ZUSTAND_WARTET, GERAETECODE_ZUSTAND_LANGSAMER):
            if abfrage is None and zustand == GERAETECODE_ZUSTAND_LANGSAMER:
                raise InvalidResponse("geraetecode_token: langsamer ohne abfrage_s")
            if abfrage is not None:
                ergebnis["abfrage_s"] = abfrage
        return ergebnis

    async def abmelden(self) -> None:
        """Widerruft das Token beim Server; wirft nie (das Token ist danach in jedem Fall verworfen)."""
        if self.token is None:
            return
        try:
            await self._call("abmelden", ok=(200, 204), expect_json=False)
        except ApiError as error:
            _LOGGER.info("Abmelden beim Server nicht bestaetigt (%s)", type(error).__name__)
        finally:
            self.token = None

    async def uebernehmen(self, device_id: str, code: str | None) -> None:
        body: dict = {"device_id": device_id}
        if code:
            body["code"] = code
        await self._call("uebernehmen", json=body)

    async def geraet(self) -> dict:
        return await self._dict("geraet")

    async def inventur(self) -> str:
        return await self._text_feld("inventur", "command_id")

    async def vorschlag(self) -> dict:
        return await self._dict("vorschlag")

    async def zuordnung(self, kreis: str, profil: str, hebelsatz: str, tenant_id: str | None = None) -> dict:
        """`tenant_id` nur beim Ersetzen: der Server schlaegt dann die Zuordnung des alten Geraets vor."""
        body = v3_anfrage.auswahl_body({"kreis": kreis, "profil": profil, "hebelsatz": hebelsatz}, tenant_id)
        return await self._dict("zuordnung", json=body)

    async def pruefen(self, anfrage: dict) -> dict:
        return await self._dict("pruefen", json=anfrage)

    async def einrichten(self, anfrage: dict) -> str:
        return await self._text_feld("einrichten", "job_id", json=anfrage)

    async def neu_konfigurieren(self, anfrage: dict) -> str:
        return await self._text_feld("neu_konfigurieren", "job_id", json=anfrage)

    async def entfernen(self) -> dict:
        return await self._dict("entfernen")

    async def auftrag(self, job_id: str) -> dict:
        """Stand eines Auftrags. Nach einem angenommenen `entfernen` widerruft die Freigabe das Token: der Server
        antwortet dann 401 (`TokenUngueltig`) statt „fertig“, der Aufrufer wertet das als „entfernt“."""
        return await self._dict("auftrag", job_id=job_id)

    async def _dict(self, route: str, **kwargs: Any) -> dict:
        body = await self._call(route, **kwargs)
        if not isinstance(body, dict):
            raise InvalidResponse(f"{route}: Antwort ist kein Objekt")
        felder = {key: body[key] for key in EINRICHTUNG_ANTWORT_FELDER[route] if key in body}
        return v3_antwort.verschachtelt(route, felder, _OBJEKT_FELDER)

    async def _text_feld(self, route: str, feld: str, **kwargs: Any) -> str:
        wert = (await self._dict(route, **kwargs)).get(feld)
        if not isinstance(wert, str) or not wert:
            raise InvalidResponse(f"{route}: Antwort ohne {feld}")
        return wert

    async def _call(self, route: str, *, json: dict | None = None, job_id: str | None = None, auth: bool = True,
                    ok: tuple[int, ...] = (200,), expect_json: bool = True,
                    headers: dict[str, str] | None = None) -> Any:
        method, path = EINRICHTUNG_ROUTEN[route]
        if json is not None:
            pflicht, optional = EINRICHTUNG_ANFRAGE_FELDER[route]
            if fremd := sorted(set(json) - set(pflicht) - set(optional)):
                raise ValueError(f"{route}: nicht deklarierte Felder {fremd}")  # Programmfehler, nie Nutzereingabe
        if job_id is not None:
            path = path.replace("<job_id>", quote(job_id, safe=""))
        headers = {**(headers or {}), **({"Authorization": f"Bearer {self.token}"} if auth and self.token else {})}
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                async with self._session.request(method, f"{self._base_url}{EINRICHTUNG_PREFIX}{path}", json=json,
                                                 headers=headers, allow_redirects=False) as response:
                    if response.status not in ok:
                        await self._fehler(route, response, auth)
                    if response.status == 204 or not expect_json:
                        return None
                    try:
                        body = await response.json()
                    except (aiohttp.ContentTypeError, ValueError, RecursionError) as error:
                        raise InvalidResponse(f"{route}: kein JSON", response.status) from error
                    if isinstance(body, dict) and body.get("zustand") == ZUSTAND_PORTAL_BESTAETIGUNG:
                        _LOGGER.info("Einrichtung: %s wartet auf die Bestaetigung im Portal", route)
                        text = body["error"] if isinstance(body.get("error"), str) else ""
                        raise PortalBestaetigung(text[:ERROR_TEXT_MAX])
                    return body
        except TimeoutError as error:
            _LOGGER.info("Einrichtung: %s ohne Antwort (Zeitueberschreitung)", route)
            raise CannotConnect("Der SmartHeat-Dienst antwortet nicht.") from error
        except aiohttp.ClientError as error:
            _LOGGER.info("Einrichtung: %s nicht erreichbar (%s)", route, type(error).__name__)
            raise CannotConnect("Der SmartHeat-Dienst ist nicht erreichbar.") from error

    @staticmethod
    async def _fehler_body(response: aiohttp.ClientResponse) -> dict:
        """Body einer Fehlerantwort als Objekt; hoechstens `FEHLER_BODY_MAX_BYTES` werden gelesen (ein groesserer Body
        ist kein JSON im Sinne des Clients: leeres Objekt), ebenso bei Nicht-JSON oder einem anderen JSON-Typ."""
        daten = bytearray()
        try:
            while chunk := await response.content.read(FEHLER_BODY_MAX_BYTES + 1 - len(daten)):
                daten += chunk
                if len(daten) > FEHLER_BODY_MAX_BYTES:
                    return {}
            parsed = json.loads(daten)
        except (aiohttp.ClientError, ValueError, RecursionError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    async def _fehler(route: str, response: aiohttp.ClientResponse, auth: bool) -> None:
        """Wirft je Status den passenden Fehler; der Body kann Nicht-JSON sein (Routing-404/405 als HTML). Eine
        Weiterleitung (3xx) wird nie verfolgt (der Body mit dem Nachweis ginge sonst an das Ziel) und ist ein
        Fehler ohne Text. Erwartete Ablehnungen (401/404/409/429) loggt der Client als INFO, alles andere als WARNING;
        nie den Text des Servers."""
        if 300 <= response.status < 400:
            _LOGGER.warning("Einrichtung: %s abgelehnt (HTTP %s, Weiterleitung)", route, response.status)
            raise ApiError("", response.status)
        body = await EinrichtungClient._fehler_body(response)
        status = response.status
        text = body["error"] if isinstance(body.get("error"), str) else ""
        text = text[:ERROR_TEXT_MAX]
        zustand = body.get("zustand") if status < 500 else None
        erwartet = status in _ERWARTETE_STATUS or (isinstance(zustand, str) and zustand in _ERWARTETE_ZUSTAENDE)
        _LOGGER.log(logging.INFO if erwartet else logging.WARNING, "Einrichtung: %s abgelehnt (HTTP %s)", route, status)
        if zustand == ZUSTAND_PORTAL_BESTAETIGUNG:
            raise PortalBestaetigung(text, status)
        if status == 403 and zustand == ZUSTAND_ERNEUT_ANMELDEN:
            raise ErneutAnmelden(text, status)
        if status == 400:
            fehler = body["fehler"] if isinstance(body.get("fehler"), dict) else {}
            raise Rejected(text, {str(k): str(v)[:FEHLER_WERT_MAX] for k, v in fehler.items()}, status)
        if status == 401:
            raise (TokenUngueltig if auth else InvalidAuth)(text, status)
        if status >= 500:
            raise ServerError(text, status)
        raise _ERRORS.get(status, ApiError)(text, status)
