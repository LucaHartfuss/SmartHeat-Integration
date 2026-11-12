"""Assistent der SmartHeat-Integration: feste Schritte, Inhalte vom Server.

Start aus der Supervisor-Discovery des Add-ons (Geraete-ID, Uebernahme-Code) oder manuell (Add-on installieren und
starten, dann dessen Discovery lesen); beide Wege pruefen vorher die Mindestversion des Add-ons. Die Anmeldung braucht
kein Passwort: der Assistent fordert beim Add-on den Besitznachweis des Geraets an (Schritt `anfordern`), holt damit beim
Server einen Anmeldecode, zeigt Code und Link (Schritt `warten`, der Kunde bestaetigt im Portal) und fragt im Takt des
Servers nach, bis das kurzlebige Einrichtungs-Token da ist. Das Token haelt nur dieser Flow im Speicher (Regel 6); es wird
bei jedem Ende (Eintrag, Abbruch, Schliessen) beim Server beendet. Inventur, Vorschlag, Zuordnung und Pruefung laufen auf
dem Server, die Uebernahme der Konfiguration im Geraet; der Assistent zeigt nur an. "Neu konfigurieren": nach neuer
Anmeldung ein Menue "Zuordnung aendern" (Auftrag neu_konfigurieren) bzw. "Geraet entfernen" (Auftrag entfernen).

Fehler der Einrichtungs-API: abgelaufenes Token oder zu alte Bestaetigung (`erneut_anmelden`) -> neuer Ablauf mit neuem
Code (Formular `anmelden` mit Hinweis); abgelaufener Code ebenso, abgelehnter Code oder fehlender Nachweis -> Abbruch;
`portal_bestaetigung` -> Abbruch mit eigenem Text; Feldfehler der Pruefung -> zurueck ins
Formular mit dem Feld; nicht erreichbar -> Abbruch cannot_connect; alles andere -> Abbruch mit dem Text des Servers.
Mehr als 8 Raumfuehler, 16 Batterien oder 10 Handys markiert der Assistent selbst (`zu_viele`), ohne Kandidaten fuer
Raumfuehler oder Raum-Soll bricht er ab. Antwortet das Geraet beim "Geraet entfernen" nicht, ist der Eintrag trotzdem
entfernt. Vom Supervisor wird nie ein Fehlertext gezeigt oder geloggt (er kann Optionswerte zitieren), nur die
Fehlerart."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import voluptuous as vol
from homeassistant.components.hassio import AddonError
from homeassistant.config_entries import SOURCE_HASSIO, ConfigFlow, ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.hassio import is_hassio
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from homeassistant.loader import async_get_integration

from . import addon_control, supervisor_client, vertrag_pin
from . import anfrage as v3_anfrage
from .const import (
    ABFRAGE_FEHLER_MAX,
    ABFRAGE_MAX_SECONDS,
    ABFRAGE_ZUWACHS_SECONDS,
    AUFTRAG_FEHLGESCHLAGEN,
    AUFTRAG_FERTIG,
    AUFTRAG_WAIT_SECONDS,
    CODE_WAIT_MAX_SECONDS,
    DEFAULT_BASE_URL,
    DISCOVERY_FIELDS,
    DISCOVERY_WAIT_SECONDS,
    DOMAIN,
    EINRICHTUNG_ANTWORT_FELDER,
    EINRICHTUNG_GERAET_ABO_FELDER,
    EINRICHTUNG_GERAET_ANLAGE_FELDER,
    EINRICHTUNG_GERAET_INVENTUR_FELDER,
    EINRICHTUNG_GERAET_KONFIGURATION_ZUSATZ,
    EINRICHTUNG_GERAET_KREIS_FELDER,
    EINRICHTUNG_MAX_BATTERIEN,
    EINRICHTUNG_MAX_HANDYS,
    EINRICHTUNG_MAX_KREISE,
    EINRICHTUNG_MAX_RAUMFUEHLER,
    EINRICHTUNG_RAUMFUEHLER_FELDER,
    EINRICHTUNG_VORSCHLAG_AUSWAHL_FELDER,
    EINRICHTUNG_VORSCHLAG_HEBELSATZ_FELDER,
    EINRICHTUNG_VORSCHLAG_INTEGRATION_FELDER,
    EINRICHTUNG_VORSCHLAG_KREIS_FELDER,
    EINRICHTUNG_VORSCHLAG_PROFIL_FELDER,
    EINRICHTUNG_ZUORDNUNG_ROLLE_FELDER,
    ENTFERNEN_WAIT_SECONDS,
    GERAET_ID_RE,
    GERAETECODE_ZUSTAND_ABGELAUFEN,
    GERAETECODE_ZUSTAND_ABGELEHNT,
    GERAETECODE_ZUSTAND_BESTAETIGT,
    GERAETECODE_ZUSTAND_LANGSAMER,
    HEIZUNGSBRUECKE_ADDON_SLUG,
    INVENTUR_WAIT_SECONDS,
    MIN_ADDON_VERSIONS,
    NACHWEIS_WAIT_SECONDS,
    POLL_SECONDS,
    RAUM_SOLL_MAX,
    RAUM_SOLL_MIN,
    RAUM_SOLL_STEP,
)
from .einrichtung_client import (
    ApiError,
    CannotConnect,
    Conflict,
    EinrichtungClient,
    ErneutAnmelden,
    InvalidAuth,
    InvalidResponse,
    NotFound,
    PortalBestaetigung,
    Rejected,
    ServerError,
    TokenUngueltig,
    TooMany,
)
from .flow_progress import ProgressFlowMixin
from .texts import async_hint, async_role_label, klartext

_LOGGER = logging.getLogger(__name__)

# Abbruchgruende von async_bootstrap_url, die schon vor dem Installieren der Heizungsbruecke gelten
_VOR_DER_INSTALLATION = frozenset({"repository_mehrdeutig", "fremde_heizungsbruecke"})
_RAEUME = ("raumfuehler", "room_target")
# Felder des Schritts `raeume` und die der ganzen Anlage (Schritt `benachrichtigung`)
_KREIS_FELDER = ("raum_name", "raumfuehler", "room_target", "raum_soll", "kontroll_name", "kontroll_fuehler")
_ANLAGENWEIT = ("batterien", "benachrichtigung", "hinweise_aus")
# Obergrenzen der Auswahl je Feld (der Server lehnt mehr ab)
_MAXIMUM = {"batterien": EINRICHTUNG_MAX_BATTERIEN, "benachrichtigung": EINRICHTUNG_MAX_HANDYS}


def _fk(nummer: int, feld: str) -> str:
    """Fehlerschluessel des Servers: Kreis 1 mit dem Feldnamen, ein weiterer Kreis mit `k<Nummer>.` davor."""
    return feld if nummer == 1 else f"k{nummer}.{feld}"


_INVENTUR_FERTIG = "fertig"
_INVENTUR_FEHLGESCHLAGEN = "fehlgeschlagen"
# Pin: je Objekt der Einrichtungs-Antworten die Literale, die dieser Assistent liest, gegen die
# Konstanten des Vertrags (const.EINRICHTUNG_*_FELDER). Liest der Assistent ein Feld, das der Vertrag nicht nennt, ist
# das ein RuntimeError beim Import. Felder des V1-Dokuments in `konfiguration` (bindung, profil, hebelsatz) gehoeren
# zu dessen eigenem Vertrag und stehen hier nicht.
_VERTRAG_GEPINNT = {
    "ANTWORT_FELDER[geraet]": (EINRICHTUNG_ANTWORT_FELDER["geraet"],
                               ("eingerichtet", "tenant_id", "anlagen", "abos", "inventur", "konfiguration")),
    "ANTWORT_FELDER[vorschlag]": (EINRICHTUNG_ANTWORT_FELDER["vorschlag"], ("integrationen",)),
    "ANTWORT_FELDER[zuordnung]": (EINRICHTUNG_ANTWORT_FELDER["zuordnung"],
                                  ("rollen", "raumfuehler", "room_target", "batterie_je_entity", "benachrichtigung",
                                   "hinweise", "raum_soll")),
    "ANTWORT_FELDER[pruefen]": (EINRICHTUNG_ANTWORT_FELDER["pruefen"], ("fehler", "warnungen")),
    "ANTWORT_FELDER[entfernen]": (EINRICHTUNG_ANTWORT_FELDER["entfernen"], ("freigegeben", "job_id")),
    "ANTWORT_FELDER[auftrag]": (EINRICHTUNG_ANTWORT_FELDER["auftrag"], ("zustand", "fehler")),
    "GERAET_ANLAGE_FELDER": (EINRICHTUNG_GERAET_ANLAGE_FELDER, ("tenant_id", "aktiv", "belegt", "ersetzbar")),
    "GERAET_ABO_FELDER": (EINRICHTUNG_GERAET_ABO_FELDER, ("abo_id", "name")),
    "GERAET_KREIS_FELDER": (EINRICHTUNG_GERAET_KREIS_FELDER, ("nummer", "bindung")),
    "GERAET_INVENTUR_FELDER": (EINRICHTUNG_GERAET_INVENTUR_FELDER, ("zustand", "fehler")),
    "GERAET_KONFIGURATION_ZUSATZ": (EINRICHTUNG_GERAET_KONFIGURATION_ZUSATZ, ("hebel", "shift_lever", "kreise")),
    "VORSCHLAG_INTEGRATION_FELDER": (EINRICHTUNG_VORSCHLAG_INTEGRATION_FELDER,
                                     ("plattform", "label", "hinweis", "kreise", "profile", "vorschlag")),
    "VORSCHLAG_KREIS_FELDER": (EINRICHTUNG_VORSCHLAG_KREIS_FELDER, ("schluessel", "label", "nummer")),
    "VORSCHLAG_PROFIL_FELDER": (EINRICHTUNG_VORSCHLAG_PROFIL_FELDER, ("profile_id", "label", "hebelsaetze")),
    "VORSCHLAG_HEBELSATZ_FELDER": (EINRICHTUNG_VORSCHLAG_HEBELSATZ_FELDER, ("id",)),
    "VORSCHLAG_AUSWAHL_FELDER": (EINRICHTUNG_VORSCHLAG_AUSWAHL_FELDER, ("kreise", "profil")),
    "ZUORDNUNG_ROLLE_FELDER": (EINRICHTUNG_ZUORDNUNG_ROLLE_FELDER, ("rolle", "pflicht", "vorschlag", "kandidaten")),
    "RAUMFUEHLER_FELDER": (EINRICHTUNG_RAUMFUEHLER_FELDER, ("kandidaten", "vorschlag")),
}
vertrag_pin.enthalten(__name__, _VERTRAG_GEPINNT)


class _Unterbrechung(Exception):
    """Ein Aufruf der Einrichtungs-API hat den normalen Weg verlassen; `_unterbrechung` entscheidet, wie weiter."""


class _NeuAnmelden(_Unterbrechung):
    """Token abgelaufen oder widerrufen (`sitzung_abgelaufen`) oder die Bestaetigung im Portal ist zu alt
    (`erneut_anmelden`): `grund` ist der Fehlerschluessel des Formulars."""

    def __init__(self, grund: str) -> None:
        super().__init__(grund)
        self.grund = grund


class _Feldfehler(_Unterbrechung):
    def __init__(self, fehler: dict[str, str], text: str = "") -> None:
        super().__init__("feldfehler")
        self.fehler = fehler
        self.text = text


class _Abbruch(_Unterbrechung):
    def __init__(self, reason: str, **placeholders: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.placeholders = placeholders


async def _pause(sekunden: float) -> None:
    """Wartezeit des Pollings (die Tests ersetzen sie)."""
    await asyncio.sleep(sekunden)


def _discovery(config: Any) -> dict[str, str]:
    """Discovery-Felder des Add-ons; nur nicht leere Texte, die Geraete-ID nur im Format des Servers (sonst fehlt sie:
    sie wird unique_id und steht im Dialog)."""
    if not isinstance(config, Mapping):
        return {}
    result = {key: value for key in DISCOVERY_FIELDS if isinstance(value := config.get(key), str) and value}
    if not GERAET_ID_RE.fullmatch(result.get("geraet_id", "")):
        result.pop("geraet_id", None)
    return result


def _text(value: Any) -> str:
    return value if isinstance(value, str) and value else "-"


def _md(value: Any) -> str:
    """Ein Wert fuer eine Beschreibung (Markdown), der nicht von uns stammt: maskiert, bei leerem Wert ein Strich.
    Jeder solche Platzhalter geht hier durch."""
    return klartext(value) or "-"


def _entities(kandidaten: list[str], *, multiple: bool = False) -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(include_entities=list(kandidaten), multiple=multiple))


def _auswahl(options: list, *, multiple: bool = False, translation_key: str | None = None) -> selector.SelectSelector:
    config = selector.SelectSelectorConfig(options=options, multiple=multiple, mode=selector.SelectSelectorMode.DROPDOWN)
    if translation_key is not None:
        config["translation_key"] = translation_key
    return selector.SelectSelector(config)


def _vorbelegt(key: str, default: Any, *, required: bool = True) -> vol.Marker:
    """Feld mit Vorbelegung, ohne Vorbelegung ohne `default` (voluptuous kennt kein "leer")."""
    if required:
        return vol.Required(key, default=default) if default not in (None, "", []) else vol.Required(key)
    return vol.Optional(key, description={"suggested_value": default})


class SmartHeatConfigFlow(ProgressFlowMixin, ConfigFlow, domain=DOMAIN):
    # 3 = Eintrag ab 1.0.0 (Geraete-ID).
    VERSION = 3

    def __init__(self) -> None:
        self._client: EinrichtungClient | None = None
        self._discovery: dict[str, str] = {}
        self._auto_update_gesetzt = False  # der Assistent hat die automatischen Updates des Add-ons eingeschaltet
        self._geraet: dict = {}
        self._vorschlag: dict = {}
        self._integration: dict = {}
        self._auswahl: dict = {}
        self._kreise: list[dict] = []
        self._index = 0
        self._eingaben: dict = {}
        self._tenant: str | None = None
        self._code: dict | None = None
        self._server: str | None = None
        self._ersetzen = False
        self._reconfigure = False
        self._fehler: dict[str, str] = {}
        self._offen: _Unterbrechung | None = None

    # --- Ende: das Token immer beenden ---

    @callback
    def async_remove(self) -> None:
        """Dialog geschlossen: das Token beim Server beenden (best effort)."""
        if self._client is not None and self._client.token:
            self.hass.async_create_task(self._client.abmelden(), "smartheat_einrichtung_abmelden")

    async def _abmelden(self) -> None:
        if self._client is not None:
            await self._client.abmelden()

    async def _abbrechen(self, reason: str, **placeholders: str) -> ConfigFlowResult:
        """Abbruch; Texte des Servers gehen vorher durch `_md` (eigene Texte bleiben, wie sie sind)."""
        await self._abmelden()
        return self.async_abort(reason=reason, description_placeholders=placeholders or None)

    async def _api(self, call: Callable[..., Awaitable[Any]], *args: Any, konflikt: str = "abgelehnt",
                   veraltet: str | None = None, **kwargs: Any) -> Any:
        """Aufruf mit festem Token; Fehler werden zur passenden Unterbrechung (siehe Modul-Doku). `veraltet`: Abbruchgrund,
        wenn die Adresse die Route nicht kennt (404 oder 405: ein Server ohne Geraetecode)."""
        try:
            return await call(*args, **kwargs)
        except TokenUngueltig:
            raise _NeuAnmelden("sitzung_abgelaufen") from None
        except ErneutAnmelden:
            raise _NeuAnmelden("erneut_anmelden") from None
        except PortalBestaetigung:
            raise _Abbruch("portal_bestaetigung") from None
        except InvalidAuth:
            # 401 ohne Token: der Server nimmt den Nachweis nicht an (Geraet unbekannt, gesperrt, Uhr falsch, Wiederholung)
            raise _Abbruch("nachweis_abgelehnt") from None
        except (CannotConnect, ServerError) as error:
            _LOGGER.info("Einrichtung: Server nicht erreichbar (%s)", type(error).__name__)
            raise _Abbruch("cannot_connect") from None
        except Rejected as error:
            if error.fehler:
                raise _Feldfehler(error.fehler, error.text) from None
            raise _Abbruch("abgelehnt", grund=_md(error.text)) from None
        except NotFound as error:
            if veraltet is not None:
                raise _Abbruch(veraltet) from None
            raise _Abbruch("geraet_unbekannt", grund=_md(error.text)) from None
        except Conflict as error:
            raise _Abbruch(konflikt, grund=_md(error.text)) from None
        except InvalidResponse:
            # Text ist eine interne Diagnose, kein Text des Servers
            _LOGGER.warning("Einrichtung: unbrauchbare Antwort des Servers")
            raise _Abbruch("unknown") from None
        except ApiError as error:
            if veraltet is not None and error.status == 405:
                raise _Abbruch(veraltet) from None
            raise _Abbruch("abgelehnt", grund=_md(error.text)) from None

    def _merken(self, unterbrechung: _Unterbrechung) -> str:
        """Unterbrechung aus einem Fortschritts-Job: im naechsten Schritt `unterbrechung` behandeln."""
        self._offen = unterbrechung
        return "unterbrechung"

    async def async_step_unterbrechung(self, user_input: dict | None = None) -> ConfigFlowResult:
        offen, self._offen = self._offen, None
        return await self._unterbrechung(offen or _Abbruch("unknown"))

    async def _unterbrechung(self, unterbrechung: _Unterbrechung) -> ConfigFlowResult:
        if isinstance(unterbrechung, _NeuAnmelden):
            await self._abmelden()
            self._code = None
            return self._anmelden_formular({"base": unterbrechung.grund})
        if isinstance(unterbrechung, _Feldfehler):
            self._fehler = dict(unterbrechung.fehler)
            if self._fehler_ohne_feld():
                # Kein Formular zeigt das Feld (z. B. kreis, tenant_id): Zurueckschicken liefe im Kreis.
                grund = unterbrechung.text or ", ".join(f"{k}: {v}" for k, v in self._fehler.items())
                return await self._abbrechen("abgelehnt", grund=_md(grund))
            return await self._formular_mit_fehlern()
        assert isinstance(unterbrechung, _Abbruch)
        return await self._abbrechen(unterbrechung.reason, **unterbrechung.placeholders)

    async def _progress_error_step(self) -> str:
        return self._merken(_Abbruch("unknown"))

    # --- Start ---

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        """Nur die Discovery der Heizungsbruecke aus dem SmartHeat-Repository: jedes Add-on mit `discovery: [smartheat]`
        kann eine schicken, und eine fremde Geraete-ID liesse den Nutzer ein fremdes Geraet uebernehmen."""
        config = _discovery(discovery_info.config)
        if "geraet_id" not in config or not await self._von_der_heizungsbruecke(discovery_info.slug):
            return self.async_abort(reason="discovery_ungueltig")
        await self.async_set_unique_id(config["geraet_id"])
        self._abort_if_unique_id_configured()
        # Meldet die Heizungsbruecke ein anderes Geraet (etwa nach einer Neuinstallation), ist der offene Flow des alten
        # tot: er wuerde dessen Code und Geraete-ID anzeigen.
        for offen in self._async_in_progress(include_uninitialized=True):
            kontext = offen.get("context") or {}
            if kontext.get("source") == SOURCE_HASSIO and kontext.get("unique_id") != config["geraet_id"]:
                self.hass.config_entries.flow.async_abort(offen["flow_id"])
        self._discovery = config
        self.context["title_placeholders"] = {"geraet": config["geraet_id"]}
        return await self.async_step_bestaetigen()

    async def _von_der_heizungsbruecke(self, slug: str) -> bool:
        try:
            eigener = await addon_control.async_bridge_slug(self.hass)
        except (AddonError, KeyError) as error:
            _LOGGER.warning("Einrichtung: Herkunft der Discovery nicht pruefbar (%s)", type(error).__name__)
            return False
        if eigener is None or slug != eigener:
            _LOGGER.warning("Einrichtung: Discovery eines anderen Add-ons verworfen")
            return False
        return True

    async def async_step_bestaetigen(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="bestaetigen",
                                        description_placeholders={"geraet": self._discovery["geraet_id"]})
        if (reason := await self._addon_version_fehler()) is not None:
            return self.async_abort(reason=reason)
        return await self.async_step_anmelden()

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        if not is_hassio(self.hass):
            return self.async_abort(reason="kein_supervisor")
        return await self.async_step_addon_starten()

    async def async_step_addon_starten(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._run_progress("addon_starten", "addon_starten", self._addon_starten)

    async def _addon_starten(self) -> str:
        # Vor der Installation: neben einem mehrdeutigen Repository oder einer fremden Heizungsbruecke wird keine
        # zweite Bruecke installiert; die uebrigen Gruende (Option) pruefen erst nach dem Start.
        if (grund := (await addon_control.async_bootstrap_url(self.hass)).grund) in _VOR_DER_INSTALLATION:
            return self._merken(_Abbruch(grund))
        try:
            gestartet = await addon_control.async_install_and_start(self.hass)
            self._auto_update_gesetzt = gestartet.auto_update_gesetzt
        except AddonError as error:
            _LOGGER.warning("Einrichtung: Add-on nicht installiert oder gestartet (%s)", type(error).__name__)
            return self._merken(_Abbruch("addon_fehler"))
        if (reason := await self._addon_version_fehler()) is not None:
            return self._merken(_Abbruch(reason))
        if (grund := (await addon_control.async_bootstrap_url(self.hass)).grund) is not None:
            return self._merken(_Abbruch(grund))
        deadline = time.monotonic() + DISCOVERY_WAIT_SECONDS
        while True:
            found = _discovery(await addon_control.async_discovery(self.hass))
            if "geraet_id" in found:
                self._discovery = found
                return "nach_discovery"
            if time.monotonic() >= deadline:
                return self._merken(_Abbruch("keine_discovery"))
            await asyncio.sleep(POLL_SECONDS)

    async def _addon_version_fehler(self) -> str | None:
        """Mindestversion der Heizungsbruecke (aelter meldet sich nie per Discovery); None = in Ordnung."""
        try:
            await supervisor_client.async_resolve_bridge(
                self.hass, MIN_ADDON_VERSIONS[HEIZUNGSBRUECKE_ADDON_SLUG])
        except supervisor_client.AddonOutdatedError:
            return "addon_veraltet"
        except supervisor_client.AmbiguousAddonMatchError:
            return "addon_mehrdeutig"
        except (supervisor_client.AddonNotFoundError, AddonError, KeyError) as error:
            # KeyError: hassio nicht geladen (get_supervisor_client)
            _LOGGER.warning("Einrichtung: Add-on-Version nicht pruefbar (%s)", type(error).__name__)
            return "addon_fehler"
        return None

    async def async_step_nach_discovery(self, user_input: dict | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(self._discovery["geraet_id"], raise_on_progress=False)
        self._abort_if_unique_id_configured()
        self.context["title_placeholders"] = {"geraet": self._discovery["geraet_id"]}
        return await self.async_step_anmelden()

    # --- Anmelden mit Geraetecode und Uebernehmen ---

    def _anmelden_formular(self, errors: dict[str, str], grund: str = "") -> ConfigFlowResult:
        """Formular ohne Eingabefelder: es sagt, warum ein neuer Code noetig ist (`grund`: Text des Servers, roh);
        Absenden beginnt den Ablauf neu."""
        return self.async_show_form(step_id="anmelden", errors=errors, description_placeholders={"grund": _md(grund)})

    async def async_step_anmelden(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Beginn eines Ablaufs (auch nach dem Formular mit Fehler): Adresse des Servers bestimmen, dann Nachweis und
        Code anfordern."""
        if self._client is None:
            bootstrap = await addon_control.async_bootstrap_url(self.hass)
            if bootstrap.url is None:
                return self.async_abort(reason=bootstrap.grund or "bootstrap_url_ungueltig")
            integration = await async_get_integration(self.hass, DOMAIN)
            self._client = EinrichtungClient(async_get_clientsession(self.hass), bootstrap.url,
                                             self.hass.config.language, str(integration.version))
            self._server = bootstrap.url
        return await self.async_step_anfordern()

    async def async_step_anfordern(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._run_progress("anfordern", "anfordern", self._anfordern)

    async def _anfordern(self) -> str:
        """Nachweis beim Add-on holen und damit den Anmeldecode beim Server anfordern."""
        assert self._client is not None
        try:
            nachweis = await addon_control.async_nachweis_anfordern(
                self.hass, self._discovery["geraet_id"], NACHWEIS_WAIT_SECONDS)
        except addon_control.NachweisFehlt:
            return self._merken(_Abbruch("nachweis_fehlt"))
        try:
            self._code = await self._api(self._client.geraetecode_anfordern, nachweis, veraltet="server_veraltet")
        except _Unterbrechung as unterbrechung:
            return self._merken(unterbrechung)
        return "warten"

    async def async_step_warten(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Code und Link stehen im Fortschrittstext; weicht die Adresse des Servers von der Produktion ab, nennt der Text
        sie (der Kunde hat sie als Option des Add-ons gesetzt)."""
        code = self._code
        assert code is not None and self._server is not None
        platzhalter = {"anmeldecode": code["anmeldecode"], "link": code["link"], "link_komplett": code["link_komplett"]}
        aktion = "warten"
        if self._server != DEFAULT_BASE_URL:
            platzhalter["server"], aktion = self._server, "warten_eigener_server"
        return await self._run_progress("warten", aktion, self._warten, platzhalter)

    async def _warten(self) -> str:
        """Im Takt des Servers nach dem Token fragen, bis der Kunde bestaetigt oder abgelehnt hat oder der Code ablaeuft.
        Der Takt kommt aus der Antwort (nach oben begrenzt), `langsamer` verlaengert ihn immer; einzelne Netzfehler werden
        uebergangen, erst `ABFRAGE_FEHLER_MAX` hintereinander brechen ab."""
        assert self._client is not None and self._code is not None
        code = self._code
        abfrage = code["abfrage_s"]
        ende = time.monotonic() + min(code["gueltig_s"], CODE_WAIT_MAX_SECONDS)
        fehler = 0
        try:
            while True:
                await _pause(min(abfrage, ABFRAGE_MAX_SECONDS))
                try:
                    stand = await self._api(self._client.geraetecode_abholen, code["geraetecode"],
                                            veraltet="server_veraltet")
                except _Abbruch as abbruch:
                    fehler += 1
                    if abbruch.reason != "cannot_connect" or fehler >= ABFRAGE_FEHLER_MAX:
                        raise
                else:
                    fehler = 0
                    zustand, genannt = stand["zustand"], stand.get("abfrage_s")
                    if zustand == GERAETECODE_ZUSTAND_BESTAETIGT:
                        return "uebernehmen"
                    if zustand == GERAETECODE_ZUSTAND_ABGELEHNT:
                        raise _Abbruch("code_abgelehnt")
                    if zustand == GERAETECODE_ZUSTAND_ABGELAUFEN:
                        return "code_abgelaufen"
                    if zustand == GERAETECODE_ZUSTAND_LANGSAMER:
                        abfrage = max(genannt or 0, abfrage + ABFRAGE_ZUWACHS_SECONDS)
                    elif genannt is not None:
                        abfrage = genannt
                if time.monotonic() >= ende:
                    return "code_abgelaufen"
        except _Unterbrechung as unterbrechung:
            return self._merken(unterbrechung)

    async def async_step_code_abgelaufen(self, user_input: dict | None = None) -> ConfigFlowResult:
        self._code = None
        return self._anmelden_formular({"base": "code_abgelaufen"})

    async def async_step_uebernehmen(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Das Token ist da: das Geraet uebernehmen (ein eigenes, schon uebernommenes Geraet: ohne Fehler erneut). Ein
        Fehler steht im Formular `anmelden` (neuer Ablauf mit neuem Code); _Abbruch, wenn das Add-on das Geraet nicht
        (mehr) meldet."""
        try:
            fehler = await self._uebernehmen()
        except _Abbruch as abbruch:
            return await self._unterbrechung(abbruch)
        if fehler is not None:
            await self._abmelden()
            return self._anmelden_formular({"base": fehler[0]}, fehler[1])
        return await self.async_step_geraet()

    async def _uebernehmen(self) -> tuple[str, str] | None:
        """(Fehlerschluessel des Formulars, Text des Servers) oder None."""
        assert self._client is not None
        code = await self._uebernahme_code()
        try:
            await self._client.uebernehmen(self._discovery["geraet_id"], code)
        except TokenUngueltig:
            return "sitzung_abgelaufen", ""
        except ErneutAnmelden:
            return "erneut_anmelden", ""
        except PortalBestaetigung:
            raise _Abbruch("portal_bestaetigung") from None
        except TooMany as error:
            return "uebernahme_gesperrt", error.text
        except Conflict as error:
            return "anmeldung_gebunden", error.text
        except (CannotConnect, ServerError):
            return "cannot_connect", ""
        except InvalidResponse:
            return "unknown", ""
        except ApiError as error:
            return "uebernahme_fehlgeschlagen", error.text
        return None

    async def _uebernahme_code(self) -> str | None:
        """Code nur aus der aktuellen Discovery der Heizungsbruecke (async_discovery liest nur deren Nachrichten),
        nie aus der ersten Meldung, die den Flow gestartet hat. Meldet das Add-on kein oder ein anderes Geraet:
        Abbruch. Neu konfigurieren uebernimmt ein eigenes Geraet ohne Code."""
        if self._reconfigure:
            return None
        frisch = _discovery(await addon_control.async_discovery(self.hass))
        if frisch.get("geraet_id") != self._discovery["geraet_id"]:
            _LOGGER.warning("Einrichtung: das Add-on meldet dieses Geraet nicht (mehr)")
            raise _Abbruch("geraet_nicht_gemeldet")
        return frisch.get("claim_code")

    async def async_step_geraet(self, user_input: dict | None = None) -> ConfigFlowResult:
        assert self._client is not None
        try:
            self._geraet = await self._api(self._client.geraet)
        except _Unterbrechung as unterbrechung:
            return await self._unterbrechung(unterbrechung)
        if self._reconfigure:
            return await self.async_step_menue()
        if self._geraet.get("eingerichtet"):
            return await self.async_step_schon_eingerichtet()
        return await self.async_step_anlage()

    async def async_step_schon_eingerichtet(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="schon_eingerichtet",
                                        description_placeholders={"anlage": _md(self._geraet.get("tenant_id"))})
        return await self._eintrag()

    # --- Anlage ---

    def _anlagen(self) -> list[dict]:
        """Anlagen, die dieses Geraet bekommen kann: aktiv, frei oder ersetzbar."""
        return [a for a in self._geraet.get("anlagen") or []
                if isinstance(a, dict) and isinstance(a.get("tenant_id"), str) and a.get("aktiv")
                and (not a.get("belegt") or a.get("ersetzbar"))]

    async def async_step_anlage(self, user_input: dict | None = None) -> ConfigFlowResult:
        anlagen = self._anlagen()
        if not anlagen:
            return await self._abbrechen("keine_anlage")
        if user_input is not None:
            self._tenant = user_input["anlage"]
            self._ersetzen = any(a["tenant_id"] == self._tenant and a.get("belegt") for a in anlagen)
            return await (self.async_step_ersetzen() if self._ersetzen else self.async_step_inventur())
        options = [{"value": a["tenant_id"], "label": a["tenant_id"]} for a in anlagen]
        return self.async_show_form(step_id="anlage", data_schema=vol.Schema({
            vol.Required("anlage", default=anlagen[0]["tenant_id"]): _auswahl(options)}))

    async def async_step_ersetzen(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="ersetzen", description_placeholders={"anlage": _md(self._tenant)})
        return await self.async_step_inventur()

    # --- Inventur und Vorschlag ---

    async def async_step_inventur(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._run_progress("inventur", "inventur", self._inventur)

    async def _inventur(self) -> str:
        """Inventur anfordern (ein noch offener Befehl wird weiter benutzt) und auf das Ergebnis warten."""
        assert self._client is not None
        try:
            await self._api(self._client.inventur)
            deadline = time.monotonic() + INVENTUR_WAIT_SECONDS
            while True:
                stand = (await self._api(self._client.geraet)).get("inventur") or {}
                if stand.get("zustand") == _INVENTUR_FERTIG:
                    self._vorschlag = await self._api(self._client.vorschlag, konflikt="keine_integration")
                    return "integration"
                if stand.get("zustand") == _INVENTUR_FEHLGESCHLAGEN:
                    fehler = stand.get("fehler")
                    # Ein abgelaufener Befehl hat keinen Text: dann sagen, dass das Geraet nicht erreichbar war.
                    grund = _md(fehler) if isinstance(fehler, str) and fehler else await async_hint(
                        self.hass, "geraet_nicht_erreichbar")
                    raise _Abbruch("inventur_fehler", grund=grund)
                if time.monotonic() >= deadline:
                    return "inventur_zeit"
                await asyncio.sleep(POLL_SECONDS)
        except _Unterbrechung as unterbrechung:
            return self._merken(unterbrechung)

    async def async_step_inventur_zeit(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="inventur_zeit")
        return await self.async_step_inventur()

    async def async_step_integration(self, user_input: dict | None = None) -> ConfigFlowResult:
        integrationen = [i for i in self._vorschlag.get("integrationen") or []
                         if isinstance(i, dict) and isinstance(i.get("plattform"), str)]
        if not integrationen:
            return await self._abbrechen("keine_integration", grund="-")
        # Ohne gezeigtes Formular kann HA die Eingabe des Clients weiterreichen (REST: {} nach dem Fortschritt der
        # Inventur): nur eine angebotene Plattform gilt als Auswahl, sonst Auswahl bzw. Formular.
        wahl = (user_input or {}).get("integration")
        if wahl is None and len(integrationen) == 1:
            wahl = integrationen[0]["plattform"]
        gewaehlt = next((i for i in integrationen if i["plattform"] == wahl), None)
        if gewaehlt is not None:
            self._integration = gewaehlt
            return await self.async_step_system()
        options = [{"value": i["plattform"], "label": _text(i.get("label"))} for i in integrationen]
        return self.async_show_form(step_id="integration",
                                    data_schema=vol.Schema({vol.Required("integration"): _auswahl(options)}))

    async def async_step_system(self, user_input: dict | None = None) -> ConfigFlowResult:
        integration = self._integration
        angebot = [k for k in integration.get("kreise") or [] if isinstance(k, dict)]
        profile = [p for p in integration.get("profile") or [] if isinstance(p, dict) and p.get("hebelsaetze")]
        if not angebot or not profile:
            return await self._abbrechen("nicht_unterstuetzt", integration=_md(integration.get("label")))
        if user_input is not None:
            gewaehlt = list(user_input.get("kreise") or [])
            fehler = self._wahl_fehler(gewaehlt, angebot)
            self._fehler = {key: value for key, value in self._fehler.items() if key != "kreise"} | fehler
            if not fehler:
                profil = next(p for p in profile if p["profile_id"] == user_input["profil"])
                self._auswahl = {"profil": profil["profile_id"]}
                self._kreise = self._nummerieren(gewaehlt, angebot)
                hebelsaetze = profil["hebelsaetze"]
                if len(hebelsaetze) == 1:
                    return await self.async_step_hebelsatz({"hebelsatz": hebelsaetze[0]["id"]})
                return await self.async_step_hebelsatz()
        vorschlag = integration.get("vorschlag") or {}
        bisher = [k["schluessel"] for k in self._kreise]
        schema = vol.Schema({
            _vorbelegt("kreise", bisher or vorschlag.get("kreise")): _auswahl(
                [{"value": k["schluessel"], "label": _text(k.get("label"))} for k in angebot], multiple=True),
            _vorbelegt("profil", self._auswahl.get("profil") or vorschlag.get("profil")): _auswahl(
                [{"value": p["profile_id"], "label": _text(p.get("label"))} for p in profile]),
        })
        return self.async_show_form(step_id="system", data_schema=schema, errors=self._fehler_fuer(("kreise",)),
                                    description_placeholders={"integration": _md(integration.get("label")),
                                                              "hinweis": klartext(integration.get("hinweis"))})

    def _wahl_fehler(self, gewaehlt: list, angebot: list[dict]) -> dict[str, str]:
        """Fehler der Auswahl der Heizkreise: keiner, zu viele, oder ein schon eingerichteter Kreis fehlt (er laesst
        sich hier nicht herausnehmen)."""
        if not gewaehlt:
            return {"kreise": "required"}
        if len(gewaehlt) > EINRICHTUNG_MAX_KREISE:
            return {"kreise": "zu_viele"}
        if any(isinstance(k.get("nummer"), int) and k.get("schluessel") not in gewaehlt for k in angebot):
            return {"kreise": "kreis_nicht_entfernbar"}
        return {}

    @staticmethod
    def _nummerieren(gewaehlt: list, angebot: list[dict]) -> list[dict]:
        """Die gewaehlten Kreise mit Nummern: ein eingerichteter behaelt seine, neue bekommen die kleinsten freien."""
        fest = {k["schluessel"]: k["nummer"] for k in angebot if isinstance(k.get("nummer"), int)}
        frei = iter(n for n in range(1, EINRICHTUNG_MAX_KREISE + 1) if n not in fest.values())
        kreise = [{"nummer": fest.get(k["schluessel"]) or next(frei), "schluessel": k["schluessel"],
                   "fest": k["schluessel"] in fest, "zuordnung": {}, "eingaben": {}, "abo_id": None,
                   "ausgelassen": False}
                  for k in angebot if k["schluessel"] in gewaehlt]
        return sorted(kreise, key=lambda k: k["nummer"])

    async def async_step_hebelsatz(self, user_input: dict | None = None) -> ConfigFlowResult:
        assert self._client is not None
        profil = next(p for p in self._integration["profile"] if p["profile_id"] == self._auswahl["profil"])
        if user_input is None:
            return self.async_show_form(step_id="hebelsatz", data_schema=vol.Schema({vol.Required("hebelsatz"): _auswahl(
                [h["id"] for h in profil["hebelsaetze"]], translation_key="lever_set")}))
        self._auswahl["hebelsatz"] = user_input["hebelsatz"]
        for kreis in self._kreise:
            try:
                # Beim Ersetzen schlaegt der Server die Zuordnung des alten Geraets vor.
                kreis["zuordnung"] = await self._api(
                    self._client.zuordnung, kreis["schluessel"], self._auswahl["profil"], self._auswahl["hebelsatz"],
                    tenant_id=self._tenant if self._ersetzen else None)
            except _Unterbrechung as unterbrechung:
                return await self._unterbrechung(unterbrechung)
            for rolle in kreis["zuordnung"].get("rollen") or []:
                if rolle.get("pflicht") and not rolle.get("kandidaten"):
                    bezeichnung = await async_role_label(self.hass, _text(rolle.get("rolle")))
                    return await self._abbrechen("rolle_ohne_kandidaten", rolle=_md(bezeichnung))
            # Raumfuehler und Raum-Soll sind Pflichtfelder des Schritts `raeume`: ohne Kandidaten gabe es nur einen
            # leeren Selektor, den niemand ausfuellen kann.
            for feld in _RAEUME:
                if not (kreis["zuordnung"].get(feld) or {}).get("kandidaten"):
                    return await self._abbrechen("rolle_ohne_kandidaten",
                                                 rolle=await async_role_label(self.hass, feld, schritt="raeume"))
        return await self._kreis_beginnen(0)

    # --- Je Heizkreis: Abo, Raeume, Anlagenwerte ---

    def _aktuell(self) -> dict:
        return self._kreise[self._index]

    def _dabei(self) -> list[dict]:
        """Die Kreise, die in die Anfrage kommen (ein neuer weiterer Kreis ohne Abo bleibt erkannt)."""
        return [k for k in self._kreise if not k["ausgelassen"]]

    def _platzhalter(self) -> dict[str, str]:
        return {"nummer": str(self._aktuell()["nummer"])}

    async def _kreis_beginnen(self, index: int) -> ConfigFlowResult:
        """Naechster Kreis: ein neuer weiterer Kreis zuerst mit dem Abo, jeder andere mit den Raeumen."""
        self._index = index
        if index >= len(self._kreise):
            return await self.async_step_benachrichtigung()
        kreis = self._aktuell()
        if kreis["nummer"] >= 2 and not kreis["fest"]:
            return await self.async_step_abo()
        return await self.async_step_raeume()

    def _freie_abos(self) -> list[dict]:
        """Abos des Kontos, die noch an keinem Kreis dieses Ablaufs gewaehlt sind (oder am aktuellen)."""
        vergeben = {k["abo_id"] for k in self._kreise if k["abo_id"] and k is not self._aktuell()}
        return [a for a in self._geraet.get("abos") or []
                if isinstance(a, dict) and isinstance(a.get("abo_id"), str) and a["abo_id"] not in vergeben]

    async def async_step_abo(self, user_input: dict | None = None) -> ConfigFlowResult:
        kreis = self._aktuell()
        abos = self._freie_abos()
        if user_input is not None:
            kreis["abo_id"] = user_input.get("abo_id") or None
            kreis["ausgelassen"] = kreis["abo_id"] is None
            self._fehler = {k: v for k, v in self._fehler.items() if k != _fk(kreis["nummer"], "abo_id")}
            if kreis["ausgelassen"]:
                return await self._kreis_beginnen(self._index + 1)
            return await self.async_step_raeume()
        felder: dict = {}
        if abos:
            felder[_vorbelegt("abo_id", kreis["abo_id"], required=False)] = _auswahl(
                [{"value": a["abo_id"], "label": _text(a.get("name"))} for a in abos])
        return self.async_show_form(step_id="abo", data_schema=vol.Schema(felder), description_placeholders={
            **self._platzhalter(), "abos": "" if abos else "-"}, errors=self._fehler_kreis(("abo_id",)))

    async def async_step_raeume(self, user_input: dict | None = None) -> ConfigFlowResult:
        kreis = self._aktuell()
        z, e = kreis["zuordnung"], kreis["eingaben"]
        if user_input is not None:
            e.update({key: user_input.get(key) for key in _KREIS_FELDER})
            if self._raeume_fehler():
                return await self.async_step_raeume()
            return await self.async_step_anlagenwerte()
        standard = "Referenzraum" if kreis["nummer"] == 1 else f"Raum {kreis['nummer']}"
        schema = vol.Schema({
            vol.Required("raum_name", default=e.get("raum_name") or standard): selector.TextSelector(),
            _vorbelegt("raumfuehler", e.get("raumfuehler") or z["raumfuehler"]["vorschlag"]): _entities(
                z["raumfuehler"]["kandidaten"], multiple=True),
            _vorbelegt("room_target", e.get("room_target") or z["room_target"]["vorschlag"]): _entities(
                z["room_target"]["kandidaten"]),
            _vorbelegt("raum_soll", e.get("raum_soll") or z.get("raum_soll")): selector.NumberSelector(
                selector.NumberSelectorConfig(min=RAUM_SOLL_MIN, max=RAUM_SOLL_MAX, step=RAUM_SOLL_STEP,
                                              unit_of_measurement="°C", mode=selector.NumberSelectorMode.BOX)),
            _vorbelegt("kontroll_name", e.get("kontroll_name"), required=False): selector.TextSelector(),
            _vorbelegt("kontroll_fuehler", e.get("kontroll_fuehler") or [], required=False): _entities(
                z["raumfuehler"]["kandidaten"], multiple=True),
        })
        return self.async_show_form(step_id="raeume", data_schema=schema, description_placeholders=self._platzhalter(),
                                    errors=self._fehler_kreis(_KREIS_FELDER))

    def _raeume_fehler(self) -> bool:
        """Prueft die Raeume des aktuellen Kreises; setzt die Feldfehler (alte Fehler der Felder entfallen)."""
        kreis = self._aktuell()
        e, nummer = kreis["eingaben"], kreis["nummer"]
        neu: dict[str, str] = {}
        fuehler = [*(e.get("raumfuehler") or []), *(e.get("kontroll_fuehler") or [])]
        if len(fuehler) > EINRICHTUNG_MAX_RAUMFUEHLER:
            neu["raumfuehler"] = "zu_viele"
        if e.get("kontroll_fuehler") and not e.get("kontroll_name"):
            neu["kontroll_name"] = "required"
        if e.get("kontroll_name") and not e.get("kontroll_fuehler"):
            neu["kontroll_fuehler"] = "required"
        eigene = {_fk(nummer, feld) for feld in (*_KREIS_FELDER, "raeume")}
        self._fehler = {k: v for k, v in self._fehler.items() if k not in eigene} | {
            _fk(nummer, feld): code for feld, code in neu.items()}
        return bool(neu)

    def _rollen(self, kreis: dict | None = None) -> list[dict]:
        """Angezeigte Rollen: Pflicht immer (Kandidaten geprueft), optionale nur mit Kandidaten."""
        return [r for r in (kreis or self._aktuell())["zuordnung"].get("rollen") or [] if r.get("kandidaten")]

    async def async_step_anlagenwerte(self, user_input: dict | None = None) -> ConfigFlowResult:
        kreis = self._aktuell()
        rollen = self._rollen()
        if user_input is not None:
            kreis["eingaben"]["rollen"] = {key: value for key, value in user_input.items() if value}
            return await self._kreis_beginnen(self._index + 1)
        alt = kreis["eingaben"].get("rollen", {})
        felder = {
            _vorbelegt(r["rolle"], alt.get(r["rolle"]) or r.get("vorschlag"), required=bool(r.get("pflicht"))):
                _entities(r["kandidaten"])
            for r in rollen
        }
        return self.async_show_form(step_id="anlagenwerte", data_schema=vol.Schema(felder),
                                    description_placeholders=self._platzhalter(),
                                    errors=self._fehler_kreis(tuple(r["rolle"] for r in rollen)))

    def _angebot(self, feld: str) -> list:
        """Das Angebot der Anlage (Handys, Hinweise): was die Zuordnung eines der Kreise nennt, ohne Doppelte."""
        return list(dict.fromkeys(w for k in self._dabei() for w in k["zuordnung"].get(feld) or []))

    async def async_step_benachrichtigung(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._eingaben.update({key: user_input.get(key) for key in _ANLAGENWEIT})
            if self._grenzen_verletzt(("batterien", "benachrichtigung")):
                return await self.async_step_benachrichtigung()
            return await self.async_step_zusammenfassung()
        je_entity: dict = {}
        for kreis in self._dabei():
            je_entity.update(kreis["zuordnung"].get("batterie_je_entity") or {})
        gewaehlt = [e for k in self._dabei() for e in (*(k["eingaben"].get("raumfuehler") or []),
                                                       *(k["eingaben"].get("kontroll_fuehler") or []),
                                                       k["eingaben"].get("room_target"))]
        batterien = list(dict.fromkeys(je_entity[e] for e in gewaehlt if e in je_entity))
        bisher = self._bisherige_bindung()
        # Neu konfigurieren: der Server liefert nur die Auswahlmoeglichkeiten, die bisherige Wahl steht in der
        # Konfiguration des Geraets; sie bleibt erhalten, soweit sie noch angeboten wird.
        if "batterien" in bisher:
            batterien = self._noch_angeboten(bisher["batterien"], set(je_entity.values()))
        handys_angebot, hinweise_angebot = self._angebot("benachrichtigung"), self._angebot("hinweise")
        handys = self._noch_angeboten(bisher.get("benachrichtigung"), set(handys_angebot))
        hinweise = self._noch_angeboten(bisher.get("hinweise_aus"), set(hinweise_angebot))
        felder: dict = {}
        if handys_angebot:
            felder[vol.Optional("benachrichtigung", default=self._vorgabe("benachrichtigung", handys))] = _auswahl(
                handys_angebot, multiple=True)
        felder[vol.Optional("hinweise_aus", default=self._vorgabe("hinweise_aus", hinweise))] = _auswahl(
            hinweise_angebot, multiple=True, translation_key="hinweise")
        if je_entity:
            felder[vol.Optional("batterien", default=self._vorgabe("batterien", batterien))] = _entities(
                sorted(set(je_entity.values())), multiple=True)
        return self.async_show_form(step_id="benachrichtigung", data_schema=vol.Schema(felder),
                                    errors=self._fehler_fuer(_ANLAGENWEIT))

    def _bisherige_bindung(self) -> dict:
        """Bindung des ersten Heizkreises der aktuellen Konfiguration des Geraets (Batterien, Handys und Hinweise gelten
        fuer die ganze Anlage); nur beim Neu konfigurieren (sonst leer)."""
        konfiguration = self._geraet.get("konfiguration") if self._reconfigure else None
        kreise = konfiguration.get("kreise") if isinstance(konfiguration, dict) else None
        erster = next((k for k in kreise or [] if isinstance(k, dict) and k.get("nummer") == 1), None)
        bindung = erster.get("bindung") if erster else None
        return bindung if isinstance(bindung, dict) else {}

    def _vorgabe(self, key: str, sonst: list) -> list:
        """Schon im Assistenten gewaehlter Wert (auch eine bewusst leere Wahl), sonst `sonst`."""
        gewaehlt = self._eingaben.get(key)
        return list(gewaehlt) if gewaehlt is not None else sonst

    @staticmethod
    def _noch_angeboten(werte: object, angebot: set) -> list:
        return [w for w in dict.fromkeys(werte if isinstance(werte, list) else []) if w in angebot]

    def _zu_viele(self) -> dict[str, str]:
        """Felder der Auswahl mit mehr Eintraegen, als der Server annimmt (`zu_viele`)."""
        fehler = {feld: "zu_viele" for feld, maximum in _MAXIMUM.items()
                  if len(self._eingaben.get(feld) or []) > maximum}
        for kreis in self._dabei():
            e = kreis["eingaben"]
            if len([*(e.get("raumfuehler") or []), *(e.get("kontroll_fuehler") or [])]) > EINRICHTUNG_MAX_RAUMFUEHLER:
                fehler[_fk(kreis["nummer"], "raumfuehler")] = "zu_viele"
        return fehler

    def _grenzen_verletzt(self, felder: tuple[str, ...]) -> bool:
        """Setzt die Feldfehler `zu_viele` der Felder der Anlage (alte Fehler dieser Felder entfallen); True, wenn es
        welche gibt."""
        zu_viele = {feld: code for feld, code in self._zu_viele().items() if feld in felder}
        self._fehler = {key: value for key, value in self._fehler.items() if key not in felder} | zu_viele
        return bool(zu_viele)

    def _fehler_fuer(self, felder: tuple[str, ...]) -> dict[str, str]:
        return {key: value for key, value in self._fehler.items() if key in felder}

    def _fehler_kreis(self, felder: tuple[str, ...]) -> dict[str, str]:
        """Fehler des aktuellen Kreises je Formularfeld; der Fehler `raeume` des Servers gehoert zum Namen."""
        nummer = self._aktuell()["nummer"]
        fehler = {feld: self._fehler[_fk(nummer, feld)] for feld in felder if _fk(nummer, feld) in self._fehler}
        if "raum_name" in felder and _fk(nummer, "raeume") in self._fehler:
            fehler["raum_name"] = self._fehler[_fk(nummer, "raeume")]
        return fehler

    def _fehler_ohne_feld(self) -> bool:
        """Feldfehler eines Felds, das kein Formular zeigt (z. B. kreis, tenant_id, eine Rolle ohne Kandidaten)."""
        bekannt = {*_ANLAGENWEIT, "kreise"}
        for kreis in self._dabei():
            nummer = kreis["nummer"]
            bekannt |= {_fk(nummer, feld) for feld in (*_KREIS_FELDER, "raeume", "abo_id",
                                                       *(r["rolle"] for r in self._rollen(kreis)))}
        return any(key not in bekannt for key in self._fehler)

    async def _formular_mit_fehlern(self) -> ConfigFlowResult:
        if "kreise" in self._fehler:
            return await self.async_step_system()
        for index, kreis in enumerate(self._kreise):
            if kreis["ausgelassen"]:
                continue
            nummer = kreis["nummer"]

            def eigene(felder, nummer=nummer):
                return any(_fk(nummer, feld) in self._fehler for feld in felder)

            self._index = index
            if eigene(("abo_id",)):
                return await self.async_step_abo()
            if eigene((*_KREIS_FELDER, "raeume")):
                return await self.async_step_raeume()
            if eigene(tuple(r["rolle"] for r in self._rollen(kreis))):
                return await self.async_step_anlagenwerte()
        return await self.async_step_benachrichtigung()

    def _anfrage(self) -> dict:
        kreise = [{"nummer": k["nummer"], "kreis": k["schluessel"], **k["eingaben"], "abo_id": k["abo_id"]}
                  for k in self._dabei()]
        return v3_anfrage.anfrage(self._auswahl, kreise, self._eingaben,
                                  tenant_id=None if self._reconfigure else self._tenant, ersetzen=bool(self._ersetzen))

    async def _warnungen(self, warnungen: dict) -> str:
        """Warnungen der Pruefung als Text der UI-Sprache; unbekannte Arten mit ihrem Schluessel."""
        zeilen = []
        for art, werte in warnungen.items():
            # Art und Entities stammen vom Server: jede Zeile bleibt eine Listenzeile, nur ihre Werte werden maskiert.
            entities = klartext(", ".join(str(w) for w in werte) if isinstance(werte, list) else str(werte))
            key = f"warning_{art}"
            text = await async_hint(self.hass, key)
            zeilen.append(f"- {text.format(entities=entities) if text != key else f'{klartext(art)}: {entities}'}")
        return "\n".join(zeilen) or "-"

    async def async_step_zusammenfassung(self, user_input: dict | None = None) -> ConfigFlowResult:
        assert self._client is not None
        if user_input is not None:
            return await self.async_step_einrichten()
        try:
            if zu_viele := self._zu_viele():
                # Der Server wuerde mit 400 ohne Feldfehler ablehnen; hier zurueck in das Formular des Felds.
                raise _Feldfehler(zu_viele)
            ergebnis = await self._api(self._client.pruefen, self._anfrage())
            fehler = ergebnis.get("fehler") or {}
            if fehler:
                raise _Feldfehler({str(k): str(v) for k, v in fehler.items()})
        except _Unterbrechung as unterbrechung:
            return await self._unterbrechung(unterbrechung)
        self._fehler = {}
        return self.async_show_form(step_id="zusammenfassung", description_placeholders={
            "anlage": _md(self._tenant), "warnungen": await self._warnungen(ergebnis.get("warnungen") or {}),
            # eigener Absatz vor der Frage; der Umbruch steht hier, weil Uebersetzungstexte keinen Leerraum am Ende haben duerfen
            "auto_update": await async_hint(self.hass, "auto_update_hinweis") + "\n\n" if self._auto_update_gesetzt else ""})

    # --- Auftrag ---

    async def async_step_einrichten(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._run_progress("einrichten", "einrichten", self._einrichten)

    async def _einrichten(self) -> str:
        assert self._client is not None
        try:
            if self._reconfigure:
                job_id = await self._api(self._client.neu_konfigurieren, self._anfrage())
            else:
                job_id = await self._api(self._client.einrichten, self._anfrage())
            deadline = time.monotonic() + AUFTRAG_WAIT_SECONDS
            while True:
                auftrag = await self._api(self._client.auftrag, job_id)
                if auftrag.get("zustand") == AUFTRAG_FERTIG:
                    self._geraet = await self._api(self._client.geraet)
                    return "fertig"
                if auftrag.get("zustand") == AUFTRAG_FEHLGESCHLAGEN:
                    raise _Abbruch("einrichten_fehler", grund=_md(auftrag.get("fehler")))
                if time.monotonic() >= deadline:
                    raise _Abbruch("einrichten_zeit")
                await asyncio.sleep(POLL_SECONDS)
        except _Unterbrechung as unterbrechung:
            return self._merken(unterbrechung)

    async def async_step_fertig(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._eintrag()

    async def _eintrag(self) -> ConfigFlowResult:
        """Eintrag aus der Konfiguration, die das Geraet uebernommen hat; nie ein Token."""
        konfiguration = self._geraet.get("konfiguration")
        tenant_id = self._geraet.get("tenant_id")
        if not isinstance(konfiguration, dict) or not isinstance(tenant_id, str):
            return await self._abbrechen("keine_konfiguration")
        data = {"geraet_id": self._discovery["geraet_id"], "tenant_id": tenant_id,
                "profile_id": konfiguration.get("profil"), "lever_set": konfiguration.get("hebelsatz"),
                "hebel": list(konfiguration.get("hebel") or []), "shift_lever": konfiguration.get("shift_lever")}
        await self._abmelden()
        if self._reconfigure:
            return self.async_update_reload_and_abort(self._get_reconfigure_entry(), data=data)
        return self.async_create_entry(title=tenant_id, data=data)

    # --- Neu konfigurieren ---

    async def async_step_reconfigure(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Nur ein Eintrag mit gueltiger Geraete-ID; sonst Abbruch vor der Anmeldung."""
        geraet_id = self._get_reconfigure_entry().data.get("geraet_id")
        if not isinstance(geraet_id, str) or not GERAET_ID_RE.fullmatch(geraet_id):
            _LOGGER.warning("Neu konfigurieren: Eintrag ohne gueltige Geraete-ID; bitte loeschen und neu einrichten")
            return self.async_abort(reason="unknown")
        self._reconfigure = True
        self._discovery = {"geraet_id": geraet_id}
        return await self.async_step_anmelden()

    async def async_step_menue(self, user_input: dict | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="menue", menu_options=["zuordnung_aendern", "geraet_entfernen"],
                                    description_placeholders={"anlage": _md(self._geraet.get("tenant_id"))})

    async def async_step_zuordnung_aendern(self, user_input: dict | None = None) -> ConfigFlowResult:
        self._tenant = self._geraet.get("tenant_id")
        return await self.async_step_inventur()

    async def async_step_geraet_entfernen(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="geraet_entfernen",
                                        description_placeholders={"anlage": _md(self._geraet.get("tenant_id"))})
        return await self.async_step_entfernen()

    async def async_step_entfernen(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._run_progress("entfernen", "entfernen", self._entfernen)

    async def _entfernen(self) -> str:
        """Nach einem angenommenen Entfernen widerruft die Freigabe das Token: 401/404 auf `auftrag` heisst dann
        "entfernt" (Server einrichtung/api.py, Route `auftrag`)."""
        assert self._client is not None
        try:
            ergebnis = await self._api(self._client.entfernen, konflikt="entfernen_fehler")
        except _Unterbrechung as unterbrechung:
            return self._merken(unterbrechung)
        if ergebnis.get("freigegeben"):
            return "entfernt"
        job_id = ergebnis.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            return self._merken(_Abbruch("entfernen_fehler", grund="-"))
        deadline = time.monotonic() + ENTFERNEN_WAIT_SECONDS
        while True:
            try:
                auftrag = await self._client.auftrag(job_id)
            except (InvalidAuth, NotFound):
                # Die Freigabe hat das Token widerrufen. Unschaerfe (selten): laeuft das Token genau jetzt ab (1 h,
                # bei Nutzung verlaengert), sieht das gleich aus und meldet "entfernt", obwohl der Auftrag noch
                # laeuft; das Geraet fuehrt ihn trotzdem aus, nur der Eintrag verschwindet frueher.
                return "entfernt"
            except (CannotConnect, ServerError) as error:
                _LOGGER.info("Einrichtung: Stand des Entfernens nicht lesbar (%s)", type(error).__name__)
                return "entfernen_laeuft"
            except ApiError as error:
                return self._merken(_Abbruch("entfernen_fehler", grund=_md(error.text)))
            if auftrag.get("zustand") == AUFTRAG_FERTIG:
                return "entfernt"
            if auftrag.get("zustand") == AUFTRAG_FEHLGESCHLAGEN:
                return self._merken(_Abbruch("entfernen_fehler", grund=_md(auftrag.get("fehler"))))
            if time.monotonic() >= deadline:
                return "entfernen_laeuft"
            await asyncio.sleep(POLL_SECONDS)

    async def async_step_entfernt(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._eintrag_entfernen("entfernt")

    async def async_step_entfernen_laeuft(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Das Geraet hat in der Wartezeit nicht geantwortet: der Eintrag wird trotzdem entfernt. Das Loeschen
        meldet das Geraet lokal ab (stdin der Heizungsbruecke); der Server gibt es frei, sobald es erreichbar ist."""
        return await self._eintrag_entfernen("entfernen_laeuft")

    async def _eintrag_entfernen(self, reason: str) -> ConfigFlowResult:
        await self._abmelden()
        entry = self._get_reconfigure_entry()
        # Nicht eager: der Eintrag verschwindet erst, wenn dieser Flow beendet ist.
        self.hass.async_create_task(self.hass.config_entries.async_remove(entry.entry_id),
                                    "smartheat_eintrag_entfernen", eager_start=False)
        return self.async_abort(reason=reason)
