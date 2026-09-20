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

from aiohasupervisor.exceptions import SupervisorError
from homeassistant.components.hassio import AddonError, AddonManager, get_supervisor_client
from homeassistant.core import HomeAssistant

from .const import ADDON_REPOSITORY_URL

_LOGGER = logging.getLogger(__name__)


class AddonNotFoundError(Exception):
    """Kein installiertes Add-on mit passender Repository-URL und Slug-Suffix gefunden."""


class AmbiguousAddonMatchError(Exception):
    """Mehrere installierte Add-ons passen auf dieselbe Repository-URL und dasselbe
    Slug-Suffix -- welches gemeint ist, ist echt mehrdeutig (z.B. eine uebrig
    gebliebene Dev-Installation neben der Produktivinstallation), und wird nicht per
    Listenreihenfolge geraten (Korrektheit-Review-Fund si-4)."""


async def async_resolve_addon_slugs(
    hass: HomeAssistant, repository_url: str, config_slugs: list[str],
) -> dict[str, str]:
    """Loest mehrere config_slugs aus EINEM addons.list()-Aufruf auf (Effizienz-Review-
    Fund si-3: _push_config_and_finish rief bisher async_get_addon_manager fuer beide
    SmartHeat-Add-ons sequenziell auf, was zwei volle Supervisor-Roundtrips ausloeste,
    obwohl beide Slugs aus demselben Ergebnis aufloesbar sind). Uebersetzt eine rohe
    aiohasupervisor.SupervisorError in AddonError (Korrektheit-Review-Fund si-1): ein
    transienter Supervisor-Hickup fiel bisher als unbehandelte Exception durch den
    `except (AddonNotFoundError, AddonError)`-Block in config_flow.py, statt in den
    dafuer gebauten retry_push-Schritt zu fallen.
    """
    try:
        installed = await get_supervisor_client(hass).addons.list()
    except SupervisorError as error:
        raise AddonError(f"Supervisor nicht erreichbar bei der Add-on-Aufloesung: {error}") from error

    result: dict[str, str] = {}
    for config_slug in config_slugs:
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
            raise AmbiguousAddonMatchError(
                f"Mehrere installierte Add-ons passen auf Repository-URL '{repository_url}' und "
                f"Slug-Suffix '_{config_slug}': {matches}. Bitte doppelte/veraltete Installation "
                f"entfernen, bevor die Einrichtung fortgesetzt wird."
            )
        result[config_slug] = matches[0]
    return result


async def async_resolve_addon_slug(
    hass: HomeAssistant, repository_url: str, config_slug: str
) -> str:
    """Einzel-Slug-Variante -- delegiert an async_resolve_addon_slugs fuer den
    Ein-Slug-Fall (z.B. Tests, oder ein zukuenftiger Caller, der nur ein Add-on braucht).
    """
    return (await async_resolve_addon_slugs(hass, repository_url, [config_slug]))[config_slug]


async def async_get_addon_manager(
    hass: HomeAssistant, addon_name: str, config_slug: str
) -> AddonManager:
    """Loest den echten Slug auf und liefert einen dafuer konfigurierten AddonManager."""
    slug = await async_resolve_addon_slug(hass, ADDON_REPOSITORY_URL, config_slug)
    return AddonManager(hass, _LOGGER, addon_name, slug)


async def async_get_addon_managers(
    hass: HomeAssistant, addon_specs: list[tuple[str, str]],
) -> list[AddonManager]:
    """Loest mehrere Add-on-Manager aus EINEM addons.list()-Aufruf auf. addon_specs:
    Liste aus (addon_name, config_slug)-Paaren, Reihenfolge wird in der Rueckgabe
    beibehalten (Effizienz-Review-Fund si-3).
    """
    slugs = await async_resolve_addon_slugs(hass, ADDON_REPOSITORY_URL, [slug for _, slug in addon_specs])
    return [AddonManager(hass, _LOGGER, name, slugs[slug]) for name, slug in addon_specs]
