"""Binary-Sensoren der Anlage: Notbetrieb als Problem-Sensor, Verbindung zum Server als Diagnose."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import SmartHeatEntity

NOTBETRIEB_KEY = "notbetrieb"
SERVER_VERBUNDEN_KEY = "server_verbunden"
ENTITY_KEYS = (NOTBETRIEB_KEY, SERVER_VERBUNDEN_KEY)


# Push ueber den Coordinator, kein Abruf je Entity: keine Begrenzung gleichzeitiger Aufrufe.
PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities([NotbetriebSensor(coordinator), ServerVerbundenSensor(coordinator)])


class _FeldSensor(SmartHeatEntity, BinarySensorEntity):
    """Ein Anlagenfeld des Status-Ereignisses als Ein/Aus."""
    _feld: str

    def _apply(self) -> None:
        # Fehlt das Feld oder ist es kein bool (aelteres/fremdes Event), unbekannt statt eines KeyError im
        # Update-Callback bzw. eines falschen "an" fuer den Text "false".
        value = (self.daten or {}).get(self._feld)
        self._attr_is_on = value if isinstance(value, bool) else None

    def _restore(self, last: State) -> None:
        self._attr_is_on = last.state == STATE_ON


class NotbetriebSensor(_FeldSensor):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _feld = "notbetrieb"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "binary_sensor", NOTBETRIEB_KEY)


class ServerVerbundenSensor(_FeldSensor):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _feld = "server_verbunden"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "binary_sensor", SERVER_VERBUNDEN_KEY)
