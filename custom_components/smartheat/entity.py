"""Basis der SmartHeat-Entities: Entities der Anlage haengen am Geraet der Anlage, Entities eines Kreises an dessen
Geraet. Der Wert kommt aus dem Coordinator (Anlagenfelder bzw. Felder des eigenen Kreises), bis dahin gilt der zuletzt
gespeicherte Stand (RestoreEntity). Kreis-Entities entstehen mit den Kreisen, die das Status-Ereignis meldet."""
from __future__ import annotations

from collections.abc import Callable
from math import isfinite

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import entity_id, unique_id
from .coordinator import SmartHeatCoordinator
from .geraete import anlage_kennung, kreis_kennung, registrierte_kreise


def endliche_zahl(value) -> float | None:
    """Endliche Zahl (kein bool) als float, sonst None (unbekannt statt einer kaputten Anzeige). Ein JSON-int
    ausserhalb des float-Bereichs (10**400) ist nicht umwandelbar (OverflowError) und gilt wie NaN als unbekannt."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if isfinite(number) else None


class SmartHeatEntity(RestoreEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: SmartHeatCoordinator, platform: str, key: str, kreis: int | None = None) -> None:
        self.coordinator = coordinator
        self.kreis = kreis
        tenant_id = coordinator.tenant_id
        self.entity_id = entity_id(platform, key, kreis)
        self._attr_unique_id = unique_id(tenant_id, key, kreis)
        self._attr_translation_key = key
        # Name, Modell und Version des Geraets setzt der Coordinator aus dem Status-Ereignis (geraete.py).
        self._attr_device_info = DeviceInfo(
            identifiers={anlage_kennung(tenant_id) if kreis is None else kreis_kennung(tenant_id, kreis)})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._has_value():
            self._apply()
        elif (last := await self.async_get_last_state()) is not None and last.state not in (
            STATE_UNKNOWN, STATE_UNAVAILABLE,
        ):
            self._restore(last)
        self.async_on_remove(async_dispatcher_connect(self.hass, self.coordinator.signal, self._handle_update))

    @property
    def available(self) -> bool:
        """Nicht verfuegbar, solange Add-on und Integration kein gemeinsames Schema sprechen: die zuletzt gelesenen
        Werte waeren dann veraltet. Der Status-Sensor bleibt verfuegbar und nennt den Befund."""
        return self.coordinator.version_status is None

    @property
    def daten(self) -> dict | None:
        """Die Felder, die diese Entity liest: die der Anlage oder die ihres Kreises; None, solange das Ereignis sie
        nicht (mehr) meldet."""
        return self.coordinator.data if self.kreis is None else self.coordinator.kreis(self.kreis)

    @callback
    def _handle_update(self) -> None:
        if self._has_value():
            self._apply()
        self.async_write_ha_state()

    def _has_value(self) -> bool:
        return self.daten is not None

    def _apply(self) -> None:
        raise NotImplementedError

    def _restore(self, last: State) -> None:
        raise NotImplementedError


@callback
def async_kreis_entities(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
    bauen: Callable[[SmartHeatCoordinator, int], list[SmartHeatEntity]],
) -> None:
    """Die Entities je Kreis anlegen: beim Start fuer die gemeldeten und die schon registrierten Kreise (die
    registrierten stellen ihren Stand wieder her), danach bei jedem Ereignis fuer neu gemeldete. Ein Kreis bleibt
    angelegt, solange er gemeldet wird oder sein Geraet besteht; erst mit dem Geraet fallen seine Entities weg, und ein
    wiederkehrender Kreis bekommt sie neu."""
    coordinator: SmartHeatCoordinator = entry.runtime_data
    angelegt: set[int] = set()

    @callback
    def nachziehen() -> None:
        bekannt = set(coordinator.kreise) | set(registrierte_kreise(hass, entry))
        angelegt.intersection_update(bekannt)
        neu = sorted(bekannt - angelegt)
        angelegt.update(neu)
        if neu:
            async_add_entities([entity for nummer in neu for entity in bauen(coordinator, nummer)])

    nachziehen()
    entry.async_on_unload(async_dispatcher_connect(hass, coordinator.signal, nachziehen))
