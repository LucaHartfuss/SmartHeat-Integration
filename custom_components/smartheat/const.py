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

HEIZUNGSBRUECKE_ADDON_SLUG = "heizungsbruecke"
CLOUDFLARED_ADDON_SLUG = "cloudflared_access_mqtt"
