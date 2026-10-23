"""Konstanten der SmartHeat-Integration: Oberflaeche ueber der Einrichtungs-API des Servers, Status
und Wunschtemperatur ueber das lokale Add-on. Ereignis-, Discovery- und Routen-Werte sind Cross-Repo-Vertrag
(tools/contract_check.py im Dev-Root)."""
import re

DOMAIN = "smartheat"
DEFAULT_BASE_URL = "https://accounts.hartfussha.org"
OPTION_BOOTSTRAP_URL = "bootstrap_url"
REQUEST_TIMEOUT_SECONDS = 30
ERROR_TEXT_MAX = 300
# Fehlerantworten der Einrichtungs-API: Body hoechstens 64 KiB gelesen, `fehler`-Werte hoechstens 200 Zeichen.
FEHLER_BODY_MAX_BYTES = 65536
FEHLER_WERT_MAX = 200

# Status-Ereignis des Add-ons: Schema 2 der Laufzeit plus Geraete-Felder.
STATUS_EVENT = "smartheat_status"
STATUS_EVENT_SCHEMA = 3
STATUS_EVENT_FIELDS = (
    "schema", "tenant_id", "addon_version", "status", "grund", "notbetrieb", "datenfehler",
    "boost", "letzte_serverantwort", "hebelsatz", "hebel", "gelernt", "abo", "abo_frist_ende", "hinweise",
    "geraet_id", "geraet_zustand", "server_verbunden", "raum_soll", "geraet_hinweise",
)
# Felder eines Geraete-Hinweises (geraet_hinweise); repairs.py und number.py lesen sie ueber diese Namen.
HINWEIS_FIELDS = ("key", "stufe", "text")
HINWEIS_FELD_KEY, HINWEIS_FELD_STUFE, HINWEIS_FELD_TEXT = HINWEIS_FIELDS
HINWEIS_SOLL_QUELLE_AUS = "soll_quelle_aus"
HINWEIS_KEYS = ("update_noetig", "geraet_doppelt", "wunsch_begrenzt", HINWEIS_SOLL_QUELLE_AUS,
                "konfiguration_abgelehnt", "gesperrt")
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

# Supervisor-Discovery des Add-ons und lokales Wunsch-Ereignis.
DISCOVERY_SERVICE = "smartheat"
DISCOVERY_FIELDS = ("geraet_id", "zustand", "claim_code")
# Geraete-ID: "shg-" + 16 Zeichen Base32 (klein) des SHA-256 des oeffentlichen Schluessels (Add-on
# smartheat_device/identity.device_id_for, Server device_protocol.DEVICE_ID_PREFIX/DEVICE_ID_HASH_CHARS).
GERAET_ID_RE = re.compile(r"shg-[a-z2-7]{16}")
# stdin-Zeile der Integration an die Heizungsbruecke beim Loeschen des Eintrags: Kopie von
# heizungsbruecke/stdin_befehl.py (Add-on), Contract-Check Vertrag V5 (tools/contract_check.py im Dev-Root).
STDIN_BEFEHL_ENTFERNEN = "entfernen"
STDIN_ZEILE_FORMAT = "{befehl} {geraet_id}\n"
WUNSCH_EVENT = "smartheat_wunsch"
RAUM_SOLL_MIN, RAUM_SOLL_MAX, RAUM_SOLL_STEP = 15.0, 25.0, 0.5
# Grenzen der Auswahl im Assistenten: Kopie von heizungsserver einrichtung/ha.py MAX_RAUMFUEHLER,
# MAX_BATTERIEN, MAX_HANDYS (V3, Contract-Check 76); mehr lehnt der Server mit 400 ab.
EINRICHTUNG_MAX_RAUMFUEHLER = 8
EINRICHTUNG_MAX_BATTERIEN = 16
EINRICHTUNG_MAX_HANDYS = 10

# Einrichtungs-API (Server einrichtung/api.py ROUTEN).
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
# V3-Felder der Einrichtungs-API: Kopie von einrichtung/api.py (Server), Contract-Check (Vertrag
# V3). Je Route (Pflicht, optional) bzw. die Felder der Antwort; die Werte stehen ausgeschrieben, damit der Vergleich
# wortlich bleibt.
EINRICHTUNG_ANFRAGE_FELDER: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "anmelden": (("email", "password"), ()),
    "abmelden": ((), ()),
    "uebernehmen": (("device_id",), ("code",)),
    "geraet": ((), ()),
    "inventur": ((), ()),
    "vorschlag": ((), ()),
    "zuordnung": (("kreis", "profil", "hebelsatz"), ("tenant_id",)),
    "pruefen": (("kreis", "profil", "hebelsatz", "rollen", "raumfuehler"),
                ("batterien", "benachrichtigung", "hinweise_aus", "raum_soll", "tenant_id", "ersetzen")),
    "einrichten": (("kreis", "profil", "hebelsatz", "rollen", "raumfuehler", "tenant_id", "raum_soll"),
                   ("batterien", "benachrichtigung", "hinweise_aus", "ersetzen")),
    "neu_konfigurieren": (("kreis", "profil", "hebelsatz", "rollen", "raumfuehler"),
                          ("batterien", "benachrichtigung", "hinweise_aus", "raum_soll")),
    "entfernen": ((), ()),
    "auftrag": ((), ()),
}
EINRICHTUNG_ANTWORT_FELDER: dict[str, tuple[str, ...]] = {
    "anmelden": ("token", "gueltig_bis"),
    "abmelden": (),
    "uebernehmen": ("device_id",),
    "geraet": ("device_id", "host", "eingerichtet", "tenant_id", "anlagen", "inventur", "auftrag", "soll_bereich",
               "konfiguration", "raum_soll"),
    "inventur": ("command_id",),
    "vorschlag": ("integrationen",),
    "zuordnung": ("kreis", "profil", "hebelsatz", "rollen", "raumfuehler", "room_target", "batterie_je_entity",
                  "benachrichtigung", "hinweise", "raum_soll", "abfrage_s", "hinweis"),
    "pruefen": ("fehler", "warnungen"),
    "einrichten": ("job_id",),
    "neu_konfigurieren": ("job_id",),
    "entfernen": ("freigegeben", "job_id"),
    "auftrag": ("job_id", "art", "zustand", "fehler", "ergebnis", "seit"),
}
EINRICHTUNG_ZUORDNUNG_ROLLE_FELDER = ("rolle", "pflicht", "vorschlag", "herkunft", "kandidaten")
EINRICHTUNG_RAUMFUEHLER_FELDER = ("kandidaten", "vorschlag")
EINRICHTUNG_VORSCHLAG_INTEGRATION_FELDER = ("plattform", "label", "hinweis", "kreise", "profile", "vorschlag")
# Verschachtelte Objekte eines Integrationseintrags der Antwort `vorschlag` (kreise, profile, hebelsaetze, vorschlag).
EINRICHTUNG_VORSCHLAG_KREIS_FELDER = ("schluessel", "label")
EINRICHTUNG_VORSCHLAG_PROFIL_FELDER = ("profile_id", "label", "erzeuger_typ", "verteilsystem", "shift_lever",
                                       "hebelsaetze")
EINRICHTUNG_VORSCHLAG_HEBELSATZ_FELDER = ("id", "hebel")
EINRICHTUNG_VORSCHLAG_AUSWAHL_FELDER = ("kreis", "profil")
# Verschachtelte Objekte der Antwort `geraet`, die der Assistent liest (Eintrag von `anlagen`, `inventur`, Zusatzfelder
# der `konfiguration`).
EINRICHTUNG_GERAET_ANLAGE_FELDER = ("tenant_id", "aktiv", "belegt", "ersetzbar")
EINRICHTUNG_GERAET_INVENTUR_FELDER = ("zustand", "command_id", "zeit", "fehler")
EINRICHTUNG_GERAET_KONFIGURATION_ZUSATZ = ("hebel", "shift_lever")
AUFTRAG_FERTIG = "fertig"
AUFTRAG_FEHLGESCHLAGEN = "fehlgeschlagen"

DISCOVERY_WAIT_SECONDS = 300
INVENTUR_WAIT_SECONDS = 120
AUFTRAG_WAIT_SECONDS = 900
ENTFERNEN_WAIT_SECONDS = 300
POLL_SECONDS = 2

# Waechter (nur die Heizungsbruecke).
WATCHDOG_INTERVAL_SECONDS = 300
STOPPED_AFTER_CHECKS = 2
SILENCE_SECONDS = 900
MAX_RESTARTS_PER_WINDOW = 3
RESTART_WINDOW_SECONDS = 3600
WATCHDOG_MESSAGES = {
    STATUS_ADDON_GESTOPPT: ("SmartHeat: Das Add-on Heizungsbrücke läuft nicht, die Heizungssteuerung ist unterbrochen. "
                            "SmartHeat startet es neu."),
    STATUS_REAGIERT_NICHT: ("SmartHeat: Das Add-on Heizungsbrücke meldet sich seit 15 Minuten nicht, die "
                            "Heizungssteuerung ist unterbrochen. Bitte das Add-on unter Einstellungen → Add-ons "
                            "prüfen; wenn möglich, startet SmartHeat es neu."),
}
WATCHDOG_REASONS = {
    STATUS_ADDON_GESTOPPT: "Add-on läuft nicht: Heizungsbrücke",
    STATUS_REAGIERT_NICHT: "Heizungsbrücke meldet sich seit 15 Minuten nicht",
}

ADDON_REPOSITORY_URL = "https://github.com/LucaHartfuss/SmartHeat-for-HomeAssistant"
HEIZUNGSBRUECKE_ADDON_SLUG = "heizungsbruecke"
ADDON_NAME = "Heizungsbruecke"  # Anzeigename fuer HAs AddonManager und die Logzeilen
MIN_ADDON_VERSIONS: dict[str, str] = {HEIZUNGSBRUECKE_ADDON_SLUG: "1.0.0"}


def slug(tenant_id: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", tenant_id.lower())


def entity_id(platform: str, tenant_id: str, key: str) -> str:
    """Feste Entity-IDs: sensor.smartheat_<slug>_status, unveraendert fuer die Dashboards von client1."""
    return f"{platform}.smartheat_{slug(tenant_id)}_{key}"


def watchdog_notification_id(tenant_id: str) -> str:
    return f"smartheat_{slug(tenant_id)}_addon"


def signal_update(entry_id: str) -> str:
    return f"{DOMAIN}_update_{entry_id}"


def hinweis_issue_id(key: str) -> str:
    return f"hinweis_{re.sub(r'[^a-z0-9_]', '_', key.lower())}"

