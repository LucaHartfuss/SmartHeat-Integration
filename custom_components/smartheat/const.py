"""Konstanten der SmartHeat-Integration: Oberflaeche ueber der Einrichtungs-API des Servers, Status
und Wunschtemperatur ueber das lokale Add-on. Ereignis-, Discovery- und Routen-Werte sind Cross-Repo-Vertrag
(tools/contract_check.py im Dev-Root)."""
import re

DOMAIN = "smartheat"
DEFAULT_BASE_URL = "https://accounts.setwell.de"
OPTION_BOOTSTRAP_URL = "bootstrap_url"
REQUEST_TIMEOUT_SECONDS = 30
ERROR_TEXT_MAX = 300
# Fehlerantworten der Einrichtungs-API: Body hoechstens 64 KiB gelesen, `fehler`-Werte hoechstens 200 Zeichen.
FEHLER_BODY_MAX_BYTES = 65536
FEHLER_WERT_MAX = 200

# Status-Ereignis des Add-ons, Schema 4: Felder der Anlage und je Kreis ein Eintrag in `kreise` (aufsteigend nach
# Kreisnummer). Anlage und Kreis haben je eine eigene Feldliste; was nicht benannt ist, uebernimmt der Coordinator nicht.
STATUS_EVENT = "smartheat_status"
STATUS_EVENT_SCHEMA = 4
STATUS_EVENT_FIELDS = (
    "schema", "tenant_id", "addon_version", "status", "grund", "notbetrieb", "datenfehler", "letzte_serverantwort",
    "hinweise", "geraet_id", "geraet_zustand", "server_verbunden", "geraet_hinweise", "anlage_name", "kreise",
)
STATUS_KREIS_FIELDS = (
    "kreis", "name", "raum_soll", "raum_ist", "einlernphase_tag", "testphase_bis", "befunde", "boost",
    "hebelsatz", "hebel", "gelernt", "abo", "abo_frist_ende", "zustand", "kaelteschutz",
)
# Lebenszeichen der Integration an das Add-on (Vertrag V5). Der Kern -- Name, Typ und Bedeutung dieser drei Felder --
# bleibt in jedem kuenftigen Schema gleich, damit jedes Add-on Version und gelesene Schemata der Integration lesen kann.
# `v5_schemas` nennt die Schemata von `smartheat_status`, die die Integration liest. Keine Zeiten und keine Kennungen.
INTEGRATION_EVENT = "smartheat_integration"
INTEGRATION_EVENT_SCHEMA = 1
INTEGRATION_EVENT_FIELDS = ("schema", "version", "v5_schemas")
V5_SCHEMAS = (STATUS_EVENT_SCHEMA,)
# Nummer des ersten Kreises: Vorgabe fuer Werkzeuge, die genau einen Kreis ansprechen.
ERSTER_KREIS = 1
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
# Versionsbefunde der Integration (nie im Status-Ereignis des Add-ons): das Add-on meldet ein Schema, das die Integration
# nicht liest, weil es hoeher (Integration veraltet) bzw. niedriger (Add-on veraltet) ist als alle gelesenen. Gleichnamig
# sind das Repair, die Uebersetzung der Ablehnung und der Zustand des Status-Sensors; ohne brauchbare Mindestversion
# im Kern des Ereignisses nimmt das Repair den Schluessel mit Endung.
STATUS_INTEGRATION_VERALTET = "integration_veraltet"
STATUS_ADDON_VERALTET = "addon_veraltet"
STATUS_VERSION_VALUES = (STATUS_INTEGRATION_VERALTET, STATUS_ADDON_VERALTET)
ISSUE_OHNE_VERSION = "_ohne_version"
# Ein Code des Add-ons (Schluessel, Stufe, Art, Zustand): kleine Buchstaben, Ziffern, Unterstrich. Was ihm nicht entspricht,
# gilt als freier Text und geht weder in eine Issue-ID noch in die Diagnose.
CODE_RE = re.compile(r"[a-z0-9_]{1,40}")
# Eine Versionsnummer: drei Zahlen, hoechstens eine Endung; kein Domain- oder Pfadmuster, das ein Repair-Text als Link
# darstellen koennte.
MINDEST_INTEGRATION_RE =re.compile(r"(?=.{1,32}\Z)\d{1,6}\.\d{1,6}\.\d{1,6}(?:[.+-][0-9A-Za-z]{1,16})?")
STATUS_SENSOR_VALUES = (
    ADDON_STATUS_VALUES + (STATUS_ADDON_GESTOPPT, STATUS_REAGIERT_NICHT) + STATUS_VERSION_VALUES
)
BOOST_KEINER = "keiner"
BOOST_VALUES = (BOOST_KEINER, "komfort", "notfall")
ABO_VALUES = ("aktiv", "inaktiv", "beendet", "unbekannt")
# Zustand und Kaelteschutz eines Kreises (Codes des Add-ons; die Texte stehen nur in den Uebersetzungen). Die Menge des
# Zustands waechst mit dem Add-on; ein Wert ausserhalb der Menge zeigt die Entity als unbekannt.
KREIS_ZUSTAND_VALUES = (
    "regelt", "abo_inaktiv", "abo_beendet", "testphase", "ausser_betrieb",
    "heizt", "pause", "abwesend", "boost", "notbetrieb", "einlernphase", "nicht_geregelt", "kuehl", "heizt_auf",
    "uebersteuert", "leerstand",
)
KAELTESCHUTZ_VALUES = ("aus", "soll", "frost")
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
# Nachweis: die Integration schreibt `nachweis <geraet_id>`, das Add-on antwortet mit dem Ereignis NACHWEIS_EVENT
# (Felder NACHWEIS_EVENT_FIELDS: Schema, eigene Geraete-ID, Unix-Sekunden, Signatur des Geraeteschluessels ueber den festen
# Pfad der Route `geraetecode`). Kopie von heizungsbruecke/stdin_befehl.py und dem Ereignis des Add-ons (Vertrag V5).
STDIN_BEFEHL_NACHWEIS = "nachweis"
STDIN_ZEILE_FORMAT = "{befehl} {geraet_id}\n"
NACHWEIS_EVENT = "smartheat_nachweis"
NACHWEIS_EVENT_SCHEMA = 1
NACHWEIS_EVENT_FIELDS = ("schema", "geraet_id", "zeitstempel", "signatur")
NACHWEIS_SIGNATUR_MAX = 512
# Koepfe der Anfrage `geraetecode` (dieselben wie beim Bootstrap des Geraets, Vertrag V1).
NACHWEIS_KOPF_GERAET = "X-SHG-Device"
NACHWEIS_KOPF_ZEITSTEMPEL = "X-SHG-Timestamp"
NACHWEIS_KOPF_SIGNATUR = "X-SHG-Signature"
WUNSCH_EVENT = "smartheat_wunsch"
# Daten des Wunsch-Ereignisses: schema, kreis (Pflicht, ab 1) und raum_soll; ohne gueltigen Kreis verwirft das Add-on es.
WUNSCH_EVENT_SCHEMA = 4
RAUM_SOLL_MIN, RAUM_SOLL_MAX, RAUM_SOLL_STEP = 15.0, 25.0, 0.5
# Grenzen der Auswahl im Assistenten: Kopie von heizungsserver einrichtung/ha.py MAX_RAUMFUEHLER,
# MAX_BATTERIEN, MAX_HANDYS (V3, Contract-Check 76); mehr lehnt der Server mit 400 ab.
EINRICHTUNG_MAX_RAUMFUEHLER = 8
EINRICHTUNG_MAX_BATTERIEN = 16
EINRICHTUNG_MAX_HANDYS = 10
# Kopie von device_protocol.KREISE_MAX (Heizkreise je Geraet; Contract-Check 76).
EINRICHTUNG_MAX_KREISE = 4
# Anzeigename von Geraet und Hersteller; laengster uebernommener Name (Anlage, Heizkreis-Abo) in Zeichen.
PRODUKT_NAME = "SmartHeat"
GERAET_NAME_MAX = 100

# Einrichtungs-API (Server einrichtung/api.py ROUTEN).
EINRICHTUNG_PREFIX = "/einrichtung/v1"
EINRICHTUNG_ROUTEN: dict[str, tuple[str, str]] = {
    "geraetecode": ("POST", "/geraetecode"),
    "geraetecode_token": ("POST", "/geraetecode/token"),
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
    "geraetecode": ((), ()),
    "geraetecode_token": (("geraetecode",), ()),
    "abmelden": ((), ()),
    "uebernehmen": (("device_id",), ("code",)),
    "geraet": ((), ()),
    "inventur": ((), ()),
    "vorschlag": ((), ()),
    "zuordnung": (("kreis", "profil", "hebelsatz"), ("tenant_id",)),
    "pruefen": (("profil", "hebelsatz", "kreise"),
                ("batterien", "benachrichtigung", "hinweise_aus", "tenant_id", "ersetzen")),
    "einrichten": (("profil", "hebelsatz", "kreise", "tenant_id"),
                   ("batterien", "benachrichtigung", "hinweise_aus", "ersetzen")),
    "neu_konfigurieren": (("profil", "hebelsatz", "kreise"),
                          ("batterien", "benachrichtigung", "hinweise_aus")),
    "entfernen": ((), ()),
    "auftrag": ((), ()),
}
EINRICHTUNG_ANTWORT_FELDER: dict[str, tuple[str, ...]] = {
    "geraetecode": ("geraetecode", "anmeldecode", "link", "link_komplett", "gueltig_s", "abfrage_s"),
    "geraetecode_token": ("zustand", "abfrage_s", "token", "gueltig_bis"),
    "abmelden": (),
    "uebernehmen": ("device_id",),
    "geraet": ("device_id", "host", "eingerichtet", "tenant_id", "anlagen", "inventur", "auftrag", "soll_bereich",
               "abos", "konfiguration", "raum_soll"),
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
EINRICHTUNG_VORSCHLAG_INTEGRATION_FELDER = ("plattform", "weg", "label", "hinweis", "kreise", "profile", "vorschlag",
                                            "rang", "einstufung", "nur_erkennen")
# Verschachtelte Objekte eines Integrationseintrags der Antwort `vorschlag` (kreise, profile, hebelsaetze, vorschlag).
EINRICHTUNG_VORSCHLAG_KREIS_FELDER = ("schluessel", "label", "nummer")
EINRICHTUNG_VORSCHLAG_PROFIL_FELDER = ("profile_id", "label", "erzeuger_typ", "verteilsystem", "shift_lever",
                                       "hebelsaetze")
EINRICHTUNG_VORSCHLAG_HEBELSATZ_FELDER = ("id", "hebel")
EINRICHTUNG_VORSCHLAG_AUSWAHL_FELDER = ("kreise", "profil")
# Verschachtelte Objekte der Antwort `geraet`, die der Assistent liest (Eintrag von `anlagen`, `inventur`, Zusatzfelder
# der `konfiguration`).
EINRICHTUNG_GERAET_ANLAGE_FELDER = ("tenant_id", "aktiv", "belegt", "ersetzbar")
EINRICHTUNG_GERAET_INVENTUR_FELDER = ("zustand", "command_id", "zeit", "fehler")
EINRICHTUNG_GERAET_KONFIGURATION_ZUSATZ = ("hebel", "shift_lever", "kreise")
EINRICHTUNG_GERAET_KREIS_FELDER = ("nummer", "bindung", "raumfuehler", "raum_soll")
EINRICHTUNG_GERAET_ABO_FELDER = ("abo_id", "name")
# Aufbau der Kreise und Raeume der Anfragen `pruefen`, `einrichten`, `neu_konfigurieren` (Pflicht, optional).
EINRICHTUNG_ANFRAGE_KREIS_FELDER = (("nummer", "kreis", "rollen", "raeume"), ("raum_soll", "abo_id"))
EINRICHTUNG_ANFRAGE_RAUM_FELDER = (("name", "rolle", "fuehler"), ("soll_quelle", "versatz_k", "ziel_fest"))
# Zustaende der Route `geraetecode_token` (Antwort `zustand`) und die Zustaende, die jede Route als Antwort tragen kann.
GERAETECODE_ZUSTAND_WARTET = "wartet"
GERAETECODE_ZUSTAND_BESTAETIGT = "bestaetigt"
GERAETECODE_ZUSTAND_ABGELEHNT = "abgelehnt"
GERAETECODE_ZUSTAND_ABGELAUFEN = "abgelaufen"
GERAETECODE_ZUSTAND_LANGSAMER = "langsamer"
GERAETECODE_ZUSTAENDE = (GERAETECODE_ZUSTAND_WARTET, GERAETECODE_ZUSTAND_BESTAETIGT, GERAETECODE_ZUSTAND_ABGELEHNT,
                         GERAETECODE_ZUSTAND_ABGELAUFEN, GERAETECODE_ZUSTAND_LANGSAMER)
ZUSTAND_ERNEUT_ANMELDEN = "erneut_anmelden"
ZUSTAND_PORTAL_BESTAETIGUNG = "portal_bestaetigung"
# Anmeldecode wie ihn das Portal anzeigt (Alphabet ohne Zeichen, die sich verwechseln lassen), Link nur https ohne
# Zeichen, die Markdown oder die Anzeige umbrechen; Zeiten des Servers sind Sekunden in sinnvollen Grenzen.
ANMELDECODE_RE = re.compile(r"[BCDFGHJKLMNPQRSTVWXZ]{4}-[BCDFGHJKLMNPQRSTVWXZ]{4}")
LINK_RE = re.compile(r"https://[^\s()\[\]<>\"'`\\]+")
GERAETECODE_MAX_ZEICHEN = 256
GERAETECODE_SEKUNDEN_MAX = 3600
AUFTRAG_FERTIG = "fertig"
AUFTRAG_FEHLGESCHLAGEN = "fehlgeschlagen"

# Warten auf den Nachweis des Add-ons; Polling des Geraetecodes: Takt hoechstens 60 s, Wartezeit des Codes hoechstens 900 s
# (der Server nennt 600 s), nach `langsamer` mindestens 5 s mehr, so viele Netzfehler hintereinander werden uebergangen.
NACHWEIS_WAIT_SECONDS = 15
ABFRAGE_MAX_SECONDS = 60
ABFRAGE_ZUWACHS_SECONDS = 5
CODE_WAIT_MAX_SECONDS = 900
ABFRAGE_FEHLER_MAX = 3
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
                            "Bitte das Add-on unter Einstellungen → Add-ons prüfen; wenn möglich, startet SmartHeat "
                            "es neu."),
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


def entity_id(platform: str, key: str, kreis: int | None = None) -> str:
    """Feste Entity-IDs ohne Anlagennamen: <plattform>.smartheat_<schluessel> fuer die Anlage,
    <plattform>.smartheat_k<n>_<schluessel> fuer einen Kreis."""
    return f"{platform}.smartheat_{'' if kreis is None else f'k{kreis}_'}{key}"


def unique_id(tenant_id: str, key: str, kreis: int | None = None) -> str:
    """Stabile Kennung einer Entity aus Anlage, Kreis und Schluessel; die Registry haelt an ihr fest, auch wenn die
    Entity-ID geaendert wird."""
    return f"{tenant_id}_{'' if kreis is None else f'k{kreis}_'}{key}"


def watchdog_notification_id(tenant_id: str) -> str:
    return f"smartheat_{slug(tenant_id)}_addon"


def signal_update(entry_id: str) -> str:
    return f"{DOMAIN}_update_{entry_id}"


def hinweis_issue_id(key: str) -> str:
    return f"hinweis_{re.sub(r'[^a-z0-9_]', '_', key.lower())}"

