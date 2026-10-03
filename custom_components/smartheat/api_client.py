"""Async HTTP-Client fuer die heizungsserver-Accounts-API (Login, Tenants, Katalog,
Provisioning, Logout, Widerruf beim Entfernen). Ersetzt die synchronen requests.post()-Aufrufe
aus dem alten heizungsbruecke/web.py-Wizard -- dieselbe API, nur async und ohne Flask-Session.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .const import (
    ACCESS_DENIED_REASON_MAX,
    INSTALLATION_PATH,
    PROFILE_PATH,
    REQUEST_TIMEOUT_SECONDS,
    REVOKE_TIMEOUT_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class ApiError(Exception):
    """Basisklasse fuer alle Fehler dieses Clients."""


class CannotConnect(ApiError):
    """Der heizungsserver war nicht erreichbar (Netzwerkfehler)."""


class InvalidAuth(ApiError):
    """Zugangsdaten oder Sitzungstoken wurden vom Server abgelehnt (401)."""


class InvalidResponse(ApiError):
    """Der Server antwortete mit 200, aber unverstaendlich (kein JSON, Pflichtfeld fehlt)."""


class AccessDenied(ApiError):
    """403: Zugriff verweigert; reason = Text des Servers (angezeigt, nicht gedeutet)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason or "Zugriff verweigert")
        self.reason = reason


class ProfileRejected(ApiError):
    """400 beim Profilwechsel: der Server lehnt das Profil ab."""


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class HeizungsserverClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str) -> None:
        self._session = session
        self._base_url = base_url

    async def login(self, email: str, password: str) -> str:
        try:
            body = await self._request("POST", "/auth/login", "Login", json={"email": email, "password": password})
        except InvalidAuth as error:
            raise InvalidAuth("Ungueltige Zugangsdaten") from error
        token = body.get("token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise InvalidResponse("Login-Antwort ohne Token")
        return token

    async def list_tenants(self, token: str) -> list[dict]:
        tenants = await self._request("GET", "/accounts/me/tenants", "Tenant-Liste", headers=_bearer(token))
        if not isinstance(tenants, list) or not all(
            isinstance(t, dict) and isinstance(t.get("tenant_id"), str) for t in tenants
        ):
            raise InvalidResponse("Tenant-Liste hat ein unerwartetes Format")
        return tenants

    async def get_catalog(self, token: str) -> dict:
        catalog = await self._request("GET", "/catalog", "Katalog", headers=_bearer(token))
        if not isinstance(catalog, dict) or not isinstance(catalog.get("profiles"), list):
            raise InvalidResponse("Katalog-Antwort ohne 'profiles'-Liste")
        return catalog

    async def provision(self, token: str, tenant_id: str, profile_id: str, csr: str) -> dict:
        """Spec AWS-IoT 4.1: der CSR geht immer mit; der Server entscheidet anhand seines Provisioners."""
        body = await self._request(
            "POST", f"/tenants/{tenant_id}/provision", "Provisioning",
            json={"profile_id": profile_id, "csr": csr}, headers=_bearer(token),
        )
        if not isinstance(body, dict):
            raise InvalidResponse("Provisioning-Antwort ist kein Objekt")
        return body

    async def update_profile(self, token: str, tenant_id: str, profile_id: str) -> dict:
        """Profilwechsel ohne neue Zugangsdaten (Neu konfigurieren, Spec TP7 4.1). Liefert die
        profile_params fuer die Add-on-Optionen."""
        body = await self._request(
            "POST", PROFILE_PATH.format(tenant_id=tenant_id), "Profilwechsel",
            json={"profile_id": profile_id}, headers=_bearer(token), on_400=ProfileRejected,
        )
        if not isinstance(body, dict) or not isinstance(body.get("profile_params"), dict):
            raise InvalidResponse("Profil-Antwort ohne gueltiges 'profile_params'")
        return body["profile_params"]

    async def logout(self, token: str) -> None:
        """Beendet die Login-Sitzung (I5). Der Wizard ruft das nach erfolgreicher Einrichtung;
        ein Fehler dort wird nur geloggt."""
        await self._request("POST", "/auth/logout", "Logout", ok=(200, 204), expect_json=False, headers=_bearer(token))

    async def delete_installation(self, tenant_id: str, installation_token: str) -> int | None:
        """Widerruft die Zugangsdaten dieser Anlage auf dem Server (Spec TP8 4, AWS-IoT 4.3), angemeldet
        mit dem Installations-Token. Gibt den HTTP-Status zurueck, None ohne Verbindung/Timeout. Wirft
        nie: das Entfernen der Integration laeuft in jedem Fall weiter."""
        try:
            async with asyncio.timeout(REVOKE_TIMEOUT_SECONDS):
                async with self._session.delete(
                    f"{self._base_url}{INSTALLATION_PATH.format(tenant_id=tenant_id)}",
                    headers=_bearer(installation_token),
                ) as response:
                    return response.status
        except (aiohttp.ClientError, TimeoutError):
            return None

    async def _error_text(self, response: aiohttp.ClientResponse) -> str:
        try:
            body = await response.json(content_type=None)
        except (aiohttp.ClientError, ValueError):
            return ""
        error = body.get("error") if isinstance(body, dict) else None
        return error[:ACCESS_DENIED_REASON_MAX] if isinstance(error, str) else ""

    async def _check_status(
        self, response: aiohttp.ClientResponse, what: str, ok: tuple[int, ...], on_400: type[ApiError],
    ) -> None:
        if response.status in ok:
            return
        if response.status == 401:
            raise InvalidAuth("Sitzung abgelaufen")
        text = await self._error_text(response)
        # Nie Tokens/Passwoerter loggen: nur Status und der Fehlertext des Servers.
        _LOGGER.warning("%s abgelehnt (HTTP %s): %s", what, response.status, text or "-")
        if response.status == 403:
            raise AccessDenied(text)
        if response.status == 400:
            raise on_400(f"{what} abgelehnt (HTTP 400): {text}")
        raise ApiError(f"{what} fehlgeschlagen (HTTP {response.status})")

    async def _request(
        self, method: str, path: str, what: str, *, ok: tuple[int, ...] = (200,),
        on_400: type[ApiError] = ApiError, expect_json: bool = True, **kwargs: Any,
    ) -> Any:
        """Eine Anfrage mit Zeitlimit; liefert den JSON-Body (None bei 204 oder expect_json=False)."""
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                async with self._session.request(method, f"{self._base_url}{path}", **kwargs) as response:
                    await self._check_status(response, what, ok, on_400)
                    if response.status == 204 or not expect_json:
                        return None
                    return await self._read_json(response, f"{what}: Antwort ist kein gueltiges JSON")
        except TimeoutError as error:
            raise CannotConnect(f"Abo-Service antwortet nicht ({what})") from error
        except aiohttp.ClientError as error:
            raise CannotConnect(f"Abo-Service nicht erreichbar: {error}") from error

    async def _read_json(self, response: aiohttp.ClientResponse, error_message: str) -> Any:
        """Liest den JSON-Body einer bereits als erfolgreich akzeptierten Antwort. Ein falscher
        Content-Type oder ungueltiges JSON ist eine kaputte Antwort (InvalidResponse), keine
        Verbindungsstoerung -- ohne diesen Fang wuerde aiohttp.ContentTypeError (Unterklasse von
        aiohttp.ClientError) faelschlich als CannotConnect ankommen, und ein reines
        JSONDecodeError (ValueError) gar nicht gefangen."""
        try:
            return await response.json()
        except (aiohttp.ContentTypeError, ValueError) as error:
            raise InvalidResponse(f"{error_message}: {error}") from error
