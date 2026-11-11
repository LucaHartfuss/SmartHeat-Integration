"""Geraetebaum im Geraete-Verzeichnis von Home Assistant: ein Geraet fuer die Anlage und je Heizkreis eines, das ueber
der Anlage haengt. Namen kommen als Kundendaten aus dem Status-Ereignis (gekuerzt, ohne Inhalt im Log); fehlt der
Name eines Kreises, gilt der Rueckfall der Uebersetzung. Eine `configuration_url` gibt es nicht. Die Entities geben
nur die Kennung der Anlage an (`anlage_kennung`); Name, Modell und Version setzt allein der Coordinator."""
from __future__ import annotations

from collections.abc import Mapping

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import UNDEFINED

from .const import DOMAIN, GERAET_NAME_MAX, PRODUKT_NAME

HOST_NAME = "Home Assistant"
_KREIS_PRAEFIX = "_k"


def anlage_kennung(tenant_id: str) -> tuple[str, str]:
    return (DOMAIN, tenant_id)


def kreis_kennung(tenant_id: str, nummer: int) -> tuple[str, str]:
    return (DOMAIN, f"{tenant_id}{_KREIS_PRAEFIX}{nummer}")


def kreis_nummer(tenant_id: str, geraet: DeviceEntry) -> int | None:
    """Die Kreisnummer eines Kreis-Geraets dieser Anlage; None fuer jedes andere Geraet."""
    praefix = f"{tenant_id}{_KREIS_PRAEFIX}"
    for domain, kennung in geraet.identifiers:
        if domain == DOMAIN and kennung.startswith(praefix) and kennung[len(praefix):].isdecimal():
            return int(kennung[len(praefix):])
    return None


def _name(wert: object) -> str | None:
    """Ein brauchbarer Anzeigename: Text mit Inhalt, gekuerzt; sonst None."""
    if not isinstance(wert, str) or not wert.strip():
        return None
    return wert.strip()[:GERAET_NAME_MAX]


def async_anlage(hass: HomeAssistant, entry: ConfigEntry, daten: Mapping | None) -> DeviceEntry:
    """Das Geraet der Anlage anlegen oder nachziehen. Ohne Ereignisdaten (`None`, beim Laden des Eintrags) bleibt der
    Name eines vorhandenen Geraets unveraendert; ein Name, den der Nutzer vergeben hat, bleibt immer."""
    register = dr.async_get(hass)
    kennung = anlage_kennung(entry.data["tenant_id"])
    vorhanden = register.async_get_device_by_identifier(kennung, entry.entry_id) is not None
    name = UNDEFINED if daten is None and vorhanden else (_name((daten or {}).get("anlage_name")) or PRODUKT_NAME)
    version = (daten or {}).get("addon_version")
    return register.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={kennung},
        name=name,
        manufacturer=PRODUKT_NAME,
        model=HOST_NAME,
        model_id=entry.data.get("lever_set"),
        sw_version=UNDEFINED if daten is None else (version if isinstance(version, str) and version else None),
    )


def async_kreis(hass: HomeAssistant, entry: ConfigEntry, anlage: DeviceEntry, nummer: int, name: object) -> DeviceEntry:
    """Das Geraet eines Kreises unter der Anlage anlegen oder nachziehen; ohne brauchbaren Namen gilt der Rueckfall der
    Uebersetzung (Heizkreis n)."""
    gueltig = _name(name)
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={kreis_kennung(entry.data["tenant_id"], nummer)},
        manufacturer=PRODUKT_NAME,
        via_device_id=anlage.id,
        name=gueltig or UNDEFINED,
        translation_key=None if gueltig else "kreis",
        translation_placeholders=None if gueltig else {"kreis": str(nummer)},
    )


def registrierte_kreise(hass: HomeAssistant, entry: ConfigEntry) -> dict[int, DeviceEntry]:
    """Die Kreis-Geraete dieses Eintrags im Geraete-Verzeichnis nach Nummer."""
    kreise: dict[int, DeviceEntry] = {}
    for geraet in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        if (nummer := kreis_nummer(entry.data["tenant_id"], geraet)) is not None:
            kreise[nummer] = geraet
    return kreise


def async_kreis_entfernen(hass: HomeAssistant, entry: ConfigEntry, geraet: DeviceEntry) -> None:
    """Das Kreis-Geraet vom Eintrag loesen; mit dem Geraet verschwinden seine Entities."""
    dr.async_get(hass).async_update_device(geraet.id, remove_config_entry_id=entry.entry_id)
