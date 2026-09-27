"""Gemeinsame Konstanten fuer die SmartHeat-Integration."""
import re

DOMAIN = "smartheat"
DEFAULT_HEIZUNGSSERVER_BASE_URL = "https://accounts.hartfussha.org"

# Add-on-Optionen, die der Wizard 2.0 als Listen bzw. Laufkennung schreibt (Spec TP6 3.1).
OPTION_ROOM_SENSORS = "room_sensors"
OPTION_NOTIFY_SERVICES = "notify_services"
OPTION_BATTERY_ENTITIES = "battery_entities"
OPTION_SETUP_ID = "setup_id"
LIST_OPTIONS = (OPTION_ROOM_SENSORS, OPTION_NOTIFY_SERVICES, OPTION_BATTERY_ENTITIES)
# Vom Wizard nicht verwaltet: bleiben beim erneuten Einrichten aus den bestehenden Optionen
# erhalten (I3). Alles andere setzt der Wizard vollstaendig neu.
UNMANAGED_ADDON_OPTIONS = ("local_check_interval_seconds", "telemetry_interval_seconds")

ROOM_SENSOR_DOMAINS = ["sensor", "climate"]
# climate/weather liefern die Temperatur als Attribut; das Add-on liest `entity::attribut`.
CLIMATE_ATTRIBUTE_ROOM_SENSOR = "current_temperature"
CLIMATE_ATTRIBUTE_ROOM_TARGET = "temperature"
WEATHER_TEMPERATURE_ATTRIBUTE = "temperature"
WEATHER_UNIT_ATTRIBUTE = "temperature_unit"
TEMPERATURE_UNIT = "°C"

# Physikalische Grenzen (Spec TP6 4 und 8): gleich Server-R4 (generic/messages.py::
# PLAUSIBLE_RANGES) und Add-on (plausibility.py); tools/contract_check.py prueft das.
PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    "room": (5.0, 35.0),
    "outdoor": (-40.0, 45.0),
    "heat_limit": (5.0, 25.0),
}
STALE_AFTER_HOURS = 6
ROOM_SENSOR_DEVIATION_K = 3.0

# Status-Entity des Add-ons (Spec TP6 3.6), gleiche Regel wie heizungsbruecke/status.py.
STATUS_STARTET = "startet"
STATUS_BEREIT = "bereit"
STATUS_KONFIGURATIONSFEHLER = "konfigurationsfehler"
STATUS_WAIT_SECONDS = 180
STATUS_POLL_SECONDS = 2
# Namen der Status-Entity-Attribute (das Add-on definiert dieselben Konstanten in
# status.py; tools/contract_check.py vergleicht sie).
STATUS_ATTR_SETUP_ID = "setup_id"
STATUS_ATTR_GRUND = "grund"


def status_entity_id(tenant_id: str) -> str:
    return f"sensor.smartheat_{re.sub(r'[^a-z0-9_]', '_', tenant_id.lower())}_status"

# Einzel-Entity-Rollen, die als entity_<rolle> ins Add-on gehen (ohne KPI-Rollen). Die
# Raumfuehler gehen als Liste room_sensors (Spec TP6 3.1). Kein geteilter Code zwischen den
# Repos; Cross-Repo-Gleichheit prueft tools/contract_check.py im Dev-Root.
ROLE_DOMAINS: dict[str, list[str]] = {
    "entity_room_target": ["sensor", "climate"],
    "entity_outdoor_temp": ["sensor", "weather"],
    "entity_curve_current": ["number"],
    "entity_offset_current": ["number"],
    "entity_heat_limit": ["number", "sensor"],
}

# Feste Rollen-Vokabular fuer optionale KPI-Mappings (Design-Spec 2026-09-24). Jedes
# Profil zeigt im Wizard nur die Teilmenge, die sein telemetry_capabilities-Objekt
# (aus /catalog) als unterstuetzt ausweist -- siehe config_flow.py::async_step_plant_values
# (Sektion advanced).
KPI_SCALAR_ROLE_BY_CAPABILITY: dict[str, str] = {
    "has_flow_temperature": "entity_flow_temperature",
    "has_return_temperature": "entity_return_temperature",
    "has_operating_mode": "entity_operating_mode",
    "has_water_pressure": "entity_system_water_pressure",
    "has_manufacturer_efficiency_sensor": "entity_efficiency_ratio",
}

# state_class, das HA fuer den jeweiligen Feldtyp erwartet -- operating_mode hat
# bewusst keinen Eintrag (Text-Sensor ohne state_class). Energie-Rollen
# (entity_energy_<channel>) erwarten total_increasing, siehe config_flow.py.
KPI_ROLE_STATE_CLASS_EXPECTATIONS: dict[str, str] = {
    "entity_flow_temperature": "measurement",
    "entity_return_temperature": "measurement",
    "entity_system_water_pressure": "measurement",
    "entity_efficiency_ratio": "measurement",
}


# Erlaubte Energie-Kanaele -- muss mit den entity_energy_*-Optionen in der config.yaml
# des heizungsbruecke-Add-ons uebereinstimmen (Duplikation pro Repo ist das Projektmuster).
KPI_ENERGY_CHANNELS: tuple[str, ...] = (
    "electrical_heating", "electrical_dhw", "primary_heating",
    "primary_dhw", "thermal_heating", "thermal_dhw",
)


def kpi_energy_role(channel: str) -> str:
    return f"entity_energy_{channel}"


# Beide Add-ons kommen aus diesem einen Custom-Repository. Ein Supervisor praefigiert den
# Slug eines von einem Custom-Repository installierten Add-ons mit einem Repository-Hash
# (verifiziert gegen einen echten Supervisor, z.B. "f5f6325b_heizungsbruecke" statt nur
# "heizungsbruecke" -- siehe Task 14 in .superpowers/sdd/2026-09-14-smartheat-config-
# integration/progress.md). Die beiden Konstanten unten sind deshalb NICHT der tatsaechliche
# API-Slug, sondern nur der bare config.yaml-Slug -- supervisor_client.py loest daraus zur
# Laufzeit per ADDON_REPOSITORY_URL + Slug-Suffix den echten, installationsspezifischen Slug auf.
ADDON_REPOSITORY_URL = "https://github.com/LucaHartfuss/SmartHeat-for-HomeAssistant"
HEIZUNGSBRUECKE_ADDON_SLUG = "heizungsbruecke"
CLOUDFLARED_ADDON_SLUG = "cloudflared_access_mqtt"

# Mindestversionen der Add-ons fuer diesen Wizard (Spec TP6 1, Schritt 0; I4).
MIN_ADDON_VERSIONS: dict[str, str] = {
    HEIZUNGSBRUECKE_ADDON_SLUG: "0.19.0",
    CLOUDFLARED_ADDON_SLUG: "1.0.0",
}
