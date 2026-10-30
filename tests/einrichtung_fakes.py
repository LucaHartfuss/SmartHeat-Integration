"""Nachgebildete Einrichtungs-API fuer die Flow-Tests; die Add-on-Seite steht in supervisor_fakes.py."""
from __future__ import annotations

import asyncio
from unittest.mock import create_autospec

from custom_components.smartheat.einrichtung_client import (
    CannotConnect,
    Conflict,
    EinrichtungClient,
    ErneutAnmelden,
    InvalidAuth,
    NotFound,
    PortalBestaetigung,
    Rejected,
    TokenUngueltig,
)

GERAET_ID = "shg-aaaaaaaaaaaaaaaa"
TENANT = "zuhause2"
KREIS = "mypyllant|e1|SYSTEM|0"
CLAIM_CODE = "ABCDEFGHIJKL"
TOKEN = "t-geheim"
NACHWEIS_SIGNATUR = "MEUCIQD-sig_geheim-42"
NACHWEIS_ZEITSTEMPEL = 1761800000
GERAETECODE = "gc-geheim-" + "x" * 30
ANMELDECODE = "BCDF-GHJK"
CODE_ANTWORT = {"geraetecode": GERAETECODE, "anmeldecode": ANMELDECODE, "link": "https://app.example.test/geraetecode",
                "link_komplett": "https://app.example.test/geraetecode#code=BCDF-GHJK", "gueltig_s": 600,
                "abfrage_s": 5}
KONFIGURATION = {"profil": "vaillant_gastherme_heizkoerper", "hebelsatz": "vaillant_vrc720", "bindung": {},
                 "raumfuehler": ["sensor.wohnzimmer_temperatur"],
                 "hebel": ["curve", "room_setpoint", "heat_limit", "min_flow"], "shift_lever": "room_setpoint"}
VORSCHLAG = {"integrationen": [{
    "plattform": "mypyllant", "label": "myVAILLANT", "hinweis": None,
    "kreise": [{"schluessel": KREIS, "label": "Zuhause"}],
    "profile": [{"profile_id": "vaillant_gastherme_heizkoerper", "label": "Vaillant Gastherme", "erzeuger_typ":
                 "Gastherme", "verteilsystem": "Heizkoerper", "shift_lever": "room_setpoint",
                 "hebelsaetze": [{"id": "vaillant_vrc720", "hebel": ["curve", "room_setpoint", "heat_limit",
                                                                     "min_flow"]}]}],
    "vorschlag": {"kreis": KREIS, "profil": "vaillant_gastherme_heizkoerper"}}]}
ROLLEN = [
    {"rolle": "curve", "pflicht": True, "vorschlag": "number.zuhause_circuit_0_heating_curve", "herkunft": "integration",
     "kandidaten": ["number.zuhause_circuit_0_heating_curve"]},
    {"rolle": "room_setpoint", "pflicht": True, "vorschlag": "climate.zuhause_zone_1_circuit_0_climate",
     "herkunft": "integration", "kandidaten": ["climate.zuhause_zone_1_circuit_0_climate"]},
    {"rolle": "outdoor_temp", "pflicht": True, "vorschlag": "sensor.zuhause_outdoor_temperature",
     "herkunft": "integration", "kandidaten": ["sensor.zuhause_outdoor_temperature"]},
    {"rolle": "flow_setpoint", "pflicht": False, "vorschlag": None, "herkunft": None, "kandidaten": []},
]
ZUORDNUNG = {
    "kreis": {"plattform": "mypyllant", "config_entry": "e1", "anlage": "SYSTEM", "nummer": "0"},
    "profil": "vaillant_gastherme_heizkoerper", "hebelsatz": "vaillant_vrc720", "rollen": ROLLEN,
    "raumfuehler": {"kandidaten": ["sensor.wohnzimmer_temperatur", "climate.wohnzimmer_thermostat"], "vorschlag": []},
    "room_target": {"kandidaten": ["sensor.wohnzimmer_temperatur", "climate.wohnzimmer_thermostat"], "vorschlag": None},
    "batterie_je_entity": {"climate.wohnzimmer_thermostat": "sensor.wohnzimmer_batterie"},
    "benachrichtigung": ["notify.mobile_app_handy"], "hinweise": ["raumfuehler", "batterie"], "raum_soll": 20.0,
    "abfrage_s": 1800, "hinweis": None,
}


class FakeServer:
    """Einrichtungs-API an der Grenze: `client(base_url)` liefert, was der Assistent als
    `EinrichtungClient` baut, als `create_autospec(EinrichtungClient, instance=True)` (Signaturen geprueft) mit den
    Seiteneffekten unten; die Klasse selbst bleibt unveraendert. Zaehlt Aufrufe, haelt das Token wie der
    echte Client nur im Speicher und merkt sich die Adresse (`base_url`, None: nie ein Client gebaut). Fehler je Route
    ueber `fehler[route]` (eine Ausnahme, die der Aufruf wirft). Nach einem angenommenen Entfernen antwortet `auftrag`
    wie der Server mit 401 (die Freigabe widerruft das Token). `abholen_folge`: die Antworten des Abholens in
    Reihenfolge (die letzte gilt weiter; `bestaetigt` setzt das Token wie der echte Client); `halten`: das Abholen wartet,
    bis `freigabe` gesetzt ist."""

    ROUTEN = ("geraetecode_anfordern", "geraetecode_abholen", "abmelden", "uebernehmen", "geraet", "inventur", "vorschlag", "zuordnung", "pruefen",
              "einrichten", "neu_konfigurieren", "entfernen", "auftrag")

    def __init__(self) -> None:
        self.base_url: str | None = None
        self.calls: list[tuple] = []
        self.geraet_sicht = {"device_id": GERAET_ID, "host": "ha", "eingerichtet": False, "tenant_id": None,
                             "anlagen": [{"tenant_id": TENANT, "aktiv": True, "belegt": False, "ersetzbar": False}],
                             "inventur": {"zustand": "keine"}, "konfiguration": None, "raum_soll": 20.0}
        self.vorschlag_antwort = VORSCHLAG
        self.zuordnung_antwort = ZUORDNUNG
        self.inventur_ergebnis = {"zustand": "fertig"}
        self.pruef_ergebnis = {"fehler": {}, "warnungen": {}}
        self.auftrag_ergebnis = {"zustand": "fertig", "fehler": None}
        self.entfernen_ergebnis: dict = {"job_id": "j3"}
        self.entfernt = False
        self.fehler: dict[str, Exception] = {}
        self.code_antwort = dict(CODE_ANTWORT)
        self.abholen_folge: list[dict] = [{"zustand": "bestaetigt"}]
        self.abholen_fehler_folge: list = []  # je Abholen eine Ausnahme oder None (dann gilt abholen_folge)
        self.halten = False
        self.freigabe = asyncio.Event()
        self._client = None

    def client(self, base_url: str):
        """Ersatz fuer `EinrichtungClient(session, base_url)` im Assistenten."""
        self.base_url = base_url
        client = create_autospec(EinrichtungClient, instance=True)
        client.token = None
        for name in self.ROUTEN:
            getattr(client, name).side_effect = getattr(self, f"_{name}")
        self._client = client
        return client

    def _check(self, route: str) -> None:
        if route in self.fehler:
            raise self.fehler[route]

    async def _geraetecode_anfordern(self, nachweis, sprache):
        self.calls.append(("geraetecode_anfordern", nachweis, sprache))
        self._check("geraetecode_anfordern")
        return dict(self.code_antwort)

    async def _geraetecode_abholen(self, geraetecode):
        self.calls.append(("geraetecode_abholen", geraetecode))
        if self.halten:
            await self.freigabe.wait()
        if self.abholen_fehler_folge and (fehler := self.abholen_fehler_folge.pop(0)) is not None:
            raise fehler
        self._check("geraetecode_abholen")
        stand = dict(self.abholen_folge.pop(0) if len(self.abholen_folge) > 1 else self.abholen_folge[0])
        if stand["zustand"] == "bestaetigt":
            self._client.token = TOKEN
        return stand

    async def _abmelden(self):
        self.calls.append(("abmelden",))
        self._client.token = None

    async def _uebernehmen(self, device_id, code):
        self.calls.append(("uebernehmen", device_id, code))
        self._check("uebernehmen")

    async def _geraet(self):
        self._check("geraet")
        return dict(self.geraet_sicht)

    async def _inventur(self):
        self.calls.append(("inventur",))
        self._check("inventur")
        self.geraet_sicht["inventur"] = dict(self.inventur_ergebnis)
        return "c1"

    async def _vorschlag(self):
        self._check("vorschlag")
        return self.vorschlag_antwort

    async def _zuordnung(self, kreis, profil, hebelsatz, tenant_id=None):
        self.calls.append(("zuordnung", kreis, profil, hebelsatz, tenant_id))
        self._check("zuordnung")
        return self.zuordnung_antwort

    async def _pruefen(self, anfrage):
        self.calls.append(("pruefen", anfrage))
        self._check("pruefen")
        return self.pruef_ergebnis

    async def _einrichten(self, anfrage):
        self.calls.append(("einrichten", anfrage))
        self._check("einrichten")
        if self.auftrag_ergebnis.get("zustand") == "fertig":
            self.geraet_sicht.update(eingerichtet=True, tenant_id=TENANT, konfiguration=KONFIGURATION)
        return "j1"

    async def _neu_konfigurieren(self, anfrage):
        self.calls.append(("neu_konfigurieren", anfrage))
        self._check("neu_konfigurieren")
        return "j2"

    async def _entfernen(self):
        self.calls.append(("entfernen",))
        self._check("entfernen")
        self.entfernt = self.auftrag_ergebnis.get("zustand") == "fertig"
        return dict(self.entfernen_ergebnis)

    async def _auftrag(self, job_id):
        self.calls.append(("auftrag", job_id))
        if self.entfernt:
            raise TokenUngueltig("", 401)
        self._check("auftrag")
        return dict(self.auftrag_ergebnis)


__all__ = ["CannotConnect", "Conflict", "ErneutAnmelden", "FakeServer", "InvalidAuth", "NotFound", "PortalBestaetigung",
           "Rejected", "TokenUngueltig"]
