"""Notbetrieb als Problem-Sensor (Spec TP7 1.2)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import SmartHeatEntity

NOTBETRIEB_KEY = "notbetrieb"
ENTITY_KEYS = (NOTBETRIEB_KEY,)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([NotbetriebSensor(entry.runtime_data)])


class NotbetriebSensor(SmartHeatEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "binary_sensor", NOTBETRIEB_KEY)

    def _apply(self) -> None:
        # Fehlt `notbetrieb` oder ist es kein bool (aelteres/fremdes Event), unbekannt statt eines
        # KeyError im Update-Callback bzw. eines falschen "an" fuer den Text "false".
        value = (self.coordinator.data or {}).get("notbetrieb")
        self._attr_is_on = value if isinstance(value, bool) else None

    def _restore(self, last: State) -> None:
        self._attr_is_on = last.state == STATE_ON
