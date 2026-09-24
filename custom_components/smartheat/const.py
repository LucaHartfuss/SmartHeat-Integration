"""Gemeinsame Konstanten fuer die SmartHeat-Integration."""

DOMAIN = "smartheat"
DEFAULT_HEIZUNGSSERVER_BASE_URL = "https://accounts.hartfussha.org"

# Muss mit ROLE_DOMAINS aus heizungsbruecke/src/heizungsbruecke/static/wizard.js
# (jetzt entfernt) inhaltlich uebereinstimmen -- kein geteilter Code zwischen den
# Repos, siehe bestehendes Muster in heizungsbruecke/profiles.py.
ROLE_DOMAINS: dict[str, list[str]] = {
    "entity_room_actual": ["sensor"],
    "entity_room_target": ["sensor", "climate"],
    "entity_outdoor_temp": ["sensor"],
    "entity_curve_current": ["number"],
    "entity_offset_current": ["number"],
    "entity_heat_limit": ["number", "sensor"],
}

CLIMATE_ATTRIBUTE_BY_ROLE: dict[str, str] = {
    "entity_room_target": "temperature",
}

ROLE_UNIT_EXPECTATIONS: dict[str, str] = {
    "entity_room_actual": "°C",
    "entity_room_target": "°C",
    "entity_outdoor_temp": "°C",
    "entity_offset_current": "°C",
    "entity_heat_limit": "°C",
}

# Feste Rollen-Vokabular fuer optionale KPI-Mappings (Design-Spec 2026-09-24). Jedes
# Profil zeigt im Wizard nur die Teilmenge, die sein telemetry_capabilities-Objekt
# (aus /profiles) als unterstuetzt ausweist -- siehe config_flow.py::async_step_kpi_metrics.
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

# Server-seitige Profil-Werte (heizungsserver/generic/profiles.py) sind reines ASCII
# (siehe dortige hersteller/erzeuger_typ/verteilsystem-Felder) -- diese Mappings liefern
# nur die Umlaut-Anzeige im Wizard-Dropdown, der uebermittelte Wert bleibt unveraendert
# der rohe Server-Wert. Unbekannte (kuenftige) Werte fallen auf sich selbst zurueck.
ERZEUGER_TYP_LABELS: dict[str, str] = {
    "Gastherme": "Gastherme",
    "Waermepumpe": "Wärmepumpe",
}

VERTEILSYSTEM_LABELS: dict[str, str] = {
    "Heizkoerper": "Heizkörper",
    "Fussbodenheizung": "Fußbodenheizung",
}
