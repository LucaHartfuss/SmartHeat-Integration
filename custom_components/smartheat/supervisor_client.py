"""Async Client fuer die Supervisor-Management-API. Gleicher Endpunkt-Vertrag wie das
bestehende heizungsbruecke/supervisor_api.py (POST /addons/<slug>/options), nur mit
explizitem Ziel-Slug statt der add-on-eigenen "self"-Kurzform, und zusaetzlich Restart --
die Integration ruft diese API mit HA Cores eigenem, weiterreichendem Supervisor-Token auf,
nicht mit dem beschraenkten Token eines einzelnen Add-ons.
"""
from __future__ import annotations

import aiohttp


class SupervisorApiError(Exception):
    """Ein Supervisor-API-Aufruf ist fehlgeschlagen (Netzwerk oder Non-200-Antwort)."""


class SupervisorClient:
    def __init__(self, session: aiohttp.ClientSession, token: str, base_url: str = "http://supervisor") -> None:
        self._session = session
        self._token = token
        self._base_url = base_url

    async def set_addon_options(self, slug: str, options: dict) -> None:
        await self._post(f"/addons/{slug}/options", json={"options": options})

    async def restart_addon(self, slug: str) -> None:
        await self._post(f"/addons/{slug}/restart")

    async def _post(self, path: str, json: dict | None = None) -> None:
        try:
            async with self._session.post(
                f"{self._base_url}{path}",
                json=json,
                headers={"Authorization": f"Bearer {self._token}"},
            ) as response:
                if response.status != 200:
                    raise SupervisorApiError(
                        f"Supervisor-Aufruf '{path}' fehlgeschlagen (HTTP {response.status})"
                    )
        except aiohttp.ClientError as error:
            raise SupervisorApiError(f"Supervisor-API nicht erreichbar: {error}") from error
