"""Async HTTP-Client fuer die heizungsserver-Accounts-API (Login, Tenants, Katalog,
Provisioning, Logout). Ersetzt die synchronen requests.post()-Aufrufe aus dem alten
heizungsbruecke/web.py-Wizard -- dieselbe API, nur async und ohne Flask-Session.
"""
from __future__ import annotations

from typing import Any

import aiohttp


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
                return await response.json()
        except aiohttp.ClientError as error:
            raise CannotConnect(f"Abo-Service nicht erreichbar: {error}") from error

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
