"""Wunschtemperatur als Entity (Spec 5c 1.3/2.2): fuer HA-Automationen und das Dashboard. Setzen feuert nur das lokale
Ereignis smartheat_wunsch (mit dem Kontext des Dienstaufrufs: Nutzer oder Automation); das Add-on wendet den Wert
sofort lokal an (Boost bei Erhoehung, auch ohne Internet) und meldet ihn als Bedienwunsch an den Server. Kein
Server-Aufruf aus der Integration. Angezeigt wird der wirksame Soll aus dem Status-Ereignis."""
from __future__ import annotations

from math import isfinite

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import RAUM_SOLL_MAX, RAUM_SOLL_MIN, RAUM_SOLL_STEP, WUNSCH_EVENT
from .entity import SmartHeatEntity

KEY = "wunschtemperatur"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([WunschtemperaturEntity(entry.runtime_data)])


def _temperature(value) -> float | None:
    """Endliche Zahl (kein bool) als float, sonst None (unbekannt statt einer kaputten Anzeige)."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not isfinite(value):
        return None
    return float(value)


class WunschtemperaturEntity(SmartHeatEntity, NumberEntity):
    _attr_native_min_value = RAUM_SOLL_MIN
    _attr_native_max_value = RAUM_SOLL_MAX
    _attr_native_step = RAUM_SOLL_STEP
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "number", KEY)

    def _apply(self) -> None:
        self._attr_native_value = _temperature((self.coordinator.data or {}).get("raum_soll"))

    def _restore(self, last: State) -> None:
        try:
            self._attr_native_value = _temperature(float(last.state))
        except ValueError:
            self._attr_native_value = None

    async def async_set_native_value(self, value: float) -> None:
        self.hass.bus.async_fire(WUNSCH_EVENT, {"raum_soll": float(value)}, context=self._context)
        self._attr_native_value = float(value)
        self.async_write_ha_state()
