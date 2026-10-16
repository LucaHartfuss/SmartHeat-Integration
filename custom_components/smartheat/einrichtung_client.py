"""HTTP-Client der Einrichtungs-API (Spec 5c 2.1/3, Server einrichtung_api.py). Das Einrichtungs-Token lebt nur in
dieser Instanz, also im laufenden Assistenten; es wird nie gespeichert und nie geloggt (Regel 6), ebenso wenig
Passwort und Uebernahme-Code. Logs nennen nur Route, Status und Fehlerart.

Fehler, wie der Assistent sie unterscheidet:

* `CannotConnect`: Server nicht erreichbar oder Zeitueberschreitung (nichts vom Server gehoert).
* `ServerError`: Server erreichbar, aber 5xx.
* `TokenUngueltig` (Unterklasse von `InvalidAuth`): 401 auf einer Token-Route, das Token ist abgelaufen oder
  widerrufen; neu anmelden. `InvalidAuth` ohne Unterklasse: 401 beim Anmelden, die Zugangsdaten sind falsch.
* `Rejected`, `Conflict`, `NotFound`, `TooMany`: der Server hat mit Status und (meist) einem Text in Kundensprache
  geantwortet; `.text` anzeigen. `NotFound` auf einer Geraete-Route heisst: Geraet gesperrt, freigegeben oder
  anderes Konto (Geraete-Tor des Servers).
* `InvalidResponse`: 200, aber unbrauchbare Antwort.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

import aiohttp

from . import anfrage as v3_anfrage
from . import vertrag_pin
from .const import (
    EINRICHTUNG_ANFRAGE_FELDER,
    EINRICHTUNG_ANTWORT_FELDER,
    EINRICHTUNG_PREFIX,
    EINRICHTUNG_ROUTEN,
    ERROR_TEXT_MAX,
    REQUEST_TIMEOUT_SECONDS,
)

_LOGGER = logging.getLogger(__name__)
# Pin (Spec Stufe 3 S5 4): die Felder, die dieser Client direkt liest (Token, Auftrags- und Befehls-ID).
_VERTRAG_GEPINNT = {
    "ANTWORT_FELDER[anmelden]": (EINRICHTUNG_ANTWORT_FELDER["anmelden"], ("token",)),
    "ANTWORT_FELDER[inventur]": (EINRICHTUNG_ANTWORT_FELDER["inventur"], ("command_id",)),
    "ANTWORT_FELDER[einrichten]": (EINRICHTUNG_ANTWORT_FELDER["einrichten"], ("job_id",)),
    "ANTWORT_FELDER[neu_konfigurieren]": (EINRICHTUNG_ANTWORT_FELDER["neu_konfigurieren"], ("job_id",)),
}
vertrag_pin.enthalten(__name__, _VERTRAG_GEPINNT)


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
    """401. Beim Anmelden: falsche Zugangsdaten (`text` ist der Servertext)."""


class TokenUngueltig(InvalidAuth):
    """401 auf einer Token-Route: Token abgelaufen oder widerrufen. Auch die Antwort von `auftrag` nach einem
    angenommenen `entfernen` (die Freigabe widerruft das Token): der Assistent wertet das dann als „entfernt“."""


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


class EinrichtungClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str) -> None:
        self._session, self._base_url = session, base_url.rstrip("/")
        self.token: str | None = None

    async def anmelden(self, email: str, password: str) -> None:
        body = await self._call("anmelden", json={"email": email, "password": password}, auth=False)
        token = body.get("token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise InvalidResponse("Anmeldung ohne Token")
        self.token = token

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
        return {key: body[key] for key in EINRICHTUNG_ANTWORT_FELDER[route] if key in body}

    async def _text_feld(self, route: str, feld: str, **kwargs: Any) -> str:
        wert = (await self._dict(route, **kwargs)).get(feld)
        if not isinstance(wert, str) or not wert:
            raise InvalidResponse(f"{route}: Antwort ohne {feld}")
        return wert

    async def _call(self, route: str, *, json: dict | None = None, job_id: str | None = None, auth: bool = True,
                    ok: tuple[int, ...] = (200,), expect_json: bool = True) -> Any:
        method, path = EINRICHTUNG_ROUTEN[route]
        if json is not None:
            pflicht, optional = EINRICHTUNG_ANFRAGE_FELDER[route]
            if fremd := sorted(set(json) - set(pflicht) - set(optional)):
                raise ValueError(f"{route}: nicht deklarierte Felder {fremd}")  # Programmfehler, nie Nutzereingabe
        if job_id is not None:
            path = path.replace("<job_id>", quote(job_id, safe=""))
        headers = {"Authorization": f"Bearer {self.token}"} if auth and self.token else {}
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                async with self._session.request(method, f"{self._base_url}{EINRICHTUNG_PREFIX}{path}", json=json,
                                                 headers=headers, allow_redirects=False) as response:
                    if response.status not in ok:
                        await self._fehler(route, response, auth)
                    if response.status == 204 or not expect_json:
                        return None
                    try:
                        return await response.json()
                    except (aiohttp.ContentTypeError, ValueError, RecursionError) as error:
                        raise InvalidResponse(f"{route}: kein JSON", response.status) from error
        except TimeoutError as error:
            _LOGGER.info("Einrichtung: %s ohne Antwort (Zeitueberschreitung)", route)
            raise CannotConnect("Der SmartHeat-Dienst antwortet nicht.") from error
        except aiohttp.ClientError as error:
            _LOGGER.info("Einrichtung: %s nicht erreichbar (%s)", route, type(error).__name__)
            raise CannotConnect("Der SmartHeat-Dienst ist nicht erreichbar.") from error

    @staticmethod
    async def _fehler(route: str, response: aiohttp.ClientResponse, auth: bool) -> None:
        """Wirft je Status den passenden Fehler; der Body kann Nicht-JSON sein (Routing-404/405 als HTML). Eine
        Weiterleitung (3xx) wird nie verfolgt (der Body mit dem Passwort ginge sonst an das Ziel) und ist ein
        Fehler ohne Text."""
        if 300 <= response.status < 400:
            _LOGGER.warning("Einrichtung: %s abgelehnt (HTTP %s, Weiterleitung)", route, response.status)
            raise ApiError("", response.status)
        try:
            parsed = await response.json(content_type=None)
        except (aiohttp.ClientError, ValueError, RecursionError):
            parsed = None
        body: dict = parsed if isinstance(parsed, dict) else {}
        status = response.status
        text = body["error"] if isinstance(body.get("error"), str) else ""
        text = text[:ERROR_TEXT_MAX]
        _LOGGER.warning("Einrichtung: %s abgelehnt (HTTP %s)", route, status)
        if status == 400:
            fehler = body["fehler"] if isinstance(body.get("fehler"), dict) else {}
            raise Rejected(text, {str(k): str(v) for k, v in fehler.items()}, status)
        if status == 401:
            raise (TokenUngueltig if auth else InvalidAuth)(text, status)
        if status >= 500:
            raise ServerError(text, status)
        raise _ERRORS.get(status, ApiError)(text, status)
