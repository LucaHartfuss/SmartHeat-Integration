"""Sensoren der SmartHeat-Integration (Spec TP7 1.2)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .const import ABO_VALUES, BOOST_VALUES, DATENFEHLER_ARTEN, DATENFEHLER_KEINER, HINT_FIELDS, STATUS_SENSOR_VALUES
from .entity import SmartHeatEntity


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fault(data: dict) -> dict:
    return data.get("datenfehler") or {}


@dataclass(frozen=True, kw_only=True)
class EventField:
    key: str
    # Alle Lambdas in FIELDS unten liefern nur str/float/None (JSON-Skalare) oder datetime|None
    # (letzte_serverantwort) -- object waere zu weit gefasst fuer _attr_native_value (StateType |
    # date | datetime | Decimal).
    value: Callable[[dict], StateType | datetime]
    attributes: Callable[[dict], dict] = lambda data: {}
    attribute_keys: tuple[str, ...] = ()
    restore: Callable[[str], StateType | datetime] = lambda state: state
    device_class: SensorDeviceClass | None = None
    options: list[str] | None = field(default=None)
    unit: str | None = None
    category: EntityCategory | None = None


FIELDS = (
    EventField(
        key="datenfehler", value=lambda d: _fault(d).get("art", DATENFEHLER_KEINER),
        attributes=lambda d: {"rollen": _fault(d).get("rollen", [])}, attribute_keys=("rollen",),
        device_class=SensorDeviceClass.ENUM, options=[DATENFEHLER_KEINER, *DATENFEHLER_ARTEN],
    ),
    EventField(key="boost", value=lambda d: d["boost"], device_class=SensorDeviceClass.ENUM, options=list(BOOST_VALUES)),
    EventField(
        key="letzte_serverantwort",
        value=lambda d: dt_util.parse_datetime(d["letzte_serverantwort"]) if d["letzte_serverantwort"] else None,
        restore=dt_util.parse_datetime, device_class=SensorDeviceClass.TIMESTAMP,
    ),
    EventField(key="heizkurve", value=lambda d: d["kurve"], restore=_number),
    EventField(
        key="offset", value=lambda d: d["offset"], restore=_number,
        device_class=SensorDeviceClass.TEMPERATURE, unit=UnitOfTemperature.CELSIUS,
    ),
    EventField(
        key="abo", value=lambda d: d["abo"], attributes=lambda d: {"frist_ende": d["abo_frist_ende"]},
        attribute_keys=("frist_ende",), device_class=SensorDeviceClass.ENUM, options=list(ABO_VALUES),
    ),
    EventField(key="addon_version", value=lambda d: d["addon_version"], category=EntityCategory.DIAGNOSTIC),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities([StatusSensor(coordinator), *(EventSensor(coordinator, event_field) for event_field in FIELDS)])


class StatusSensor(SmartHeatEntity, SensorEntity):
    """Gesamtzustand: Waechter-Befund oder Status des Add-ons; Hinweise als Attribute (sichtbar
    auch bei abgeschaltetem Push)."""
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(STATUS_SENSOR_VALUES)
    _ATTRIBUTES = ("grund", *HINT_FIELDS)

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "sensor", "status")

    def _has_value(self) -> bool:
        return self.coordinator.status is not None

    def _apply(self) -> None:
        hints = (self.coordinator.data or {}).get("hinweise")
        if not isinstance(hints, dict):
            hints = {}  # kein Objekt (aelteres/fremdes Event): Hinweise leer statt Absturz (Final-Review M4)
        self._attr_native_value = self.coordinator.status
        self._attr_extra_state_attributes = {
            "grund": self.coordinator.reason, **{hint: hints.get(hint) for hint in HINT_FIELDS},
        }

    def _restore(self, last: State) -> None:
        if last.state in STATUS_SENSOR_VALUES:
            self._attr_native_value = last.state
        self._attr_extra_state_attributes = {key: last.attributes.get(key) for key in self._ATTRIBUTES}


class EventSensor(SmartHeatEntity, SensorEntity):
    # Wie HAs eigenes _attr_native_value (sensor/__init__.py): Default None, hier zusaetzlich
    # explizit optional, da _apply() bei fehlenden Attributen bewusst None zuweist (M2).
    _attr_extra_state_attributes: dict[str, Any] | None = None

    def __init__(self, coordinator, event_field: EventField) -> None:
        super().__init__(coordinator, "sensor", event_field.key)
        self._field = event_field
        self._attr_device_class = event_field.device_class
        self._attr_options = event_field.options
        self._attr_native_unit_of_measurement = event_field.unit
        self._attr_entity_category = event_field.category

    def _apply(self) -> None:
        data = self.coordinator.data
        # _apply() laeuft laut SmartHeatEntity nur, wenn _has_value() True ist, d.h.
        # self.coordinator.data ist nicht None (siehe entity.py).
        assert data is not None
        self._attr_native_value = self._safe_value(data)
        try:
            attributes = self._field.attributes(data) or None
        except (KeyError, TypeError, ValueError, AttributeError):
            # Ein fehlendes Feld in den Attributen (z. B. bei einem aelteren/fremden Event) soll
            # nicht den Entity-Update-Callback crashen (M2).
            attributes = None
        self._attr_extra_state_attributes = attributes

    def _safe_value(self, data: dict):
        """Ein fehlender Schluessel, ein Wert ausserhalb der Enum-Optionen, ein naiver Zeitstempel
        oder ein nicht-numerischer/nicht endlicher Wert eines numerischen Feldes (offset hat dafuer
        die Device-Class TEMPERATURE, die HA beim Schreiben validiert; heizkurve hat keine, wird
        aus Konsistenz aber genauso behandelt -- Fix-Runde 2, Fund 2) wuerden HA beim Schreiben des
        States mit einer ValueError abbrechen lassen bzw. eine kaputte Zahl anzeigen; auf None
        abbilden statt die Entity haengen zu lassen (M2), analog zu `_restore`."""
        try:
            value = self._field.value(data)
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
        if self._field.options is not None and value not in self._field.options:
            return None
        if self._field.device_class == SensorDeviceClass.TIMESTAMP and isinstance(value, datetime) and value.tzinfo is None:
            return None
        if self._field.restore is _number:
            # Numerische Felder (heizkurve, offset): _number liefert None fuer nicht-numerische
            # Werte; NaN/Inf sind fuer float() gueltig, aber fuer HA-Sensoren nicht (isfinite).
            number = _number(value)
            return number if number is not None and isfinite(number) else None
        return value

    def _restore(self, last: State) -> None:
        value = self._field.restore(last.state)
        if self._field.options is not None and value not in self._field.options:
            return  # z. B. ein Wert einer aelteren Version: nicht in die Enum-Entity
        self._attr_native_value = value
        if self._field.attribute_keys:
            self._attr_extra_state_attributes = {key: last.attributes.get(key) for key in self._field.attribute_keys}
