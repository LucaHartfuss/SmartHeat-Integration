"""SmartHeat-Integration: richtet das Add-on als Geraet ueber die Einrichtungs-API des Servers
ein, zeigt seinen Status als Entities, macht seine Hinweise zu Reparatur-Hinweisen, bietet die Wunschtemperatur an
und ueberwacht die Heizungsbruecke (zweiter Waechter); das Loeschen des Eintrags meldet das Geraet lokal ab."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import translation
from homeassistant.loader import async_get_integration

from . import addon_control, geraete, push, registry_umzug, repairs
from .const import DOMAIN
from .coordinator import SmartHeatCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = SmartHeatCoordinator(hass, entry)
    entry.runtime_data = coordinator
    integration = await async_get_integration(hass, DOMAIN)
    # Der Rueckfallname eines Kreis-Geraets kommt aus den Uebersetzungen; das Geraete-Verzeichnis liest nur den Cache.
    await translation.async_get_translations(hass, hass.config.language, "device", [DOMAIN])
    # Erst sichern und umziehen, dann das Anlagengeraet, dann starten. Scheitert die Sicherung, bleibt die Registry
    # unberuehrt; scheitert der Umzug mittendrin, steht der Vorzustand in der Sicherung und der naechste Lauf plant den
    # Rest. Der Waechter laeuft erst nach dem Umzug: Home Assistant raeumt bei einem verschobenen Laden auch die schon
    # gestarteten Aufraeumfunktionen ab, und ein Start trotz gescheitertem Umzug liesse den naechsten Lauf Eintraege
    # mit Verlauf als verwaist entfernen.
    await registry_umzug.async_pflegen(hass, entry)
    geraete.async_anlage(hass, entry, None)
    await coordinator.push.async_laden()
    coordinator.async_start(str(integration.version))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_remove_config_entry_device(hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry) -> bool:
    """Von Hand loeschen darf der Kunde nur das Geraet eines Kreises, den die Anlage nicht mehr meldet; die Anlage selbst
    und jeden gemeldeten Kreis nicht. Vor dem ersten Ereignis ist nichts bekannt: dann nichts."""
    coordinator = getattr(entry, "runtime_data", None)
    nummer = geraete.kreis_nummer(entry.data["tenant_id"], device)
    return coordinator is not None and coordinator.data is not None and nummer is not None and nummer not in coordinator.kreise


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
    nur eine WARNING. Dazu verschwinden die eigenen Reparatur-Hinweise samt dem Repair des Waechters und der Store des Pushs."""
    await addon_control.async_entfernen_melden(hass, entry.data.get("geraet_id"))
    repairs.async_clear(hass)
    await registry_umzug.async_sicherung_loeschen(hass, entry)
    await push.async_store_loeschen(hass)
