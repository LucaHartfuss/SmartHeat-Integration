"""Registry-Pflege beim Laden des Eintrags: Eintraege einer Anlage aus der Zeit vor den Kreis-Entities ziehen auf die
Kennungen je Kreis um (derselbe Registry-Eintrag, damit Verlauf, Namen, Bereiche und Dashboards der Nutzer erhalten
bleiben), alles andere ohne aktuelle Entity faellt weg. Vor der ersten Aenderung liegen die eigenen Eintraege in der
Ablage der Integration (`.storage/smartheat.registry_sicherung`): Entities und Geraete im Format von
`core.entity_registry` bzw. `core.device_registry` und der Konfigurationseintrag. Fremde Eintraege und Zugangsdaten
anderer Integrationen kopiert die Integration nicht; eine Sicherung der ganzen Dateien ist Sache der Sicherung von
Home Assistant vor dem Update."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.json import json_bytes
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.json import JsonValueType, json_loads

from . import binary_sensor, geraete, number, sensor
from .const import DOMAIN, EINRICHTUNG_MAX_KREISE, ERSTER_KREIS, entity_id, slug, unique_id

_LOGGER = logging.getLogger(__name__)

SICHERUNG_KEY = f"{DOMAIN}.registry_sicherung"
SICHERUNG_VERSION = 1
SICHERUNGEN_MAX = 3  # aeltere Staende fallen weg; die Sicherung soll den letzten Stand vor dem Umzug halten


@dataclass
class _Plan:
    umziehen: dict[str, tuple[str, str | None]] = field(default_factory=dict)  # entity_id -> (neue unique_id, neue ID)
    umbenennen: dict[str, str] = field(default_factory=dict)  # entity_id -> neue ID (unique_id gleich)
    entfernen: list[str] = field(default_factory=list)

    def leer(self) -> bool:
        return not (self.umziehen or self.umbenennen or self.entfernen)


def _schluessel(entry: ConfigEntry) -> tuple[set[str], set[str]]:
    anlage = {sensor.STATUS_KEY, *(feld.key for feld in sensor.ANLAGE_FIELDS), *binary_sensor.ENTITY_KEYS}
    kreis = {*(feld.key for feld in sensor.kreis_fields(entry.data)), number.KEY}
    return anlage, kreis


def _plan(hass: HomeAssistant, entry: ConfigEntry, tenant_id: str) -> _Plan:
    anlage, kreis = _schluessel(entry)
    gewollt = {unique_id(tenant_id, key) for key in anlage} | {
        unique_id(tenant_id, key, nummer) for nummer in range(1, EINRICHTUNG_MAX_KREISE + 1) for key in kreis}
    alt_kreis = {unique_id(tenant_id, key): key for key in kreis}
    alt_anlage = {unique_id(tenant_id, key): key for key in anlage}
    registry = er.async_get(hass)
    plan = _Plan()
    belegt: set[str] = set()  # IDs, die in diesem Plan schon vergeben sind

    def frei(neue_id: str) -> bool:
        return neue_id not in belegt and not registry.async_is_registered(neue_id) \
            and hass.states.get(neue_id) is None

    for eintrag in er.async_entries_for_config_entry(registry, entry.entry_id):
        if eintrag.platform != DOMAIN:
            continue
        alte_id = f"{eintrag.domain}.smartheat_{slug(tenant_id)}_"
        if eintrag.unique_id in alt_kreis:
            key = alt_kreis[eintrag.unique_id]
            neue_unique = unique_id(tenant_id, key, ERSTER_KREIS)
            if registry.async_get_entity_id(eintrag.domain, DOMAIN, neue_unique) is not None:
                plan.entfernen.append(eintrag.entity_id)
                continue
            neue_id = entity_id(eintrag.domain, key, ERSTER_KREIS)
            # Nur die vom Add-on vergebene ID wird umbenannt; eine vom Nutzer gewaehlte bleibt.
            ziel = neue_id if eintrag.entity_id == f"{alte_id}{key}" and frei(neue_id) else None
            if ziel:
                belegt.add(ziel)
            plan.umziehen[eintrag.entity_id] = (neue_unique, ziel)
        elif eintrag.unique_id in gewollt:
            key = alt_anlage.get(eintrag.unique_id)
            if key is not None and eintrag.entity_id == f"{alte_id}{key}" and frei(neue_id := entity_id(eintrag.domain, key)):
                belegt.add(neue_id)
                plan.umbenennen[eintrag.entity_id] = neue_id
        else:
            plan.entfernen.append(eintrag.entity_id)
    return plan


def _zeilen(fragmente) -> list[JsonValueType]:
    return [json_loads(json_bytes(fragment)) for fragment in fragmente]


async def _sichern(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Die eigenen Eintraege in die Ablage schreiben, bevor die Registry sich aendert. Scheitert das Schreiben, laedt der
    Eintrag nicht (die Registry bleibt unveraendert)."""
    registry = er.async_get(hass)
    store: Store[dict] = Store(hass, SICHERUNG_VERSION, SICHERUNG_KEY, private=True)
    try:
        bisher = (await store.async_load() or {}).get("sicherungen", [])
        stand = {
            "gesichert_am": dt_util.utcnow().isoformat(),
            "config_entry": entry.as_dict(),
            "entities": _zeilen(e.as_storage_fragment for e in er.async_entries_for_config_entry(
                registry, entry.entry_id)),
            "geraete": _zeilen(g.as_storage_fragment for g in dr.async_entries_for_config_entry(
                dr.async_get(hass), entry.entry_id)),
        }
        await store.async_save({"sicherungen": [*bisher, stand][-SICHERUNGEN_MAX:]})
    except (OSError, ValueError, TypeError) as error:
        _LOGGER.error("Sicherung der Registry-Eintraege nicht geschrieben (%s), der Eintrag laedt nicht",
                      type(error).__name__)
        raise ConfigEntryNotReady("registry_sicherung") from error


async def async_pflegen(hass: HomeAssistant, entry: ConfigEntry, anlage: dr.DeviceEntry) -> None:
    """Eintraege umziehen und umbenennen, verwaiste entfernen; vorher sichern. Ein Registry-Stand ohne Aenderungsbedarf
    bleibt unberuehrt (keine Sicherung, kein Schreiben)."""
    tenant_id = entry.data["tenant_id"]
    plan = _plan(hass, entry, tenant_id)
    if plan.leer():
        return
    await _sichern(hass, entry)
    registry = er.async_get(hass)
    for alte_id, (neue_unique, neue_id) in plan.umziehen.items():
        if neue_id:
            registry.async_update_entity(alte_id, new_unique_id=neue_unique, new_entity_id=neue_id)
        else:
            registry.async_update_entity(alte_id, new_unique_id=neue_unique)
    for alte_id, neue_id in plan.umbenennen.items():
        registry.async_update_entity(alte_id, new_entity_id=neue_id)
    for alte_id in plan.entfernen:
        registry.async_remove(alte_id)
    if plan.umziehen:
        # Das Geraet des ersten Kreises besteht, damit die umgezogenen Entities sofort dort angelegt werden und ihren
        # Stand wiederherstellen; faellt der Kreis aus der Meldung, raeumt ihn die uebliche Regel weg.
        geraete.async_kreis(hass, entry, anlage, ERSTER_KREIS, None)
    _LOGGER.info("Registry-Eintraege umgezogen: %d, umbenannt: %d, entfernt: %d",
                 len(plan.umziehen), len(plan.umbenennen), len(plan.entfernen))
