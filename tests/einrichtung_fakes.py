"""Nachgebildete Einrichtungs-API und Add-on-Seite fuer die Flow-Tests (Spec 5c 2.1)."""
from __future__ import annotations

from custom_components.smartheat.einrichtung_client import (
    CannotConnect,
    Conflict,
    InvalidAuth,
    NotFound,
    Rejected,
    TokenUngueltig,
)

GERAET_ID = "shg-aaaaaaaaaaaaaaaa"
TENANT = "zuhause2"
KREIS = "mypyllant|e1|SYSTEM|0"
CLAIM_CODE = "ABCDEFGHIJKL"
TOKEN = "t-geheim"
PASSWORT = "pw-geheim-42"
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
    """Ersetzt EinrichtungClient-Methoden; zaehlt Aufrufe, haelt das Token wie der echte Client nur im Speicher.
    Fehler je Route ueber `fehler[route]` (eine Ausnahme, die der Aufruf wirft). Nach einem angenommenen Entfernen
    antwortet `auftrag` wie der Server mit 401 (die Freigabe widerruft das Token)."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.geraet_sicht = {"device_id": GERAET_ID, "host": "ha", "eingerichtet": False, "tenant_id": None,
                             "anlagen": [{"tenant_id": TENANT, "aktiv": True, "belegt": False, "ersetzbar": False}],
                             "inventur": {"zustand": "keine"}, "konfiguration": None, "raum_soll": 20.0}
        self.zuordnung_antwort = ZUORDNUNG
        self.inventur_ergebnis = {"zustand": "fertig"}
        self.pruef_ergebnis = {"fehler": {}, "warnungen": {}}
        self.auftrag_ergebnis = {"zustand": "fertig", "fehler": None}
        self.entfernen_ergebnis: dict = {"job_id": "j3"}
        self.entfernt = False
        self.fehler: dict[str, Exception] = {}

    def bind(self, client) -> None:
        for name in ("anmelden", "abmelden", "uebernehmen", "geraet", "inventur", "vorschlag", "zuordnung", "pruefen",
                     "einrichten", "neu_konfigurieren", "entfernen", "auftrag"):
            setattr(client, name, getattr(self, f"_{name}"))
        self._client = client

    def _check(self, route: str) -> None:
        if route in self.fehler:
            raise self.fehler[route]

    async def _anmelden(self, email, password):
        self.calls.append(("anmelden", email))
        self._check("anmelden")
        self._client.token = TOKEN

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
        return VORSCHLAG

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


__all__ = ["CannotConnect", "Conflict", "FakeServer", "InvalidAuth", "NotFound", "Rejected", "TokenUngueltig"]
