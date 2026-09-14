"""Loest den echten Add-on-Slug auf und liefert einen HA-eigenen AddonManager dafuer.

Ersetzt die vorherige, handgestrickte SupervisorClient-Klasse (rohes aiohttp + manueller
os.environ["SUPERVISOR_TOKEN"]-Zugriff) durch Home Assistant Cores eigenes
homeassistant.components.hassio.AddonManager -- denselben Baustein, den z.B. die Z-Wave-JS-
und Matter-Integrationen fuer ihre gebuendelten Add-ons verwenden (I4 aus der finalen Review).

Der Unterschied zu jenen Integrationen: deren Add-ons kommen aus dem offiziellen "core"-
Repository und haben stabile, unpraefigierte Slugs (z.B. "core_zwave_js"). Die beiden
SmartHeat-Add-ons kommen aus einem eigenen Custom-Repository -- ein Supervisor praefigiert
einen von dort installierten Add-on-Slug mit einem Repository-Hash (verifiziert gegen einen
echten Supervisor, siehe Task 14 in .superpowers/sdd/2026-09-14-smartheat-config-integration/
progress.md: "heizungsbruecke" wird dort zu "f5f6325b_heizungsbruecke"). AddonManager selbst
loest das nicht auf -- sein addon_slug muss bereits der echte, installationsspezifische Slug
sein. async_resolve_addon_slug() schliesst genau diese Luecke: sie fragt den Supervisor nach
allen installierten Add-ons und findet den passenden Eintrag anhand von Repository-URL +
Slug-Suffix (beide SmartHeat-Add-ons teilen dieselbe Repository-URL, daher reicht die URL
allein nicht zur Unterscheidung).
"""
from __future__ import annotations

import logging

from homeassistant.components.hassio import AddonManager, get_supervisor_client
from homeassistant.core import HomeAssistant

from .const import ADDON_REPOSITORY_URL

_LOGGER = logging.getLogger(__name__)


class AddonNotFoundError(Exception):
    """Kein installiertes Add-on mit passender Repository-URL und Slug-Suffix gefunden."""


async def async_resolve_addon_slug(
    hass: HomeAssistant, repository_url: str, config_slug: str
) -> str:
    """Loest den echten, installationsspezifischen Slug eines Custom-Repository-Add-ons auf."""
    installed = await get_supervisor_client(hass).addons.list()
    matches = [
        addon.slug
        for addon in installed
        if addon.url == repository_url and addon.slug.endswith(f"_{config_slug}")
    ]
    if not matches:
        raise AddonNotFoundError(
            f"Kein installiertes Add-on mit Repository-URL '{repository_url}' und "
            f"Slug-Suffix '_{config_slug}' gefunden -- ist das Add-on installiert?"
        )
    if len(matches) > 1:
        _LOGGER.warning(
            "Mehrere Add-ons passen auf Repository-URL '%s' und Slug-Suffix '_%s' (%s) -- "
            "verwende den ersten Treffer",
            repository_url, config_slug, matches,
        )
    return matches[0]


async def async_get_addon_manager(
    hass: HomeAssistant, addon_name: str, config_slug: str
) -> AddonManager:
    """Loest den echten Slug auf und liefert einen dafuer konfigurierten AddonManager."""
    slug = await async_resolve_addon_slug(hass, ADDON_REPOSITORY_URL, config_slug)
    return AddonManager(hass, _LOGGER, addon_name, slug)
