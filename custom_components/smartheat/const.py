"""Konstanten der SmartHeat-Integration ab 1.0.0 (Spec 5c): Oberflaeche ueber der Einrichtungs-API des Servers, Status
und Wunschtemperatur ueber das lokale Add-on. Ereignis-, Discovery- und Routen-Werte sind Cross-Repo-Vertrag
(tools/contract_check.py im Dev-Root)."""
import re

DOMAIN = "smartheat"
DEFAULT_BASE_URL = "https://accounts.hartfussha.org"
OPTION_BOOTSTRAP_URL = "bootstrap_url"
REQUEST_TIMEOUT_SECONDS = 30
ERROR_TEXT_MAX = 300

# Status-Ereignis des Add-ons (Plan D Task 7): Schema 2 der Laufzeit plus Geraete-Felder.
STATUS_EVENT = "smartheat_status"
STATUS_EVENT_SCHEMA = 3
STATUS_EVENT_FIELDS = (
    "schema", "tenant_id", "setup_id", "addon_version", "status", "grund", "notbetrieb", "datenfehler",
    "boost", "letzte_serverantwort", "hebelsatz", "hebel", "gelernt", "abo", "abo_frist_ende", "hinweise",
    "geraet_id", "geraet_zustand", "server_verbunden", "raum_soll", "geraet_hinweise",
)
HINWEIS_FIELDS = ("key", "stufe", "text")
GERAET_ZUSTAENDE = (
    "startet", "nicht_uebernommen", "keine_verbindung", "wartet_auf_einrichtung", "regelt", "stoerung", "gesperrt",
    "update_noetig",
)
HINWEIS_KEYS = ("update_noetig", "geraet_doppelt", "wunsch_begrenzt", "soll_quelle_aus", "konfiguration_abgelehnt",
                "gesperrt")
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
STATUS_ADDON_GESTOPPT = "addon_gestoppt"
STATUS_REAGIERT_NICHT = "reagiert_nicht"
STATUS_SENSOR_VALUES = ADDON_STATUS_VALUES + (STATUS_ADDON_GESTOPPT, STATUS_REAGIERT_NICHT)
BOOST_KEINER = "keiner"
BOOST_VALUES = (BOOST_KEINER, "komfort", "notfall")
ABO_VALUES = ("aktiv", "inaktiv", "beendet", "unbekannt")
DATENFEHLER_ARTEN = ("lokal", "server", "anlage")
DATENFEHLER_KEINER = "keiner"
HINT_FIELDS = ("raumfuehler_ausgefallen", "batterie_niedrig", "manueller_eingriff")

# Supervisor-Discovery des Add-ons (Plan D Task 8) und lokales Wunsch-Ereignis (Plan D Task 5).
DISCOVERY_SERVICE = "smartheat"
DISCOVERY_FIELDS = ("geraet_id", "zustand", "claim_code")
# Geraete-ID: "shg-" + 16 Zeichen Base32 (klein) des SHA-256 des oeffentlichen Schluessels (Add-on
# smartheat_device/identity.device_id_for, Server device_protocol.DEVICE_ID_PREFIX/DEVICE_ID_HASH_CHARS).
GERAET_ID_RE = re.compile(r"shg-[a-z2-7]{16}")
ZUSTAND_NICHT_UEBERNOMMEN = "nicht_uebernommen"
WUNSCH_EVENT = "smartheat_wunsch"
RAUM_SOLL_MIN, RAUM_SOLL_MAX, RAUM_SOLL_STEP = 15.0, 25.0, 0.5

# Einrichtungs-API (Server einrichtung_api.ROUTEN, Plan S Task 8).
EINRICHTUNG_PREFIX = "/einrichtung/v1"
EINRICHTUNG_ROUTEN: dict[str, tuple[str, str]] = {
    "anmelden": ("POST", "/anmelden"),
    "abmelden": ("POST", "/abmelden"),
    "uebernehmen": ("POST", "/geraete/uebernehmen"),
    "geraet": ("GET", "/geraet"),
    "inventur": ("POST", "/geraet/inventur"),
    "vorschlag": ("GET", "/geraet/vorschlag"),
    "zuordnung": ("POST", "/geraet/zuordnung"),
    "pruefen": ("POST", "/geraet/pruefen"),
    "einrichten": ("POST", "/geraet/einrichten"),
    "neu_konfigurieren": ("POST", "/geraet/neu-konfigurieren"),
    "entfernen": ("POST", "/geraet/entfernen"),
    "auftrag": ("GET", "/geraet/auftraege/<job_id>"),
}
AUFTRAG_FERTIG = "fertig"
AUFTRAG_FEHLGESCHLAGEN = "fehlgeschlagen"
# Rollen, die der Server im Schritt Anlagenwerte anbieten kann (device_protocol.HA_ROLLEN ohne room_target, das der
# Schritt Raeume zeigt); jede braucht einen Text unter config.step.anlagenwerte.data.
ROLLEN_ANLAGENWERTE = (
    "curve", "room_setpoint", "level", "heat_limit", "min_flow", "mode_select", "setpoint_comfort", "setpoint_setback",
    "outdoor_temp", "flow_temperature", "flow_setpoint", "return_temperature", "operating_mode",
    "system_water_pressure", "efficiency_ratio", "generator_hours", "generator_starts", "generator_state",
    "energy_electrical_heating", "energy_electrical_dhw", "energy_primary_heating", "energy_primary_dhw",
    "energy_thermal_heating", "energy_thermal_dhw", "energy_electrical_total",
)

DISCOVERY_WAIT_SECONDS = 300
INVENTUR_WAIT_SECONDS = 120
AUFTRAG_WAIT_SECONDS = 900
ENTFERNEN_WAIT_SECONDS = 300
POLL_SECONDS = 2

# Waechter (Spec TP7 1.3), nur noch die Heizungsbruecke.
WATCHDOG_INTERVAL_SECONDS = 300
STOPPED_AFTER_CHECKS = 2
SILENCE_SECONDS = 900
MAX_RESTARTS_PER_WINDOW = 3
RESTART_WINDOW_SECONDS = 3600
WATCHDOG_MESSAGES = {
    STATUS_ADDON_GESTOPPT: ("SmartHeat: Das Add-on Heizungsbrücke läuft nicht, die Heizungssteuerung ist unterbrochen. "
                            "SmartHeat startet es neu."),
    STATUS_REAGIERT_NICHT: ("SmartHeat: Das Add-on Heizungsbrücke meldet sich seit 15 Minuten nicht, die "
                            "Heizungssteuerung ist unterbrochen. SmartHeat startet es neu."),
}
WATCHDOG_REASONS = {
    STATUS_ADDON_GESTOPPT: "Add-on läuft nicht: {addons}",
    STATUS_REAGIERT_NICHT: "Heizungsbrücke meldet sich seit 15 Minuten nicht",
}

ADDON_REPOSITORY_URL = "https://github.com/LucaHartfuss/SmartHeat-for-HomeAssistant"
HEIZUNGSBRUECKE_ADDON_SLUG = "heizungsbruecke"
MIN_ADDON_VERSIONS: dict[str, str] = {HEIZUNGSBRUECKE_ADDON_SLUG: "1.0.0"}
ADDON_SPECS: list[tuple[str, str]] = [("Heizungsbruecke", HEIZUNGSBRUECKE_ADDON_SLUG)]
ADDON_DISPLAY_NAMES: dict[str, str] = {HEIZUNGSBRUECKE_ADDON_SLUG: "Heizungsbrücke"}


def slug(tenant_id: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", tenant_id.lower())


def entity_id(platform: str, tenant_id: str, key: str) -> str:
    """Feste Entity-IDs: sensor.smartheat_<slug>_status wie seit TP6 (client1-Dashboards)."""
    return f"{platform}.smartheat_{slug(tenant_id)}_{key}"


def watchdog_notification_id(tenant_id: str) -> str:
    return f"smartheat_{slug(tenant_id)}_addon"


def signal_update(entry_id: str) -> str:
    return f"{DOMAIN}_update_{entry_id}"


def hinweis_issue_id(key: str) -> str:
    return f"hinweis_{re.sub(r'[^a-z0-9_]', '_', key.lower())}"


# ============================================================================
# Bleibt bis Plan 5c W Teil B (Contract-Loader): tools/contract_check.py liest diese Namen aus const.py und
# (catalog.py, tests/fixtures/*) weiter; ohne sie bricht der Loader ab (Exit 2). W Teil B B1 stellt die Checks
# um und entfernt diesen Block zusammen mit catalog.py und den Fixtures. Kein Code der Integration nutzt ihn.
# ============================================================================
DEFAULT_HEIZUNGSSERVER_BASE_URL = "https://accounts.hartfussha.org"
CLOUDFLARED_ADDON_SLUG = "cloudflared_access_mqtt"

OPTION_ROOM_SENSORS = "room_sensors"
OPTION_NOTIFY_SERVICES = "notify_services"
OPTION_BATTERY_ENTITIES = "battery_entities"
OPTION_SETUP_ID = "setup_id"
OPTION_ENTITY_ROOM_TARGET = "entity_room_target"
OPTION_ABGEMELDET = "abgemeldet"
OPTION_NOTIFY_HINTS_OFF = "notify_hints_off"
OPTION_TRANSPORT = "transport"
OPTION_INSTALLATION_TOKEN = "installation_token"
OPTION_ACCOUNTS_API_BASE_URL = "accounts_api_base_url"
LIST_OPTIONS = (OPTION_ROOM_SENSORS, OPTION_NOTIFY_SERVICES, OPTION_BATTERY_ENTITIES)

CLIENT_TYPE_HA = "ha"
PROVISION_CLIENT_KEYS = ("client_type", "client_version")
HINT_CATEGORIES = (
    "raumfuehler", "batterie", "manueller_eingriff", "quellwechsel", "schreibbudget", "schreibzaehler",
)
PASSWORD_CREDENTIAL_OPTIONS = ("mqtt_username", "mqtt_password")
CERTIFICATE_CREDENTIAL_OPTIONS = ("tls_certificate", "tls_private_key")
TRANSPORT_MOSQUITTO = "mosquitto_cloudflared"
TRANSPORT_IOT_CORE = "iot_core"
CREDENTIAL_PASSWORD = "password"
CREDENTIAL_CERTIFICATE = "certificate"
DESCRIPTOR_KEYS = {
    TRANSPORT_MOSQUITTO: frozenset({"kind", "host", "port", "cloudflared"}),
    TRANSPORT_IOT_CORE: frozenset({"kind", "host", "port", "alpn", "ca_pem", "client_id"}),
}
CLOUDFLARED_KEYS = frozenset({"hostname", "service_token_id", "service_token_secret"})
CREDENTIAL_FOR_TRANSPORT = {TRANSPORT_MOSQUITTO: CREDENTIAL_PASSWORD, TRANSPORT_IOT_CORE: CREDENTIAL_CERTIFICATE}
CREDENTIAL_KEYS = {
    CREDENTIAL_PASSWORD: frozenset({"kind", "username", "password"}),
    CREDENTIAL_CERTIFICATE: frozenset({"kind", "certificate_pem"}),
}
PROVISION_RESPONSE_KEYS = frozenset({"transport", "credential", "installation_token", "profile_params"})
PROFILE_PARAM_KEYS = ("verteilsystem", "daily_trigger_time")
PROFILE_PATH = "/tenants/{tenant_id}/profile"
INSTALLATION_PATH = "/tenants/{tenant_id}/installation"
POLL_INTERVAL_MAX_SECONDS = 1800
REQUIRED_CATALOG_VERSION = 4
PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    "room": (5.0, 35.0),
    "outdoor": (-40.0, 45.0),
    "heat_limit": (5.0, 25.0),
}

LEVER_OPTIONS: dict[str, str] = {
    "curve": "entity_curve_current", "room_setpoint": "entity_shift_current", "level": "entity_level_current",
    "heat_limit": "entity_heat_limit", "min_flow": "entity_min_flow",
}


def field_for_role(role: str) -> str:
    return LEVER_OPTIONS.get(role, f"entity_{role}")


LEVER_SETS = ("vaillant_vrc720", "weishaupt_wwp", "weishaupt_wwp_basis", "viessmann_vicare")
LEVER_SET_FIELDS: dict[str, tuple[str, ...]] = {
    "vaillant_vrc720": (
        "entity_curve_current", "entity_shift_current", "entity_min_flow", "entity_heat_limit", "entity_outdoor_temp",
    ),
    "weishaupt_wwp": (
        "entity_curve_current", "entity_shift_current", "entity_heat_limit", "entity_mode_select",
        "entity_setpoint_comfort", "entity_setpoint_setback", "entity_outdoor_temp",
    ),
    "weishaupt_wwp_basis": (
        "entity_shift_current", "entity_mode_select", "entity_setpoint_comfort", "entity_setpoint_setback",
        "entity_outdoor_temp",
    ),
    "viessmann_vicare": (
        "entity_curve_current", "entity_level_current", "entity_shift_current", "entity_mode_select",
        "entity_outdoor_temp",
    ),
}
OPTIONAL_LEVER_SET_FIELDS: dict[str, tuple[str, ...]] = {
    "vaillant_vrc720": ("entity_flow_setpoint",),
    "weishaupt_wwp": ("entity_flow_setpoint",),
    "weishaupt_wwp_basis": ("entity_curve_current", "entity_flow_setpoint"),
    "viessmann_vicare": (),
}
_NUMBER = ["number"]
LEVER_SET_DOMAINS: dict[str, dict[str, list[str]]] = {
    "vaillant_vrc720": {
        "entity_curve_current": _NUMBER, "entity_shift_current": ["climate"], "entity_min_flow": _NUMBER,
        "entity_heat_limit": _NUMBER,
    },
    "weishaupt_wwp": {
        "entity_curve_current": _NUMBER, "entity_shift_current": _NUMBER, "entity_heat_limit": _NUMBER,
        "entity_mode_select": ["select"], "entity_setpoint_comfort": _NUMBER, "entity_setpoint_setback": _NUMBER,
    },
    "weishaupt_wwp_basis": {
        "entity_curve_current": _NUMBER, "entity_shift_current": _NUMBER, "entity_mode_select": ["select"],
        "entity_setpoint_comfort": _NUMBER, "entity_setpoint_setback": _NUMBER,
    },
    "viessmann_vicare": {
        "entity_curve_current": _NUMBER, "entity_level_current": _NUMBER, "entity_shift_current": _NUMBER,
        "entity_mode_select": ["climate"],
    },
}
ROLE_DOMAINS: dict[str, list[str]] = {
    "entity_room_target": ["sensor", "climate"],
    "entity_outdoor_temp": ["sensor", "weather"],
    "entity_flow_setpoint": ["sensor"],
}


def plant_fields(lever_set: str) -> tuple[str, ...]:
    return LEVER_SET_FIELDS[lever_set] + OPTIONAL_LEVER_SET_FIELDS[lever_set]


def lever_set_levers(lever_set: str) -> tuple[str, ...]:
    return tuple(lever for lever, option in LEVER_OPTIONS.items() if option in LEVER_SET_FIELDS[lever_set])


KPI_SCALAR_ROLE_BY_CAPABILITY: dict[str, str] = {
    "has_flow_temperature": "entity_flow_temperature",
    "has_return_temperature": "entity_return_temperature",
    "has_operating_mode": "entity_operating_mode",
    "has_water_pressure": "entity_system_water_pressure",
    "has_manufacturer_efficiency_sensor": "entity_efficiency_ratio",
    "has_generator_hours": "entity_generator_hours",
    "has_generator_starts": "entity_generator_starts",
    "has_generator_state": "entity_generator_state",
}
KPI_ENERGY_CHANNELS: tuple[str, ...] = (
    "electrical_heating", "electrical_dhw", "primary_heating",
    "primary_dhw", "thermal_heating", "thermal_dhw", "electrical_total",
)
