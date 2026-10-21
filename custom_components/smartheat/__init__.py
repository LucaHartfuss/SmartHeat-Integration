"""SmartHeat-Integration ab 1.0.0 (Spec 5c 2.2): richtet das Add-on als Geraet ueber die Einrichtungs-API des Servers
ein, zeigt seinen Status als Entities, macht seine Hinweise zu Reparatur-Hinweisen, bietet die Wunschtemperatur an
und ueberwacht die Heizungsbruecke (zweiter Waechter); das Loeschen des Eintrags meldet das Geraet lokal ab (L4-06)."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import addon_control, binary_sensor, number, repairs, sensor
from .const import DOMAIN
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
    """L4-06 Variante C (aendert Spec 5c 4): das Loeschen des Eintrags meldet das Geraet ueber das stdin der
    Heizungsbruecke lokal ab (Ursprungswerte, Portal "Abgemeldet"); kein Server-Aufruf, das Add-on laeuft weiter, aus
    dem Konto entfernt der Kunde das Geraet im Portal. Fehler sind nur eine WARNING. Dazu verschwinden die eigenen
    Reparatur-Hinweise."""
    await addon_control.async_entfernen_melden(hass, entry.data.get("geraet_id"))
    repairs.async_clear(hass)
