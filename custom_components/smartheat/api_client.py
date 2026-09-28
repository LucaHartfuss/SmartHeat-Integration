"""Async HTTP-Client fuer die heizungsserver-Accounts-API (Login, Tenants, Katalog,
Provisioning, Logout, Widerruf beim Entfernen). Ersetzt die synchronen requests.post()-Aufrufe
aus dem alten heizungsbruecke/web.py-Wizard -- dieselbe API, nur async und ohne Flask-Session.
"""
from __future__ import annotations

import asyncio
import base64
from typing import Any

import aiohttp

from .const import INSTALLATION_PATH, PROFILE_PATH, REVOKE_TIMEOUT_SECONDS


def _basic_auth_header(username: str, password: str) -> str:
    """Baut den Authorization-Header fuer HTTP Basic selbst (RFC 7617), statt der von aiohttp
    3.14 als deprecated markierten `auth=aiohttp.BasicAuth(...)`. `aiohttp.encode_basic_auth()`
    scheidet aus: die Mindestversion in hacs.json (HA 2026.4.0) bindet keine aiohttp-Version, in
    der diese Funktion garantiert existiert -- ein AttributeError daraus liegt ausserhalb der in
    delete_installation() gefangenen Exceptions und wuerde das Entfernen der Integration
    abbrechen. Niemals loggen (Regel 6)."""
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


class ApiError(Exception):
    """Basisklasse fuer alle Fehler dieses Clients."""


class CannotConnect(ApiError):
    """Der heizungsserver war nicht erreichbar (Netzwerkfehler)."""


class InvalidAuth(ApiError):
    """Zugangsdaten oder Sitzungstoken wurden vom Server abgelehnt (401)."""


class HeizungsserverClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str) -> None:
        self._session = session
        self._base_url = base_url

    async def login(self, email: str, password: str) -> str:
        try:
            async with self._session.post(
                f"{self._base_url}/auth/login", json={"email": email, "password": password}
            ) as response:
                if response.status == 401:
                    raise InvalidAuth("Ungueltige Zugangsdaten")
                if response.status != 200:
                    raise ApiError(f"Login fehlgeschlagen (HTTP {response.status})")
                body = await response.json()
                return body["token"]
        except aiohttp.ClientError as error:
            raise CannotConnect(f"Abo-Service nicht erreichbar: {error}") from error

    async def list_tenants(self, token: str) -> list[dict]:
        return await self._get_authenticated("/accounts/me/tenants", token)

    async def get_catalog(self, token: str) -> dict:
        catalog = await self._get_authenticated("/catalog", token)
        if not isinstance(catalog, dict) or not isinstance(catalog.get("profiles"), list):
            raise ApiError("Katalog-Antwort ohne 'profiles'-Liste")
        return catalog

    async def provision(self, token: str, tenant_id: str, profile_id: str) -> dict:
        try:
            async with self._session.post(
                f"{self._base_url}/tenants/{tenant_id}/provision",
                json={"profile_id": profile_id},
                headers={"Authorization": f"Bearer {token}"},
            ) as response:
                if response.status == 401:
                    raise InvalidAuth("Sitzung abgelaufen")
                if response.status != 200:
                    raise ApiError(f"Provisioning fehlgeschlagen (HTTP {response.status})")
                return await self._read_json(response, "Provisioning-Antwort ist kein gueltiges JSON")
        except aiohttp.ClientError as error:
            raise CannotConnect(f"Abo-Service nicht erreichbar: {error}") from error

    async def update_profile(self, token: str, tenant_id: str, profile_id: str) -> dict:
        """Profilwechsel ohne neue Zugangsdaten (Neu konfigurieren, Spec TP7 4.1). Liefert die
        profile_params fuer die Add-on-Optionen."""
        try:
            async with self._session.post(
                f"{self._base_url}{PROFILE_PATH.format(tenant_id=tenant_id)}",
                json={"profile_id": profile_id},
                headers={"Authorization": f"Bearer {token}"},
            ) as response:
                if response.status == 401:
                    raise InvalidAuth("Sitzung abgelaufen")
                if response.status != 200:
                    raise ApiError(f"Profilwechsel fehlgeschlagen (HTTP {response.status})")
                body = await self._read_json(response, "Profil-Antwort ist kein gueltiges JSON")
        except aiohttp.ClientError as error:
            raise CannotConnect(f"Abo-Service nicht erreichbar: {error}") from error
        if not isinstance(body, dict) or not isinstance(body.get("profile_params"), dict):
            raise ApiError("Profil-Antwort ohne gueltiges 'profile_params'")
        return body["profile_params"]

    async def logout(self, token: str) -> None:
        """Beendet die Login-Sitzung (I5). Der Wizard ruft das nach erfolgreicher Einrichtung;
        ein Fehler dort wird nur geloggt."""
        try:
            async with self._session.post(
                f"{self._base_url}/auth/logout", headers={"Authorization": f"Bearer {token}"},
            ) as response:
                if response.status == 401:
                    raise InvalidAuth("Sitzung abgelaufen")
                if response.status not in (200, 204):
                    raise ApiError(f"Logout fehlgeschlagen (HTTP {response.status})")
        except aiohttp.ClientError as error:
            raise CannotConnect(f"Abo-Service nicht erreichbar: {error}") from error

    async def delete_installation(self, tenant_id: str, username: str, password: str) -> int | None:
        """Widerruft die MQTT-Zugangsdaten dieser Anlage auf dem Server (Spec TP8 4), angemeldet mit
        genau diesen Zugangsdaten. Gibt den HTTP-Status zurueck, None ohne Verbindung/Timeout. Wirft
        nie: das Entfernen der Integration laeuft in jedem Fall weiter."""
        try:
            async with asyncio.timeout(REVOKE_TIMEOUT_SECONDS):
                async with self._session.delete(
                    f"{self._base_url}{INSTALLATION_PATH.format(tenant_id=tenant_id)}",
                    headers={"Authorization": _basic_auth_header(username, password)},
                ) as response:
                    return response.status
        except (aiohttp.ClientError, TimeoutError):
            return None

    async def _read_json(self, response: aiohttp.ClientResponse, error_message: str) -> Any:
        """Liest den JSON-Body einer bereits als 200 akzeptierten Antwort. Ein falscher
        Content-Type oder ungueltiges JSON ist eine kaputte Antwort (ApiError), keine
        Verbindungsstoerung -- ohne diesen Fang wuerde aiohttp.ContentTypeError (Unterklasse von
        aiohttp.ClientError) faelschlich als CannotConnect ankommen, und ein reines
        JSONDecodeError (ValueError) gar nicht gefangen."""
        try:
            return await response.json()
        except (aiohttp.ContentTypeError, ValueError) as error:
            raise ApiError(f"{error_message}: {error}") from error

    async def _get_authenticated(self, path: str, token: str) -> Any:
        try:
            async with self._session.get(
                f"{self._base_url}{path}", headers={"Authorization": f"Bearer {token}"}
            ) as response:
                if response.status == 401:
                    raise InvalidAuth("Sitzung abgelaufen")
                if response.status != 200:
                    raise ApiError(f"Anfrage an '{path}' fehlgeschlagen (HTTP {response.status})")
                return await response.json()
        except aiohttp.ClientError as error:
            raise CannotConnect(f"Abo-Service nicht erreichbar: {error}") from error
