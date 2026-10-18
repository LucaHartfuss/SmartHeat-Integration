"""Loest den echten Add-on-Slug auf und liefert einen HA-eigenen AddonManager dafuer.

Ersetzt die vorherige, handgestrickte SupervisorClient-Klasse (rohes aiohttp + manueller
os.environ["SUPERVISOR_TOKEN"]-Zugriff) durch Home Assistant Cores eigenes
homeassistant.components.hassio.AddonManager -- denselben Baustein, den z.B. die Z-Wave-JS-
und Matter-Integrationen fuer ihre gebuendelten Add-ons verwenden (I4 aus der finalen Review).

Der Unterschied zu jenen Integrationen: deren Add-ons kommen aus dem offiziellen "core"-
Repository und haben stabile, unpraefigierte Slugs (z.B. "core_zwave_js"). Das SmartHeat-Add-on
kommt aus einem eigenen Custom-Repository -- ein Supervisor praefigiert einen von dort installierten
Add-on-Slug mit einem Repository-Hash (verifiziert gegen einen echten Supervisor: "heizungsbruecke"
wird dort zu "f5f6325b_heizungsbruecke"). AddonManager selbst loest das nicht auf -- sein addon_slug
muss bereits der echte, installationsspezifische Slug sein. async_resolve_addons() schliesst genau
diese Luecke und folgt dabei dieser Regel (Sicherheitsfix 0f34d56): Das Store-Repository wird allein
an seiner Eintragsadresse (`source`, auch mit `#<branch>`) erkannt und liefert den Repository-Slug;
ein installiertes Add-on zaehlt nur, wenn es zu diesem Repository gehoert und genau den Slug
"<Repository-Slug>_<config_slug>" hat. Weder die `url` aus der config.yaml des Add-ons noch ein blosses
Slug-Suffix zaehlen. Geprueft wird zusaetzlich die Mindestversion (TP6 I4): ein zu altes Add-on ist kein Treffer.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from aiohasupervisor.exceptions import SupervisorError
from awesomeversion import AwesomeVersion
from homeassistant.components.hassio import AddonError, AddonManager, get_supervisor_client
from homeassistant.core import HomeAssistant
from homeassistant.helpers.hassio import is_hassio

from .const import ADDON_REPOSITORY_URL

_LOGGER = logging.getLogger(__name__)


class AddonNotFoundError(Exception):
    """Kein installiertes Add-on mit passender Repository-URL und Slug-Suffix gefunden."""

    def __init__(self, config_slug: str) -> None:
        super().__init__(f"Add-on '{config_slug}' ist nicht installiert")
        self.config_slug = config_slug


class AmbiguousAddonMatchError(Exception):
    """Mehrere installierte Add-ons passen auf dieselbe Repository-URL und dasselbe
    Slug-Suffix -- welches gemeint ist, ist echt mehrdeutig (z.B. eine uebrig gebliebene
    Dev-Installation) und wird nicht per Listenreihenfolge geraten."""

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


_BRANCH = re.compile(r"[A-Za-z0-9._/-]+")


def _valid_branch(branch: str) -> bool:
    """Branch-Angabe nur aus [A-Za-z0-9._/-], nicht leer, ohne `..` und ohne fuehrendes `/` (also auch ohne
    Leerraum, Steuerzeichen und ein zweites `#`)."""
    return bool(_BRANCH.fullmatch(branch)) and ".." not in branch and not branch.startswith("/")


def _normalized_repository(value: str) -> str | None:
    """Adresse ohne Branch-Angabe `#<branch>`, ohne / und .git am Ende, klein geschrieben; None bei einer
    ungueltigen Branch-Angabe (fail closed)."""
    address, hash_mark, branch = value.strip().partition("#")
    if hash_mark and not _valid_branch(branch):
        return None
    return address.lower().rstrip("/").removesuffix(".git")


def _same_repository(value: object, repository_url: str = ADDON_REPOSITORY_URL) -> bool:
    """Adresse eines Store-Repositorys gleich `repository_url`: ohne / und .git am Ende, Gross-/Kleinschreibung egal
    (GitHub-Adressen unterscheiden sie nicht); das Schema bleibt unterschieden (http ist nicht https). Ein Branch
    desselben Repositorys (`<Adresse>#<branch>`, Branch siehe _valid_branch) gilt als gleichwertig (5c-W-11); die
    Slug-Pruefung in matching_addons bleibt."""
    if not isinstance(value, str):
        return False
    normalized = _normalized_repository(value)
    return normalized is not None and normalized == _normalized_repository(repository_url)


async def async_repository_slug(hass: HomeAssistant, repository_url: str = ADDON_REPOSITORY_URL) -> str | None:
    """Slug (Hash) unseres Repositorys im Add-on-Store, nur lesend. Erkannt nur an `source`, der Adresse, unter der
    das Repository eingetragen ist; `url` kommt aus der repository.yaml des Autors und beweist nichts. None, wenn es
    nicht (oder mehrdeutig) eingetragen ist: dann gehoert kein Add-on zu SmartHeat (fail closed). Wirft AddonError."""
    try:
        repositories = await get_supervisor_client(hass).store.repositories_list()
    except SupervisorError as error:
        raise AddonError(f"Add-on-Store nicht lesbar: {type(error).__name__}") from None
    slugs = [repository.slug for repository in repositories if _same_repository(repository.source, repository_url)]
    return slugs[0] if len(slugs) == 1 else None


def matching_addons(installed, repository_slug: str | None, config_slug: str) -> list:
    """Installierte Add-ons genau aus unserem Repository (`repository` = dessen Slug, Slug = "<Repo-Slug>_<config_slug>").
    Weder die `url` eines Add-ons (aus dessen config.yaml, frei waehlbar) noch ein Slug-Suffix (auch
    local_heizungsbruecke oder ein fremdes Repository) zaehlen. Gemeinsame Zuordnung fuer async_resolve_addons,
    async_find_addon_managers und addon_control.async_bridge_slug."""
    if not repository_slug:
        return []
    expected = f"{repository_slug}_{config_slug}"
    return [addon for addon in installed if addon.repository == repository_slug and addon.slug == expected]


async def async_resolve_addons(
    hass: HomeAssistant, repository_url: str, min_versions: dict[str, str],
) -> dict[str, ResolvedAddon]:
    """Loest alle `min_versions`-Schluessel (bare config.yaml-Slugs) aus EINEM addons.list() auf, nur Add-ons aus dem
    Repository mit der Store-Adresse `repository_url`, und prueft die Mindestversion. Eine rohe SupervisorError wird
    AddonError."""
    repository_slug = await async_repository_slug(hass, repository_url)
    try:
        installed = await get_supervisor_client(hass).addons.list()
    except SupervisorError as error:
        raise AddonError(f"Supervisor nicht erreichbar bei der Add-on-Aufloesung: {type(error).__name__}") from None

    result: dict[str, ResolvedAddon] = {}
    for config_slug, required in min_versions.items():
        matches = matching_addons(installed, repository_slug, config_slug)
        if not matches:
            raise AddonNotFoundError(config_slug)
        if len(matches) > 1:
            raise AmbiguousAddonMatchError(config_slug, [addon.slug for addon in matches])
        addon = matches[0]
        if addon.version is None or AwesomeVersion(addon.version) < AwesomeVersion(required):
            raise AddonOutdatedError(config_slug, addon.version or "unbekannt", required)
        result[config_slug] = ResolvedAddon(addon.slug, addon.version)
    return result


async def async_find_addon_managers(
    hass: HomeAssistant, addon_specs: list[tuple[str, str]],
) -> dict[str, AddonManager | None]:
    """Fuer den Waechter: jeder config-Slug einzeln aus EINEM addons.list() (Zuordnung wie in der Modul-Doku:
    Repository per `source`, genauer Slug), ohne Mindestversion. Nicht oder mehrfach installiert -> None (mit
    einer Warnung, die die beiden Faelle unterscheidet). Ist Hass.io gar nicht geladen (Core/Container-
    Installation, aus einem Backup wiederhergestellt) wuerde get_supervisor_client() mit einem rohen
    KeyError abbrechen -- das wird hier zu einer regulaeren AddonError, damit der Aufrufer sauber degradiert
    statt mittendrin abzubrechen. Eine SupervisorError beim eigentlichen Abruf wird ebenfalls AddonError."""
    if not is_hassio(hass):
        raise AddonError("Hass.io ist auf dieser Installation nicht geladen")
    repository_slug = await async_repository_slug(hass)
    try:
        installed = await get_supervisor_client(hass).addons.list()
    except SupervisorError as error:
        raise AddonError(f"Supervisor nicht erreichbar bei der Add-on-Aufloesung: {type(error).__name__}") from None
    managers: dict[str, AddonManager | None] = {}
    for name, config_slug in addon_specs:
        matches = matching_addons(installed, repository_slug, config_slug)
        if len(matches) == 1:
            managers[config_slug] = AddonManager(hass, _LOGGER, name, matches[0].slug)
            continue
        reason = "nicht installiert" if not matches else "mehrfach installiert"
        _LOGGER.warning("Add-on %s (%s) ist %s, wird uebersprungen", name, config_slug, reason)
        managers[config_slug] = None
    return managers
