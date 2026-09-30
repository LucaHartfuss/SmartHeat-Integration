"""Gemeinsame Konstanten fuer die SmartHeat-Integration."""
import re
from collections.abc import Mapping

DOMAIN = "smartheat"
DEFAULT_HEIZUNGSSERVER_BASE_URL = "https://accounts.hartfussha.org"

# Add-on-Optionen, die der Wizard 2.0 als Listen bzw. Laufkennung schreibt (Spec TP6 3.1).
OPTION_ROOM_SENSORS = "room_sensors"
OPTION_NOTIFY_SERVICES = "notify_services"
OPTION_BATTERY_ENTITIES = "battery_entities"
OPTION_SETUP_ID = "setup_id"
OPTION_ENTITY_ROOM_TARGET = "entity_room_target"
OPTION_ABGEMELDET = "abgemeldet"
OPTION_NOTIFY_HINTS_OFF = "notify_hints_off"
# Abschaltbare Hinweis-Kategorien; gleich in heizungsbruecke/notifier.py und config.yaml (Contract-Check 15).
HINT_CATEGORIES = ("raumfuehler", "batterie", "manueller_eingriff", "quellwechsel")
# Markiert einen unvollstaendigen Eintrag; heute setzt ihn kein Code mehr (die v1-Migration ist
# entfallen), er bleibt als Merkmal fuer entry_incomplete().
DATA_INCOMPLETE = "unvollstaendig"
BRIDGE_CREDENTIAL_OPTIONS = ("mqtt_username", "mqtt_password")
CLOUDFLARED_CREDENTIAL_OPTIONS = ("service_token_id", "service_token_secret")
# Profilwechsel ohne neue Zugangsdaten; Route im Server: accounts_api.py (Contract-Check 17).
PROFILE_PATH = "/tenants/{tenant_id}/profile"
INSTALLATION_PATH = "/tenants/{tenant_id}/installation"
# Add-on-Option der Heizungsbruecke mit der Basis-URL der accounts-api (Spec TP3 S3); das
# Entfernen widerruft damit dort, wo auch das Add-on fragt (Spec TP8 4).
OPTION_ACCOUNTS_API_BASE_URL = "accounts_api_base_url"
REVOKE_TIMEOUT_SECONDS = 10
# Zeitlimit je Anfrage an die accounts-api; laenger gilt der Dienst als nicht erreichbar (AU-036).
REQUEST_TIMEOUT_SECONDS = 30
# mypyllant-Pollintervall, auf das OWN_WRITE_SETTLE_SECONDS (2100 s) im Add-on abgestimmt ist (client1:
# 30 min, 5 min Reserve). Laenger -> Warnung im Wizard (TP12c, AU-017); das Add-on bleibt fest.
POLL_INTERVAL_MAX_SECONDS = 1800
# Katalog-Version, die diese Integration voraussetzt (TP12c: circuit_in_name, poll_interval_option).
# Ein aelterer Server-Katalog bricht den Wizard ab (catalog_outdated), statt still auf die alte
# Zonensuche zurueckzufallen.
REQUIRED_CATALOG_VERSION = 2
# Laenge des vom Server gemeldeten Ablehnungsgrunds (403), der dem Nutzer angezeigt wird.
ACCESS_DENIED_REASON_MAX = 200
LIST_OPTIONS = (OPTION_ROOM_SENSORS, OPTION_NOTIFY_SERVICES, OPTION_BATTERY_ENTITIES)
# Vom Wizard nicht verwaltet: bleiben beim erneuten Einrichten aus den bestehenden Optionen
# erhalten (I3). Alles andere setzt der Wizard vollstaendig neu.
UNMANAGED_ADDON_OPTIONS = ("local_check_interval_seconds", "telemetry_interval_seconds")

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

# Status-Kanal (Spec TP7 1.1): HA-Event des Add-ons. Name, schema, Felder und Wertemengen muessen
# zu heizungsbruecke/status.py passen (tools/contract_check.py, Pruefung 14).
STATUS_EVENT = "smartheat_status"
STATUS_EVENT_SCHEMA = 1
STATUS_EVENT_FIELDS = (
    "schema", "tenant_id", "setup_id", "addon_version", "status", "grund", "notbetrieb", "datenfehler",
    "boost", "letzte_serverantwort", "kurve", "parallelverschiebung", "mindestvorlauf", "abo",
    "abo_frist_ende", "hinweise",
)
STATUS_STARTET = "startet"
STATUS_REGELT = "regelt"
STATUS_KONFIGURATIONSFEHLER = "konfigurationsfehler"
STATUS_ZUGANG_ABGELEHNT = "zugang_abgelehnt"
STATUS_ABO_BEENDET = "abo_beendet"
STATUS_ABO_INAKTIV = "abo_inaktiv"
STATUS_NOTBETRIEB = "notbetrieb"
STATUS_DATENFEHLER = "datenfehler"
STATUS_ABGEMELDET = "abgemeldet"
ADDON_STATUS_VALUES = (
    STATUS_STARTET, STATUS_REGELT, STATUS_KONFIGURATIONSFEHLER, STATUS_ZUGANG_ABGELEHNT, STATUS_ABO_BEENDET,
    STATUS_ABO_INAKTIV, STATUS_NOTBETRIEB, STATUS_DATENFEHLER, STATUS_ABGEMELDET,
)
# Nur die Integration (zweiter Waechter, Spec TP7 1.3).
# Setup gilt als fertig (TP12c 3.1, Nutzer-Entscheidung 3): Eintrag und Waechter entstehen auch mit
# Warnzustand; der Abschlusstext nennt ihn.
SETUP_DONE_STATUSES = (STATUS_REGELT, STATUS_DATENFEHLER, STATUS_NOTBETRIEB, STATUS_ABO_INAKTIV, STATUS_ABO_BEENDET)
STATUS_ADDON_GESTOPPT = "addon_gestoppt"
STATUS_REAGIERT_NICHT = "reagiert_nicht"
STATUS_SENSOR_VALUES = ADDON_STATUS_VALUES + (STATUS_ADDON_GESTOPPT, STATUS_REAGIERT_NICHT)
BOOST_KEINER = "keiner"
BOOST_VALUES = (BOOST_KEINER, "komfort", "notfall")
ABO_VALUES = ("aktiv", "inaktiv", "beendet", "unbekannt")
DATENFEHLER_ARTEN = ("lokal", "server", "anlage")
DATENFEHLER_KEINER = "keiner"
HINT_FIELDS = ("raumfuehler_ausgefallen", "batterie_niedrig", "manueller_eingriff")

STATUS_WAIT_SECONDS = 180
SIGN_OFF_WAIT_SECONDS = 60
WATCHDOG_INTERVAL_SECONDS = 300
STOPPED_AFTER_CHECKS = 2
SILENCE_SECONDS = 900
MAX_RESTARTS_PER_WINDOW = 3
RESTART_WINDOW_SECONDS = 3600

# Meldungen des zweiten Waechters (auf Deutsch wie alle Meldungen des Add-ons).
WATCHDOG_MESSAGES = {
    STATUS_ADDON_GESTOPPT: (
        "SmartHeat: Ein SmartHeat-Add-on läuft nicht, die Heizungssteuerung ist unterbrochen. "
        "SmartHeat startet es neu."
    ),
    STATUS_REAGIERT_NICHT: (
        "SmartHeat: Das Add-on Heizungsbrücke meldet sich seit 15 Minuten nicht, die Heizungssteuerung "
        "ist unterbrochen. SmartHeat startet es neu."
    ),
}
WATCHDOG_REASONS = {
    STATUS_ADDON_GESTOPPT: "Add-on läuft nicht: {addons}",
    STATUS_REAGIERT_NICHT: "Heizungsbrücke meldet sich seit 15 Minuten nicht",
}
WATCHDOG_ALL_CLEAR_MESSAGE = "SmartHeat: Die SmartHeat-Add-ons laufen wieder, die Heizungssteuerung ist wieder aktiv."


def slug(tenant_id: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", tenant_id.lower())


def entity_id(platform: str, tenant_id: str, key: str) -> str:
    """Feste Entity-IDs: sensor.smartheat_<slug>_status wie in TP6."""
    return f"{platform}.smartheat_{slug(tenant_id)}_{key}"


def watchdog_notification_id(tenant_id: str) -> str:
    return f"smartheat_{slug(tenant_id)}_addon"


# Offene Schritte beim Entfernen/Rueckbau (TP12c); Hinweistexte common.open_step_<problem>.
PROBLEM_SUPERVISOR = "supervisor"
PROBLEM_FOREIGN_TENANT = "foreign_tenant"
PROBLEM_NO_SIGN_OFF = "no_sign_off"
PROBLEM_STOP = "stop"
PROBLEM_REVOKE = "revoke"
PROBLEM_CLEAR = "clear"
PROBLEM_PROFILE = "profile"
PROBLEM_ADDONS = "addons"


def removal_notification_id(tenant_id: str) -> str:
    return f"smartheat_{slug(tenant_id)}_removal"


def setup_notification_id(tenant_id: str) -> str:
    return f"smartheat_{slug(tenant_id)}_setup"


def repair_issue_id(entry_id: str) -> str:
    return f"complete_setup_{entry_id}"


def signal_update(entry_id: str) -> str:
    return f"{DOMAIN}_update_{entry_id}"


# Einzel-Entity-Rollen, die als entity_<rolle> ins Add-on gehen (ohne KPI-Rollen). Die
# Raumfuehler gehen als Liste room_sensors (Spec TP6 3.1). Kein geteilter Code zwischen den
# Repos; Cross-Repo-Gleichheit prueft tools/contract_check.py im Dev-Root.
ROLE_DOMAINS: dict[str, list[str]] = {
    "entity_room_target": ["sensor", "climate"],
    "entity_outdoor_temp": ["sensor", "weather"],
    "entity_curve_current": ["number"],
    # Parallelverschiebung (TP11): Zonen-Wunschtemperatur ueber die Climate-Entity (Vaillant).
    # Nur climate (Controller-Ruling #11b) -- ein direkter number-Wert anderer Hersteller ist
    # kein Anwendungsfall der aktuellen Profile.
    "entity_shift_current": ["climate"],
    # Mindestvorlauftemperatur: nur Untergrenze, das Add-on setzt sie auf die Wunschtemperatur.
    "entity_min_flow": ["number"],
    "entity_heat_limit": ["number", "sensor"],
    # Vorlauf-Soll der Therme (optional): zeigt dem Server, wann geheizt wird.
    "entity_flow_setpoint": ["sensor"],
}

# Felder des Schritts "Anlagenwerte" (Wizard). Hier statt in config_flow.py, damit der
# Cross-Repo-Contract-Check (tools/contract_check.py, laedt nur const.py) sie mit den
# Pflichtoptionen des Add-ons vergleichen kann.
PLANT_FIELDS = ("entity_curve_current", "entity_shift_current", "entity_min_flow", "entity_heat_limit", "entity_outdoor_temp")
# Vorlauf-Soll der Therme: optional, zeigt dem Server nur, wann geheizt wird (Praezisierung 9).
OPTIONAL_PLANT_FIELDS = ("entity_flow_setpoint",)


def entry_incomplete(data: Mapping) -> bool:
    """Eintrag, der fuer den aktuellen Stand nicht reicht (AU-037): markiert oder ohne Profil oder
    ohne ein Pflichtfeld der Anlagenwerte. Weg: Neu konfigurieren."""
    entities = data.get("entities") or {}
    return bool(data.get(DATA_INCOMPLETE)) or not data.get("profile_id") or any(f not in entities for f in PLANT_FIELDS)
# Vom Add-on beschriebene bzw. dem Heizkreis zugeordnete Anlagen-Felder: nur aus dem Config-Entry
# des gewaehlten Kreises waehlbar (TP12c, AU-019). Aussentemperatur bleibt frei (Wetter-Ersatz).
WRITE_ROLE_FIELDS = ("entity_curve_current", "entity_shift_current", "entity_min_flow", "entity_heat_limit", "entity_flow_setpoint")

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


# Auswahl im Entity-Selektor (Wizard und Optionen): Eintraege oder-verknuepft, Domain und
# Geraeteklasse je Eintrag und-verknuepft. Bewusst nur `filter`, kein Legacy-`domain` daneben
# (wie das Frontend beides kombiniert, ist nicht festgelegt) -- die Domain prueft deshalb
# validation.check_domain im Backend. Sensoren ohne Geraeteklasse erscheinen nicht.
_TEMPERATURE_SENSOR = {"domain": "sensor", "device_class": "temperature"}
ENTITY_FILTERS: dict[str, list[dict[str, str]]] = {
    OPTION_ROOM_SENSORS: [_TEMPERATURE_SENSOR, {"domain": "climate"}],
    # Die sensor-Domain der Einzelrollen ist jeweils eine Temperatur (Soll, Aussen, Heizgrenze).
    **{role: [_TEMPERATURE_SENSOR if domain == "sensor" else {"domain": domain} for domain in domains]
       for role, domains in ROLE_DOMAINS.items()},
    "entity_flow_temperature": [_TEMPERATURE_SENSOR],
    "entity_return_temperature": [_TEMPERATURE_SENSOR],
    "entity_system_water_pressure": [{"domain": "sensor", "device_class": "pressure"}],
    "entity_operating_mode": [{"domain": "sensor"}],
    "entity_efficiency_ratio": [{"domain": "sensor"}],
    **{kpi_energy_role(channel): [{"domain": "sensor", "device_class": "energy"}] for channel in KPI_ENERGY_CHANNELS},
}


# Beide Add-ons kommen aus diesem einen Custom-Repository. Ein Supervisor praefigiert den
# Slug eines von einem Custom-Repository installierten Add-ons mit einem Repository-Hash
# (verifiziert gegen einen echten Supervisor, z.B. "f5f6325b_heizungsbruecke" statt nur
# "heizungsbruecke"). Die beiden Konstanten unten sind deshalb NICHT der tatsaechliche
# API-Slug, sondern nur der bare config.yaml-Slug -- supervisor_client.py loest daraus zur
# Laufzeit per ADDON_REPOSITORY_URL + Slug-Suffix den echten, installationsspezifischen Slug auf.
ADDON_REPOSITORY_URL = "https://github.com/LucaHartfuss/SmartHeat-for-HomeAssistant"
HEIZUNGSBRUECKE_ADDON_SLUG = "heizungsbruecke"
CLOUDFLARED_ADDON_SLUG = "cloudflared_access_mqtt"

# Mindestversionen der Add-ons fuer diesen Wizard (Spec TP6 1, Schritt 0; I4).
MIN_ADDON_VERSIONS: dict[str, str] = {
    HEIZUNGSBRUECKE_ADDON_SLUG: "0.24.0",
    CLOUDFLARED_ADDON_SLUG: "1.0.0",
}

ADDON_SPECS: list[tuple[str, str]] = [
    ("Heizungsbruecke", HEIZUNGSBRUECKE_ADDON_SLUG),
    ("Cloudflared Access TCP-Bridge", CLOUDFLARED_ADDON_SLUG),
]

# Kundensichtbare Anzeigenamen der Add-ons mit Umlauten (Controller-Entscheidung F7), fuer
# WATCHDOG_REASONS. ADDON_SPECS bleibt ASCII: das ist der Anzeigename, den AddonManager in
# Supervisor-Logs verwendet, kein Kundentext. Cloudflared hat im Namen keinen Umlaut; hier steht
# absichtlich der echte config.yaml-Name, damit der Kunde das Add-on in Einstellungen ->
# Add-ons wiederfindet.
ADDON_DISPLAY_NAMES: dict[str, str] = {
    HEIZUNGSBRUECKE_ADDON_SLUG: "Heizungsbrücke",
    CLOUDFLARED_ADDON_SLUG: "Cloudflared Access TCP-Bridge",
}
