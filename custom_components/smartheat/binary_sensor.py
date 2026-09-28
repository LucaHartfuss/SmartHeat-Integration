"""Notbetrieb als Problem-Sensor (Spec TP7 1.2)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import SmartHeatEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([NotbetriebSensor(entry.runtime_data)])


class NotbetriebSensor(SmartHeatEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "binary_sensor", "notbetrieb")

    def _apply(self) -> None:
        self._attr_is_on = bool(self.coordinator.data["notbetrieb"])

    def _restore(self, last: State) -> None:
        self._attr_is_on = last.state == STATE_ON
