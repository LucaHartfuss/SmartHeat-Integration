"""Konstanten der SmartHeat-Integration ab 1.0.0 (Spec 5c): Oberflaeche ueber der Einrichtungs-API des Servers, Status
und Wunschtemperatur ueber das lokale Add-on. Ereignis-, Discovery- und Routen-Werte sind Cross-Repo-Vertrag
(tools/contract_check.py im Dev-Root)."""
import re
from collections.abc import Mapping

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
WATCHDOG_ALL_CLEAR_MESSAGE = "SmartHeat: Das Add-on läuft wieder, die Heizungssteuerung ist wieder aktiv."

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
# Alte Namen, die die Module vor dem Umbau (Plan 5c I, Tasks 2-7) noch importieren.
# entfällt in Task 7 (Plan 5c I): Task 7 loescht diesen ganzen Block.
# ============================================================================

# entfällt in Task 7 (Plan 5c I): alte Add-on-Tabelle mit cloudflared (ADDON_SPECS/MIN_ADDON_VERSIONS gelten neu).
LEGACY_MIN_ADDON_VERSIONS: dict[str, str] = {HEIZUNGSBRUECKE_ADDON_SLUG: "0.36.0", "cloudflared_access_mqtt": "1.0.0"}
LEGACY_ADDON_SPECS: list[tuple[str, str]] = [
    ("Heizungsbruecke", HEIZUNGSBRUECKE_ADDON_SLUG), ("Cloudflared Access TCP-Bridge", "cloudflared_access_mqtt"),
]

DEFAULT_HEIZUNGSSERVER_BASE_URL = "https://accounts.hartfussha.org"

# Add-on-Optionen, die der Wizard 2.0 als Listen bzw. Laufkennung schreibt (Spec TP6 3.1).
OPTION_ROOM_SENSORS = "room_sensors"

OPTION_NOTIFY_SERVICES = "notify_services"

OPTION_BATTERY_ENTITIES = "battery_entities"

OPTION_SETUP_ID = "setup_id"

OPTION_ENTITY_ROOM_TARGET = "entity_room_target"

OPTION_ABGEMELDET = "abgemeldet"

OPTION_NOTIFY_HINTS_OFF = "notify_hints_off"

OPTION_LEVER_SET = "lever_set"

# Abfrageintervall der Heizungs-Integration fuer die Wartezeit nach eigenem Schreiben (Add-on config.py,
# POLL_INTERVAL_RANGE); geschrieben nur aus einer poll_interval_option im Bereich (Plan 3c, Praezisierung 13).
OPTION_POLL_INTERVAL = "poll_interval_seconds"

POLL_INTERVAL_OPTION_RANGE = (10, 3600)

# Provisioning (Spec Hersteller-Abstraktion 4.3, Plan 3c): Server broker/wire.py, Contract-Check 43.
CLIENT_TYPE_HA = "ha"

PROVISION_CLIENT_KEYS = ("client_type", "client_version")

# Abschaltbare Hinweis-Kategorien; gleich in smartheat_runtime/notifier.py und config.yaml (Contract-Check 15).
HINT_CATEGORIES = (
    "raumfuehler", "batterie", "manueller_eingriff", "quellwechsel", "schreibbudget", "schreibzaehler",
)

# Markiert einen unvollstaendigen Eintrag; heute setzt ihn kein Code mehr (die v1-Migration ist
# entfallen), er bleibt als Merkmal fuer entry_incomplete().
DATA_INCOMPLETE = "unvollstaendig"

# Zugang der Anlage (Spec AWS-IoT 4.1/4.2, Plan AWS-2); Werte wie Server broker/wire.py, Contract-Check 41.
OPTION_TRANSPORT = "transport"

OPTION_INSTALLATION_TOKEN = "installation_token"

PASSWORD_CREDENTIAL_OPTIONS = ("mqtt_username", "mqtt_password")

CERTIFICATE_CREDENTIAL_OPTIONS = ("tls_certificate", "tls_private_key")

BRIDGE_ACCESS_OPTIONS = (OPTION_TRANSPORT, OPTION_INSTALLATION_TOKEN, *PASSWORD_CREDENTIAL_OPTIONS,
                         *CERTIFICATE_CREDENTIAL_OPTIONS)

CLOUDFLARED_ACCESS_OPTIONS = ("hostname", "service_token_id", "service_token_secret")

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

# Schluessel der Server-profile_params, die in die Add-on-Optionen gehen (Contract-Check 3)
PROFILE_PARAM_KEYS = ("verteilsystem", "daily_trigger_time")

# Profilwechsel ohne neue Zugangsdaten; Route im Server: accounts_api.py (Contract-Check 17).
PROFILE_PATH = "/tenants/{tenant_id}/profile"

INSTALLATION_PATH = "/tenants/{tenant_id}/installation"

# Add-on-Option der Heizungsbruecke mit der Basis-URL der accounts-api (Spec TP3 S3); das
# Entfernen widerruft damit dort, wo auch das Add-on fragt (Spec TP8 4).
OPTION_ACCOUNTS_API_BASE_URL = "accounts_api_base_url"

REVOKE_TIMEOUT_SECONDS = 10

# mypyllant-Pollintervall, auf das OWN_WRITE_SETTLE_SECONDS (2100 s) im Add-on abgestimmt ist (client1:
# 30 min, 5 min Reserve). Laenger -> Warnung im Wizard (TP12c, AU-017); das Add-on bleibt fest.
POLL_INTERVAL_MAX_SECONDS = 1800

# Katalog-Version, die diese Integration voraussetzt (Plan 3c: Rollen nach Hebeln, Hebelsaetze).
# Ein aelterer Server-Katalog bricht den Wizard ab (catalog_outdated), statt still auf die alte
# Zonensuche zurueckzufallen.
REQUIRED_CATALOG_VERSION = 4

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

# Nur die Integration (zweiter Waechter, Spec TP7 1.3).
# Setup gilt als fertig (TP12c 3.1, Nutzer-Entscheidung 3): Eintrag und Waechter entstehen auch mit
# Warnzustand; der Abschlusstext nennt ihn.
SETUP_DONE_STATUSES = (STATUS_REGELT, STATUS_DATENFEHLER, STATUS_NOTBETRIEB, STATUS_ABO_INAKTIV, STATUS_ABO_BEENDET)

STATUS_WAIT_SECONDS = 180

SIGN_OFF_WAIT_SECONDS = 60

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

# Hebel -> Add-on-Option (Katalog v3 heisst nach Hebeln, Plan 3c Praezisierung 1). Gleich heizungsbruecke
# ha_binding.LEVER_ROLES mit Praefix entity_ (Contract-Check 42). Alle anderen Katalog-Rollen: entity_<rolle>.
LEVER_OPTIONS: dict[str, str] = {
    "curve": "entity_curve_current", "room_setpoint": "entity_shift_current", "level": "entity_level_current",
    "heat_limit": "entity_heat_limit", "min_flow": "entity_min_flow",
}

def field_for_role(role: str) -> str:
    return LEVER_OPTIONS.get(role, f"entity_{role}")

# Felder des Schritts "Anlagenwerte" (Wizard) je Hebelsatz. Hier statt in config_flow.py, damit der
# Cross-Repo-Contract-Check (tools/contract_check.py, laedt nur const.py) sie mit den
# Pflichtoptionen des Add-ons vergleichen kann.
LEVER_SETS = ("vaillant_vrc720", "weishaupt_wwp", "weishaupt_wwp_basis", "viessmann_vicare")

# Pflicht-Anlagenwerte je Hebelsatz (ohne entity_room_target); = Add-on config.REQUIRED_ENTITY_OPTIONS ohne
# entity_room_target (Contract-Check 26).
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

# Optional: Vorlauf-Soll zeigt dem Server, wann geheizt wird; Weishaupt-Basis liest die Steigung nur (readonly).
OPTIONAL_LEVER_SET_FIELDS: dict[str, tuple[str, ...]] = {
    "vaillant_vrc720": ("entity_flow_setpoint",),
    "weishaupt_wwp": ("entity_flow_setpoint",),
    "weishaupt_wwp_basis": ("entity_curve_current", "entity_flow_setpoint"),
    "viessmann_vicare": (),
}

_NUMBER = ["number"]

# Domaenen der Hebelsatz-Felder; Gegenstueck: Add-on config.writable_entity_rules (Contract-Check 25).
LEVER_SET_DOMAINS: dict[str, dict[str, list[str]]] = {
    "vaillant_vrc720": {
        # Parallelverschiebung (TP11): Zonen-Wunschtemperatur ueber die Climate-Entity.
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

# Felder ausserhalb der Hebelsaetze (fuer alle gleich).
ROLE_DOMAINS: dict[str, list[str]] = {
    "entity_room_target": ["sensor", "climate"],
    "entity_outdoor_temp": ["sensor", "weather"],
    # Vorlauf-Soll der Therme (optional): zeigt dem Server, wann geheizt wird.
    "entity_flow_setpoint": ["sensor"],
}

def plant_fields(lever_set: str) -> tuple[str, ...]:
    return LEVER_SET_FIELDS[lever_set] + OPTIONAL_LEVER_SET_FIELDS[lever_set]

def write_role_fields(lever_set: str) -> tuple[str, ...]:
    """Felder, die dem gewaehlten Heizkreis zugeordnet sein muessen (TP12c, AU-019): alle ausser der Aussentemperatur
    (Wetter-Ersatz)."""
    return tuple(field for field in plant_fields(lever_set) if field != "entity_outdoor_temp")

def field_domains(lever_set: str, field: str) -> list[str]:
    return LEVER_SET_DOMAINS[lever_set].get(field) or ROLE_DOMAINS[field]

def lever_set_levers(lever_set: str) -> tuple[str, ...]:
    """Hebel (inkl. abgeleiteter) eines Hebelsatzes aus seinen Pflichtfeldern, in LEVER_OPTIONS-Reihenfolge."""
    return tuple(lever for lever, option in LEVER_OPTIONS.items() if option in LEVER_SET_FIELDS[lever_set])

def entry_incomplete(data: Mapping) -> bool:
    """Eintrag, der fuer den aktuellen Stand nicht reicht (AU-037, Plan 3c): markiert, ohne Profil, ohne bekannten
    Hebelsatz, ohne Verschiebungshebel (`shift_lever`, sonst benennt sensor.lever_entity_key den Hebel falsch) oder
    ohne ein Pflichtfeld des Hebelsatzes. Weg: Neu konfigurieren."""
    entities = data.get("entities") or {}
    lever_set = data.get(OPTION_LEVER_SET)
    shift_lever = data.get("shift_lever")
    return (
        bool(data.get(DATA_INCOMPLETE)) or not data.get("profile_id") or lever_set not in LEVER_SET_FIELDS
        or not isinstance(shift_lever, str) or not shift_lever
        or any(field not in entities for field in LEVER_SET_FIELDS[lever_set])
    )

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
    # Audit 4 P-B: Liefer-Signale des Waermeerzeugers (der Server weist damit die Waermelieferung nach).
    "has_generator_hours": "entity_generator_hours",
    "has_generator_starts": "entity_generator_starts",
    "has_generator_state": "entity_generator_state",
}

# Ohne state_class-Pruefung: Text-Zustaende und Zaehler, deren state_class je Hersteller verschieden ist
# (mypyllant measurement, ViCare total_increasing).
KPI_ROLES_WITHOUT_STATE_CLASS = (
    "entity_operating_mode", "entity_generator_hours", "entity_generator_starts", "entity_generator_state",
)

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
    "primary_dhw", "thermal_heating", "thermal_dhw", "electrical_total",
)

def kpi_energy_role(channel: str) -> str:
    return f"entity_energy_{channel}"

# Auswahl im Entity-Selektor (Wizard und Optionen): Eintraege oder-verknuepft, Domain und
# Geraeteklasse je Eintrag und-verknuepft. Bewusst nur `filter`, kein Legacy-`domain` daneben
# (wie das Frontend beides kombiniert, ist nicht festgelegt) -- die Domain prueft deshalb
# validation.check_domain im Backend. Sensoren ohne Geraeteklasse erscheinen nicht.
TEMPERATURE_SENSOR_FILTER = {"domain": "sensor", "device_class": "temperature"}

ENTITY_FILTERS: dict[str, list[dict[str, str]]] = {
    OPTION_ROOM_SENSORS: [TEMPERATURE_SENSOR_FILTER, {"domain": "climate"}],
    # Die sensor-Domain der Einzelrollen ist jeweils eine Temperatur (Soll, Aussen). Die Hebelsatz-Felder
    # bekommen ihren Filter ueber validation.entity_selector(domains=...) nach field_domains().
    **{role: [TEMPERATURE_SENSOR_FILTER if domain == "sensor" else {"domain": domain} for domain in domains]
       for role, domains in ROLE_DOMAINS.items()},
    "entity_flow_temperature": [TEMPERATURE_SENSOR_FILTER],
    "entity_return_temperature": [TEMPERATURE_SENSOR_FILTER],
    "entity_system_water_pressure": [{"domain": "sensor", "device_class": "pressure"}],
    "entity_operating_mode": [{"domain": "sensor"}],
    "entity_efficiency_ratio": [{"domain": "sensor"}],
    "entity_generator_hours": [{"domain": "sensor"}],
    "entity_generator_starts": [{"domain": "sensor"}],
    "entity_generator_state": [{"domain": "sensor"}],
    **{kpi_energy_role(channel): [{"domain": "sensor", "device_class": "energy"}] for channel in KPI_ENERGY_CHANNELS},
}

CLOUDFLARED_ADDON_SLUG = "cloudflared_access_mqtt"
