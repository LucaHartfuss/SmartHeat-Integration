"""SmartHeat-Integration: richtet das Add-on als Geraet ueber die Einrichtungs-API des Servers
ein, zeigt seinen Status als Entities, macht seine Hinweise zu Reparatur-Hinweisen, bietet die Wunschtemperatur an
und ueberwacht die Heizungsbruecke (zweiter Waechter); das Loeschen des Eintrags meldet das Geraet lokal ab."""
from __future__ import annotations

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import addon_control, binary_sensor, number, repairs, sensor
from .const import DOMAIN, watchdog_notification_id
from .coordinator import SmartHeatCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = SmartHeatCoordinator(hass, entry)
    entry.runtime_data = coordinator
    coordinator.async_start()
    _remove_orphaned_entities(hass, entry, coordinator.tenant_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _remove_orphaned_entities(hass: HomeAssistant, entry: ConfigEntry, tenant_id: str) -> None:
    """Registry-Eintraege dieses Eintrags ohne aktuelle Entity (z. B. ein nicht mehr angebotener Sensor, Hebel eines
    anderen Hebelsatzes)."""
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
    """Das Loeschen des Eintrags meldet das Geraet ueber das stdin der
    Heizungsbruecke lokal ab (Ursprungswerte, Portal "Abgemeldet"); kein Server-Aufruf, das Add-on laeuft weiter, aus
    dem Konto entfernt der Kunde das Geraet im Portal (offene Grenze: offene-punkte.md L4-06-Konto). Fehler sind
    nur eine WARNING. Dazu verschwinden die eigenen Reparatur-Hinweise und eine offene Meldung des Waechters."""
    await addon_control.async_entfernen_melden(hass, entry.data.get("geraet_id"))
    repairs.async_clear(hass)
    if isinstance(tenant_id := entry.data.get("tenant_id"), str):  # ein kaputter Eintrag laesst sich trotzdem loeschen
        persistent_notification.async_dismiss(hass, watchdog_notification_id(tenant_id))
