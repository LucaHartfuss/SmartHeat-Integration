"""Pruefungen des Wizards ueber hass.states (Spec TP6 4): harte Sperren am Schritt und Warnungen
fuer die Zusammenfassung. Eine Referenz ist `entity_id` oder `entity_id::attribut` (Add-on-
Konvention); `weather.x` ohne Suffix meint das Attribut `temperature`."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant

from .const import (
    CLIMATE_ATTRIBUTE_ROOM_SENSOR, CLIMATE_ATTRIBUTE_ROOM_TARGET, OPTION_ENTITY_ROOM_TARGET, OPTION_ROOM_SENSORS,
    PLAUSIBLE_RANGES, ROOM_SENSOR_DEVIATION_K, STALE_AFTER_HOURS, TEMPERATURE_UNIT, WEATHER_TEMPERATURE_ATTRIBUTE,
    WEATHER_UNIT_ATTRIBUTE,
)

ERROR_NOT_FOUND = "entity_not_found"
ERROR_UNAVAILABLE = "entity_unavailable"
ERROR_NOT_NUMERIC = "not_numeric"
ERROR_UNIT = "unit_mismatch"
ERROR_RANGE = "out_of_range"
ERROR_DUPLICATE = "duplicate_entity"


def entity_of(ref: str) -> str:
    return ref.partition("::")[0]


def _domain(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


def room_sensor_ref(entity_id: str) -> str:
    return f"{entity_id}::{CLIMATE_ATTRIBUTE_ROOM_SENSOR}" if _domain(entity_id) == "climate" else entity_id


def room_target_ref(entity_id: str) -> str:
    return f"{entity_id}::{CLIMATE_ATTRIBUTE_ROOM_TARGET}" if _domain(entity_id) == "climate" else entity_id


def read_value(hass: HomeAssistant, ref: str) -> tuple[float | None, str | None]:
    entity_id, _, attribute = ref.partition("::")
    state = hass.states.get(entity_id)
    if state is None:
        return None, ERROR_NOT_FOUND
    if state.state in ("unavailable", "unknown"):
        return None, ERROR_UNAVAILABLE
    if not attribute and _domain(entity_id) == "weather":
        attribute = WEATHER_TEMPERATURE_ATTRIBUTE
    raw = state.attributes.get(attribute) if attribute else state.state
    if isinstance(raw, bool):
        return None, ERROR_NOT_NUMERIC
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, ERROR_NOT_NUMERIC
    if not math.isfinite(value):
        return None, ERROR_NOT_NUMERIC
    return value, None


def _unit_ok(hass: HomeAssistant, ref: str) -> bool:
    entity_id = entity_of(ref)
    state = hass.states.get(entity_id)
    domain = _domain(entity_id)
    if domain == "weather":
        return state.attributes.get(WEATHER_UNIT_ATTRIBUTE) == TEMPERATURE_UNIT
    if domain == "climate":
        # climate-Attribute stehen in der Systemeinheit von HA.
        return hass.config.units.temperature_unit == TEMPERATURE_UNIT
    return state.attributes.get("unit_of_measurement") == TEMPERATURE_UNIT


def check_temperature(hass: HomeAssistant, ref: str, bounds: tuple[float, float] | None = None) -> str | None:
    value, error = read_value(hass, ref)
    if error:
        return error
    if not _unit_ok(hass, ref):
        return ERROR_UNIT
    if bounds is not None and not bounds[0] <= value <= bounds[1]:
        return ERROR_RANGE
    return None


def check_numeric(hass: HomeAssistant, ref: str) -> str | None:
    return read_value(hass, ref)[1]


def duplicate_fields(refs_by_field: dict[str, list[str]]) -> dict[str, str]:
    """Dieselbe aufgeloeste Referenz in zwei Rollen (oder doppelt in einer) markiert beide Felder.
    Der Aufrufer zeigt nur die Felder seines Formulars an."""
    seen: dict[str, str] = {}
    errors: dict[str, str] = {}
    for field, refs in refs_by_field.items():
        for ref in refs:
            if ref in seen:
                errors[field] = ERROR_DUPLICATE
                errors.setdefault(seen[ref], ERROR_DUPLICATE)
            else:
                seen[ref] = field
    return errors


def stale_entities(hass: HomeAssistant, refs: list[str], now: datetime) -> list[str]:
    limit = now - timedelta(hours=STALE_AFTER_HOURS)
    result = []
    for entity_id in dict.fromkeys(entity_of(ref) for ref in refs):
        state = hass.states.get(entity_id)
        if state is None:
            continue
        if max(state.last_reported, state.last_updated) < limit:
            result.append(entity_id)
    return result


def deviating_room_sensors(values: dict[str, float]) -> list[str]:
    """Raumfuehler, die um mehr als ROOM_SENSOR_DEVIATION_K vom Mittel der uebrigen abweichen."""
    if len(values) < 2:
        return []
    result = []
    for ref, value in values.items():
        others = [other for key, other in values.items() if key != ref]
        if abs(value - sum(others) / len(others)) > ROOM_SENSOR_DEVIATION_K:
            result.append(entity_of(ref))
    return result


def check_rooms(hass: HomeAssistant, room_sensor_entities: list[str], target_entity: str) -> tuple[list[str], str, dict[str, str]]:
    """Harte Pruefungen der Raumauswahl (Wizard-Schritt rooms und Optionen): Raumfuehler-Refs,
    Soll-Ref und Feldfehler."""
    refs = [room_sensor_ref(entity) for entity in room_sensor_entities]
    target = room_target_ref(target_entity)
    errors: dict[str, str] = {}
    if not refs:
        errors[OPTION_ROOM_SENSORS] = "room_sensors_required"
    for ref in refs:
        error = check_temperature(hass, ref, PLAUSIBLE_RANGES["room"])
        if error:
            errors[OPTION_ROOM_SENSORS] = error
            break
    error = check_temperature(hass, target, PLAUSIBLE_RANGES["room"])
    if error:
        errors[OPTION_ENTITY_ROOM_TARGET] = error
    if not errors:
        errors = duplicate_fields({OPTION_ROOM_SENSORS: refs, OPTION_ENTITY_ROOM_TARGET: [target]})
    return refs, target, errors
