"""SmartHeat-Integration: konfiguriert die SmartHeat-Add-ons per Config-Flow, zeigt ihren Status
als Entities und ueberwacht sie (Spec TP7)."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from . import addon_control
from .const import DATA_INCOMPLETE, DOMAIN, repair_issue_id
from .coordinator import SmartHeatCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = SmartHeatCoordinator(hass, entry)
    entry.runtime_data = coordinator
    coordinator.async_start()
    if entry.data.get(DATA_INCOMPLETE):
        # Die Entities entstehen trotzdem, damit der Status (Konfiguration veraltet) sichtbar ist.
        ir.async_create_issue(
            hass, DOMAIN, repair_issue_id(entry.entry_id), is_fixable=False, severity=ir.IssueSeverity.WARNING,
            translation_key="complete_setup", translation_placeholders={"tenant": coordinator.tenant_id},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, repair_issue_id(entry.entry_id))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """v1 -> v2 (Spec TP7 2.1): Anlagenwerte in data, Optionen in options. Ein v1-Eintrag hat nur
    tenant_id/profile_id (client1) und wird als unvollstaendig markiert; der Reparaturhinweis
    fuehrt zu "Neu konfigurieren"."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        data = {"tenant_id": entry.data["tenant_id"], DATA_INCOMPLETE: True}
        if entry.data.get("profile_id"):
            data["profile_id"] = entry.data["profile_id"]
        hass.config_entries.async_update_entry(entry, data=data, options={}, version=2)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    ir.async_delete_issue(hass, DOMAIN, repair_issue_id(entry.entry_id))
    await addon_control.async_sign_off(hass, entry.data["tenant_id"])
