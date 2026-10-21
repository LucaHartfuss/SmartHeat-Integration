"""Attrappe des Supervisor-Clients (`aiohasupervisor.SupervisorClient`) fuer die Integrationstests (Testoffensive
T5', Spec Nr. 23: Attrappen nur an der Grenze und nur mit Spec). Die Integration und der HA-`AddonManager` holen ihren
Client ueber `get_supervisor_client`; die Fixture `supervisor` setzt dort und bei `is_hassio` an. Eigene Funktionen der
Integration werden nicht ersetzt.

`create_autospec(SupervisorClient, instance=True)` macht aus den Properties `addons`, `store` und `discovery` nur
gewoehnliche MagicMocks; deshalb bekommt jeder Unter-Client ein eigenes `create_autospec` (Signaturen geprueft,
Methoden als AsyncMock). Verhalten haben genau die Methoden, die Integration und `AddonManager` aufrufen."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import create_autospec
from uuid import uuid4

import pytest
from aiohasupervisor import SupervisorClient
from aiohasupervisor.addons import AddonsClient
from aiohasupervisor.discovery import DiscoveryClient
from aiohasupervisor.exceptions import SupervisorBadRequestError, SupervisorNotFoundError
from aiohasupervisor.models import AddonsOptions, AddonState, Discovery, Repository, StoreAddRepository
from aiohasupervisor.models.base import DEFAULT
from aiohasupervisor.store import StoreClient
from homeassistant.components.hassio import addon_manager

from custom_components.smartheat import addon_control, config_flow, supervisor_client
from custom_components.smartheat.const import ADDON_REPOSITORY_URL, HEIZUNGSBRUECKE_ADDON_SLUG

REPOSITORY_SLUG = "abc"
BRIDGE_SLUG = f"{REPOSITORY_SLUG}_{HEIZUNGSBRUECKE_ADDON_SLUG}"


def repository(slug: str = REPOSITORY_SLUG, source: str = ADDON_REPOSITORY_URL, url: str | None = None) -> Repository:
    """Store-Repository (echtes Modell): `source` ist die eingetragene Adresse, `url` die aus der repository.yaml."""
    return Repository(slug=slug, name="SmartHeat", source=source, url=url or source, maintainer="x")


def installed_addon(slug: str, *, repository: str | None = None, url: str = ADDON_REPOSITORY_URL,
                    version: str | None = "1.0.0", state: AddonState = AddonState.STARTED) -> SimpleNamespace:
    """Eintrag aus `addons.list()`: die Felder, die Integration und Attrappe lesen (`repository` = Slug des
    Store-Repositorys, `url` = frei waehlbare Homepage aus der config.yaml des Add-ons)."""
    return SimpleNamespace(slug=slug, repository=repository or slug.split("_")[0], url=url, version=version,
                           state=state)


def discovery_message(config, *, addon: str = BRIDGE_SLUG, service: str = "smartheat") -> Discovery:
    return Discovery(addon=addon, service=service, uuid=uuid4(), config=config)


class FakeSupervisor:
    """Zustand eines Supervisors: Store-Repositorys, installierbare Store-Add-ons, installierte Add-ons, Optionen je
    Slug, Discovery-Liste. `errors` bildet einen Methodennamen ("store.add_repository") oder (Name, Slug) auf eine
    `SupervisorError` ab, die dieser Aufruf wirft."""

    def __init__(self) -> None:
        self.hassio = True
        self.repositories: list = [repository()]
        # Slug, unter dem store.add_repository die Adresse eintraegt; None: das Eintragen bleibt ohne Wirkung.
        self.added_repository_slug: str | None = None
        self.store_addons: dict[str, str] = {}  # installierbar: Slug -> Version
        self.installed: list = [installed_addon(BRIDGE_SLUG)]
        self.addon_options: dict[str, object] = {}  # Optionen (config) je Slug, wie addon_info sie liefert
        self.supervision: dict[str, dict] = {}  # watchdog/boot je Slug, gesetzt ueber set_addon_options
        self.config_written: list[str] = []  # Slugs, an die set_addon_options eine Konfiguration schrieb
        self.discovery: list = []
        self.errors: dict = {}
        self.actions: list[tuple[str, str]] = []  # (start|restart|stop|uninstall, Slug) in Aufrufreihenfolge
        self.stdin: list[tuple[str, bytes]] = []  # (Slug, Bytes) je write_addon_stdin in Aufrufreihenfolge

        self.client = create_autospec(SupervisorClient, instance=True)
        self.client.addons = create_autospec(AddonsClient, instance=True)
        self.client.store = create_autospec(StoreClient, instance=True)
        self.client.discovery = create_autospec(DiscoveryClient, instance=True)
        store, addons = self.client.store, self.client.addons
        store.repositories_list.side_effect = self._repositories_list
        store.add_repository.side_effect = self._add_repository
        store.addon_info.side_effect = self._store_addon_info
        store.install_addon.side_effect = self._install_addon
        addons.list.side_effect = self._addons_list
        addons.addon_info.side_effect = self._addon_info
        addons.set_addon_options.side_effect = self._set_addon_options
        addons.start_addon.side_effect = self._start_addon
        addons.restart_addon.side_effect = self._restart_addon
        addons.stop_addon.side_effect = self._stop_addon
        addons.uninstall_addon.side_effect = self._uninstall_addon
        addons.write_addon_stdin.side_effect = self._write_addon_stdin
        self.client.discovery.list.side_effect = self._discovery_list

    def get_supervisor_client(self, hass):
        return self.client

    def is_hassio(self, hass) -> bool:
        return self.hassio

    def _check(self, name: str, slug: str | None = None) -> None:
        error = self.errors.get((name, slug)) if slug is not None else None
        if error is None:
            error = self.errors.get(name)
        if error is not None:
            raise error

    def _find(self, slug: str):
        return next((addon for addon in self.installed if addon.slug == slug), None)

    def _installed_or_404(self, slug: str):
        if (addon := self._find(slug)) is None:
            raise SupervisorNotFoundError(f"Add-on {slug} ist nicht installiert")
        return addon

    # --- store ---

    def _repositories_list(self):
        self._check("store.repositories_list")
        return list(self.repositories)

    def _add_repository(self, options: StoreAddRepository) -> None:
        self._check("store.add_repository")
        if self.added_repository_slug is not None:
            self.repositories.append(repository(self.added_repository_slug, source=options.repository))

    def _store_addon_info(self, addon: str):
        self._check("store.addon_info", addon)
        installed = self._find(addon) is not None
        if not installed and addon not in self.store_addons:
            raise SupervisorNotFoundError(f"Add-on {addon} ist nicht im Store")
        return SimpleNamespace(slug=addon, installed=installed, available=True,
                               to_dict=lambda: {"slug": addon, "installed": installed, "available": True})

    def _install_addon(self, addon: str, options=None) -> None:
        self._check("store.install_addon", addon)
        if addon not in self.store_addons:
            raise SupervisorNotFoundError(f"Add-on {addon} ist nicht im Store")
        self.installed.append(installed_addon(addon, version=self.store_addons[addon], state=AddonState.STOPPED))

    # --- addons ---

    def _addons_list(self):
        self._check("addons.list")
        return list(self.installed)

    def _addon_info(self, addon: str):
        self._check("addons.addon_info", addon)
        found = self._installed_or_404(addon)
        return SimpleNamespace(slug=addon, available=True, hostname=addon.replace("_", "-"),
                               options=self.addon_options.get(addon, {}), state=found.state, update_available=False,
                               version=found.version)

    def _set_addon_options(self, addon: str, options: AddonsOptions) -> None:
        self._check("addons.set_addon_options", addon)
        self._installed_or_404(addon)
        if options.config is not DEFAULT:
            self.config_written.append(addon)
            self.addon_options[addon] = options.config
        if options.watchdog is not None or options.boot is not None:
            self.supervision[addon] = {"watchdog": options.watchdog, "boot": options.boot}

    def _start_addon(self, addon: str) -> None:
        self._check("addons.start_addon", addon)
        self._installed_or_404(addon).state = AddonState.STARTED
        self.actions.append(("start", addon))

    def _restart_addon(self, addon: str) -> None:
        self._check("addons.restart_addon", addon)
        self._installed_or_404(addon).state = AddonState.STARTED
        self.actions.append(("restart", addon))

    def _stop_addon(self, addon: str) -> None:
        self._check("addons.stop_addon", addon)
        self._installed_or_404(addon).state = AddonState.STOPPED
        self.actions.append(("stop", addon))

    def _uninstall_addon(self, addon: str, options=None) -> None:
        self._check("addons.uninstall_addon", addon)
        self.installed.remove(self._installed_or_404(addon))
        self.actions.append(("uninstall", addon))

    def _write_addon_stdin(self, addon: str, stdin: bytes) -> None:
        """Der Supervisor schreibt nur in das stdin eines laufenden Containers; sonst 400 (Annahme nach der
        Supervisor-Quelle, im Labor zu bestaetigen, L4-21)."""
        self._check("addons.write_addon_stdin", addon)
        if self._installed_or_404(addon).state != AddonState.STARTED:
            raise SupervisorBadRequestError(f"Add-on {addon} laeuft nicht")
        self.stdin.append((addon, stdin))

    # --- discovery ---

    def _discovery_list(self):
        self._check("discovery.list")
        return list(self.discovery)


@pytest.fixture
def supervisor(monkeypatch) -> FakeSupervisor:
    """FakeSupervisor an `get_supervisor_client` (Integration und HA-`AddonManager`, der aus `.handler` importiert;
    `config_flow` importiert die Funktion nicht) und an `is_hassio`."""
    fake = FakeSupervisor()
    for module in (supervisor_client, addon_control, addon_manager):
        monkeypatch.setattr(module, "get_supervisor_client", fake.get_supervisor_client)
    for module in (supervisor_client, addon_control, config_flow):
        monkeypatch.setattr(module, "is_hassio", fake.is_hassio)
    return fake
