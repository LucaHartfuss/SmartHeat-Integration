"""SmartHeat-Integration ab 1.0.0 (Spec 5c 2.2): richtet das Add-on als Geraet ueber die Einrichtungs-API des Servers
ein, zeigt seinen Status als Entities, macht seine Hinweise zu Reparatur-Hinweisen, bietet die Wunschtemperatur an
und ueberwacht die Heizungsbruecke (zweiter Waechter)."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from . import binary_sensor, number, repairs, sensor
from .const import DOMAIN
from .coordinator import SmartHeatCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SENSOR]
# Eintrag aus einer Version vor 1.0.0 (ohne Geraete-ID, z. B. client1 vor dem Umzug in Phase 1 Schritt 3): Fehler mit
# Text statt eines KeyError, dazu ein Reparatur-Hinweis.
ALTER_EINTRAG = "alter_eintrag"


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Eintraege bis 0.16 (VERSION 2) werden nicht migriert (der Umzug von client1 in Phase 1 Schritt 3 lief ueber
    Entfernen und Neueinrichten). Sie bleiben unveraendert (ein zurueckgerolltes 0.16.1 richtet sie weiter ein) und
    laufen bis async_setup_entry, das sie mit Text und Reparatur-Hinweis ablehnt (alter_eintrag) statt mit einem
    Migrationsfehler ohne Erklaerung."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not entry.data.get("geraet_id"):
        ir.async_create_issue(hass, DOMAIN, ALTER_EINTRAG, is_fixable=False, severity=ir.IssueSeverity.ERROR,
                              translation_key=ALTER_EINTRAG)
        raise ConfigEntryError(translation_domain=DOMAIN, translation_key=ALTER_EINTRAG)
    coordinator = SmartHeatCoordinator(hass, entry)
    entry.runtime_data = coordinator
    coordinator.async_start()
    _remove_orphaned_entities(hass, entry, coordinator.tenant_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _remove_orphaned_entities(hass: HomeAssistant, entry: ConfigEntry, tenant_id: str) -> None:
    """B-TP11-4: Registry-Eintraege dieses Eintrags ohne aktuelle Entity (z. B. offset aus 0.7.x, Hebel eines anderen
    Hebelsatzes)."""
    wanted = {f"{tenant_id}_{key}" for key in (*sensor.entity_keys(entry.data), *binary_sensor.ENTITY_KEYS, number.KEY)}
    registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if registry_entry.platform == DOMAIN and registry_entry.unique_id not in wanted:
            registry.async_remove(registry_entry.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Entladen (auch Deaktivieren): die Hinweise des Geraets verschwinden mit; nach dem Laden bringt das naechste
    Status-Ereignis (spaetestens das Lebenszeichen) sie zurueck."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        repairs.async_clear(hass)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Spec 5c 4: das Geraet regelt weiter (kein Server-Aufruf, das Add-on laeuft weiter); Entfernen der Anlage geht
    ueber das Portal oder "Neu konfigurieren". Nur die eigenen Reparatur-Hinweise verschwinden."""
    repairs.async_clear(hass)
    ir.async_delete_issue(hass, DOMAIN, ALTER_EINTRAG)
