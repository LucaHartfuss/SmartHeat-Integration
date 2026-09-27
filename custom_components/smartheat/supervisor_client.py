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
sein. async_resolve_addons() schliesst genau diese Luecke: sie fragt den Supervisor nach
allen installierten Add-ons und findet den passenden Eintrag anhand von Repository-URL +
Slug-Suffix (beide SmartHeat-Add-ons teilen dieselbe Repository-URL, daher reicht die URL
allein nicht zur Unterscheidung), und prueft zusaetzlich die Mindestversion (TP6 I4) --
ein zu altes Add-on wird nicht als Treffer akzeptiert.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from aiohasupervisor.exceptions import SupervisorError
from awesomeversion import AwesomeVersion
from homeassistant.components.hassio import AddonError, AddonManager, get_supervisor_client
from homeassistant.core import HomeAssistant

from .const import ADDON_REPOSITORY_URL, MIN_ADDON_VERSIONS

_LOGGER = logging.getLogger(__name__)


class AddonNotFoundError(Exception):
    """Kein installiertes Add-on mit passender Repository-URL und Slug-Suffix gefunden."""

    def __init__(self, config_slug: str) -> None:
        super().__init__(f"Add-on '{config_slug}' ist nicht installiert")
        self.config_slug = config_slug


class AmbiguousAddonMatchError(Exception):
    """Mehrere installierte Add-ons passen auf dieselbe Repository-URL und dasselbe
    Slug-Suffix -- welches gemeint ist, ist echt mehrdeutig (z.B. eine uebrig gebliebene
    Dev-Installation) und wird nicht per Listenreihenfolge geraten (Review-Fund si-4)."""

    def __init__(self, config_slug: str, matches: list[str]) -> None:
        super().__init__(f"Add-on '{config_slug}' ist mehrfach installiert: {matches}")
        self.config_slug = config_slug
        self.matches = matches


class AddonOutdatedError(Exception):
    """Installierte Add-on-Version ist aelter als die Mindestversion dieses Wizards (I4)."""

    def __init__(self, config_slug: str, installed: str, required: str) -> None:
        super().__init__(f"Add-on '{config_slug}' {installed} ist aelter als {required}")
        self.config_slug = config_slug
        self.installed = installed
        self.required = required


@dataclass(frozen=True)
class ResolvedAddon:
    slug: str
    version: str | None


async def async_resolve_addons(
    hass: HomeAssistant, repository_url: str, min_versions: dict[str, str],
) -> dict[str, ResolvedAddon]:
    """Loest alle `min_versions`-Schluessel (bare config.yaml-Slugs) aus EINEM addons.list()
    auf (Review-Fund si-3) und prueft die Mindestversion. Eine rohe SupervisorError wird
    AddonError (Review-Fund si-1)."""
    try:
        installed = await get_supervisor_client(hass).addons.list()
    except SupervisorError as error:
        raise AddonError(f"Supervisor nicht erreichbar bei der Add-on-Aufloesung: {error}") from error

    result: dict[str, ResolvedAddon] = {}
    for config_slug, required in min_versions.items():
        matches = [
            addon for addon in installed
            if addon.url == repository_url and addon.slug.endswith(f"_{config_slug}")
        ]
        if not matches:
            raise AddonNotFoundError(config_slug)
        if len(matches) > 1:
            raise AmbiguousAddonMatchError(config_slug, [addon.slug for addon in matches])
        addon = matches[0]
        if addon.version is None or AwesomeVersion(addon.version) < AwesomeVersion(required):
            raise AddonOutdatedError(config_slug, addon.version or "unbekannt", required)
        result[config_slug] = ResolvedAddon(addon.slug, addon.version)
    return result


async def async_get_addon_managers(
    hass: HomeAssistant, addon_specs: list[tuple[str, str]],
) -> list[AddonManager]:
    """AddonManager je (Anzeigename, config_slug), Reihenfolge wie uebergeben."""
    resolved = await async_resolve_addons(
        hass, ADDON_REPOSITORY_URL, {slug: MIN_ADDON_VERSIONS[slug] for _, slug in addon_specs},
    )
    return [AddonManager(hass, _LOGGER, name, resolved[slug].slug) for name, slug in addon_specs]
