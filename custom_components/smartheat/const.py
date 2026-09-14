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
