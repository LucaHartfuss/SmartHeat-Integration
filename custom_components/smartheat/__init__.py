"""SmartHeat-Integration: konfiguriert die SmartHeat-Add-ons per Config-Flow, zeigt ihren Status
als Entities und ueberwacht sie (Spec TP7)."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from . import addon_control, binary_sensor, sensor
from .const import DOMAIN, entry_incomplete, repair_issue_id
from .coordinator import SmartHeatCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = SmartHeatCoordinator(hass, entry)
    entry.runtime_data = coordinator
    coordinator.async_start()
    if entry_incomplete(entry.data):
        # Die Entities entstehen trotzdem, damit der Status (Konfiguration veraltet) sichtbar ist.
        ir.async_create_issue(
            hass, DOMAIN, repair_issue_id(entry.entry_id), is_fixable=False, severity=ir.IssueSeverity.WARNING,
            translation_key="complete_setup", translation_placeholders={"tenant": coordinator.tenant_id},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, repair_issue_id(entry.entry_id))
    _remove_orphaned_entities(hass, entry, coordinator.tenant_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _remove_orphaned_entities(hass: HomeAssistant, entry: ConfigEntry, tenant_id: str) -> None:
    """B-TP11-4: Registry-Eintraege dieses Eintrags ohne aktuelle Entity (z. B. offset aus 0.7.x, Hebel eines anderen
    Hebelsatzes). Nur fuer vollstaendige Eintraege: bis "Neu konfigurieren" behaelt ein unvollstaendiger Eintrag seine
    Entities (Plan 3c, Praezisierung 8)."""
    if entry_incomplete(entry.data):
        return
    wanted = {f"{tenant_id}_{key}" for key in (*sensor.entity_keys(entry.data), *binary_sensor.ENTITY_KEYS)}
    registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if registry_entry.platform == DOMAIN and registry_entry.unique_id not in wanted:
            registry.async_remove(registry_entry.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    ir.async_delete_issue(hass, DOMAIN, repair_issue_id(entry.entry_id))
    await addon_control.async_sign_off(hass, entry.data["tenant_id"])
