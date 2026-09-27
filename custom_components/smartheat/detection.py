"""Erkennungs-Engine des Wizards (Spec TP6 2.2): reine Funktionen ueber Registry- und
State-Snapshots, ohne Config-Flow-Abhaengigkeit. Die Suchregeln sind dieselben wie im
Server-Test tests/generic/test_catalog_registry_fixture.py (TP3-Spec 3.1). Nicht genau ein
Treffer heisst: keine Vorbelegung."""
from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .catalog import REQUIRED_CIRCUIT_ROLES, IntegrationDescriptor
from .const import TEMPERATURE_UNIT, WEATHER_TEMPERATURE_ATTRIBUTE, WEATHER_UNIT_ATTRIBUTE

_LOGGER = logging.getLogger(__name__)
PREFERRED_WEATHER = "weather.forecast_home"


@dataclass(frozen=True)
class RegistryEntry:
    entity_id: str
    unique_id: str | None
    platform: str
    original_name: str | None
    config_entry_id: str | None
    device_id: str | None
    device_class: str | None


@dataclass(frozen=True)
class DeviceInfo:
    id: str
    name: str | None
    model: str | None
    config_entries: tuple[str, ...]


@dataclass(frozen=True)
class WeatherCandidate:
    entity_id: str
    temperature: object
    unit: str | None


@dataclass(frozen=True)
class Circuit:
    config_entry_id: str | None
    system_key: str
    circuit: str
    label: str
    roles: dict[str, str]

    @property
    def key(self) -> str:
        return f"{self.config_entry_id}|{self.system_key}|{self.circuit}"


def _domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


def _normalize(unique_id: str, domain: str) -> str:
    """unique_id ohne fuehrende Plattform-Domain und ein Trennzeichen ("mypyllant " bei
    Number-, "mypyllant_" bei Sensor-Entities)."""
    if unique_id.startswith(domain) and len(unique_id) > len(domain):
        return unique_id[len(domain) + 1:]
    return unique_id


def installed_integrations(descriptors: list[IntegrationDescriptor], entry_domains: set[str]) -> list[IntegrationDescriptor]:
    return [descriptor for descriptor in descriptors if descriptor.domain in entry_domains]


def find_circuits(descriptor: IntegrationDescriptor, entries: list[RegistryEntry], devices: list[DeviceInfo]) -> list[Circuit]:
    """Kandidat = (Config-Entry, Anlagen-Kennung, Kreisnummer), bei dem jede Pflichtrolle genau
    einmal gefunden wurde. Die Anlagen-Kennung ist der unique_id-Teil vor dem Suffix."""
    groups: dict[tuple, dict[str, list[str]]] = {}
    curve_devices: dict[tuple, str | None] = {}
    for entry in entries:
        if entry.platform != descriptor.domain or not entry.unique_id:
            continue
        for role, matcher in descriptor.circuit_roles.items():
            if _domain_of(entry.entity_id) != matcher.entity_domain:
                continue
            match = matcher.uid_pattern().search(entry.unique_id)
            if match is None:
                continue
            key = (entry.config_entry_id, _normalize(entry.unique_id[:match.start()], descriptor.domain), match.group(1))
            groups.setdefault(key, {}).setdefault(role, []).append(entry.entity_id)
            if role == "curve_current":
                curve_devices[key] = entry.device_id
    devices_by_id = {device.id: device for device in devices}
    circuits = []
    for key in sorted(groups, key=lambda k: (str(k[0]), k[1], int(k[2]))):
        roles = groups[key]
        if any(len(roles.get(role, [])) != 1 for role in REQUIRED_CIRCUIT_ROLES):
            _LOGGER.info("Heizkreis %s unvollstaendig oder mehrdeutig, nicht angeboten: %s", key, roles)
            continue
        device = devices_by_id.get(curve_devices.get(key))
        label = device.name if device is not None and device.name else f"Heizkreis {key[2]}"
        found = {role: ids[0] for role, ids in roles.items() if len(ids) == 1}
        circuits.append(Circuit(key[0], key[1], key[2], label, found))
    # Gleiche Beschriftung (z.B. alle Kreise an einem Anlagen-Geraet): erst Kreisnummer, dann
    # Anlagen-Kennung anhaengen.
    counts = Counter(circuit.label for circuit in circuits)
    circuits = [replace(c, label=f"{c.label} · Kreis {c.circuit}") if counts[c.label] > 1 else c for c in circuits]
    counts = Counter(circuit.label for circuit in circuits)
    return [replace(c, label=f"{c.label} ({c.system_key})") if counts[c.label] > 1 else c for c in circuits]


def system_role_suggestions(descriptor: IntegrationDescriptor, circuit: Circuit, entries: list[RegistryEntry]) -> dict[str, str]:
    prefix = f"{circuit.system_key}_"
    result = {}
    for role, matcher in descriptor.system_roles.items():
        hits = []
        for entry in entries:
            if entry.platform != descriptor.domain or entry.config_entry_id != circuit.config_entry_id:
                continue
            if _domain_of(entry.entity_id) != matcher.entity_domain:
                continue
            unique_id = entry.unique_id or ""
            if not _normalize(unique_id, descriptor.domain).startswith(prefix):
                continue
            if matcher.unique_id_suffix is not None:
                if not unique_id.endswith(matcher.unique_id_suffix):
                    continue
            elif not (entry.original_name or "").lower().endswith(matcher.original_name_suffix.lower()):
                continue
            hits.append(entry.entity_id)
        if len(hits) == 1:
            result[role] = hits[0]
    return result


def suggest_erzeuger_typ(descriptor: IntegrationDescriptor, config_entry_ids: set, devices: list[DeviceInfo]) -> str | None:
    found = set()
    for device in devices:
        if not device.model or not set(device.config_entries) & config_entry_ids:
            continue
        for hint in descriptor.erzeuger_typ_hints:
            if hint.model_contains.lower() in device.model.lower():
                found.add(hint.erzeuger_typ)
    return found.pop() if len(found) == 1 else None


def weather_fallback(candidates: list[WeatherCandidate]) -> str | None:
    valid = sorted(
        candidate.entity_id for candidate in candidates
        if isinstance(candidate.temperature, (int, float)) and not isinstance(candidate.temperature, bool)
        and candidate.unit == TEMPERATURE_UNIT
    )
    if PREFERRED_WEATHER in valid:
        return PREFERRED_WEATHER
    return valid[0] if valid else None


def battery_entities(selected_entity_ids: list[str], entries: list[RegistryEntry], units: dict[str, str | None]) -> list[str]:
    """Je Geraet der gewaehlten Raumfuehler/Sollwert-Entities eine Batterie-Entity: sensor in %
    bevorzugt, sonst binary_sensor. Entity ohne Geraet -> keine Batterie."""
    by_id = {entry.entity_id: entry for entry in entries}
    result: list[str] = []
    for entity_id in selected_entity_ids:
        entry = by_id.get(entity_id)
        if entry is None or entry.device_id is None:
            continue
        candidates = [e for e in entries if e.device_id == entry.device_id and e.device_class == "battery"]
        sensors = sorted(e.entity_id for e in candidates if _domain_of(e.entity_id) == "sensor" and units.get(e.entity_id) == "%")
        binaries = sorted(e.entity_id for e in candidates if _domain_of(e.entity_id) == "binary_sensor")
        for chosen in (sensors or binaries)[:1]:
            if chosen not in result:
                result.append(chosen)
    return result


def mobile_app_services(service_names: Iterable[str]) -> list[str]:
    return sorted(f"notify.{name}" for name in service_names if name.startswith("mobile_app_"))


# --- Adapter auf Home Assistant (nur hier wird hass gelesen) ---

def registry_snapshot(hass: HomeAssistant) -> tuple[list[RegistryEntry], list[DeviceInfo]]:
    entries = [
        RegistryEntry(
            entry.entity_id, entry.unique_id, entry.platform, entry.original_name,
            entry.config_entry_id, entry.device_id, entry.device_class or entry.original_device_class,
        )
        for entry in er.async_get(hass).entities.values()
    ]
    devices = [
        DeviceInfo(device.id, device.name_by_user or device.name, device.model, tuple(device.config_entries))
        for device in dr.async_get(hass).devices.values()
    ]
    return entries, devices


def weather_candidates(hass: HomeAssistant) -> list[WeatherCandidate]:
    return [
        WeatherCandidate(
            state.entity_id, state.attributes.get(WEATHER_TEMPERATURE_ATTRIBUTE), state.attributes.get(WEATHER_UNIT_ATTRIBUTE),
        )
        for state in hass.states.async_all("weather")
    ]


def unit_map(hass: HomeAssistant, entity_ids: Iterable[str]) -> dict[str, str | None]:
    result = {}
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        result[entity_id] = state.attributes.get("unit_of_measurement") if state is not None else None
    return result
