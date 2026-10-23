"""Wunschtemperatur als Entity (Spec 5c 1.3/2.2): fuer HA-Automationen und das Dashboard. Setzen feuert nur das lokale
Ereignis smartheat_wunsch (mit dem Kontext des Dienstaufrufs: Nutzer oder Automation); das Add-on wendet den Wert
sofort lokal an (Boost bei Erhoehung, auch ohne Internet) und meldet ihn als Bedienwunsch an den Server. Kein
Server-Aufruf aus der Integration. Angezeigt wird allein der wirksame Soll aus dem Status-Ereignis (nach dem Setzen
erst mit dem naechsten Ereignis, A6-41). Solange das Thermostat aus ist (Hinweis soll_quelle_aus) oder der Waechter das
Add-on als gestoppt bzw. nicht reagierend meldet, lehnt die Entity ab (5c F/Q3, A6-41)."""
from __future__ import annotations

from math import floor, isfinite

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    HINWEIS_FELD_KEY,
    HINWEIS_SOLL_QUELLE_AUS,
    RAUM_SOLL_MAX,
    RAUM_SOLL_MIN,
    RAUM_SOLL_STEP,
    STATUS_ADDON_GESTOPPT,
    STATUS_REAGIERT_NICHT,
    WUNSCH_EVENT,
)
from .entity import SmartHeatEntity

KEY = "wunschtemperatur"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([WunschtemperaturEntity(entry.runtime_data)])


def _temperature(value) -> float | None:
    """Endliche Zahl (kein bool) als float, sonst None (unbekannt statt einer kaputten Anzeige). Ein JSON-int
    ausserhalb des float-Bereichs (10**400) ist nicht umwandelbar (OverflowError) und gilt wie NaN als unbekannt."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if isfinite(number) else None


def _auf_schritt(value: float) -> float:
    """Auf den 0,5er-Schritt runden (halbe Schritte aufwaerts, 22,25 -> 22,5), dann in den Bedienbereich."""
    stufe = floor(value / RAUM_SOLL_STEP + 0.5) * RAUM_SOLL_STEP
    return min(max(stufe, RAUM_SOLL_MIN), RAUM_SOLL_MAX)


class WunschtemperaturEntity(SmartHeatEntity, NumberEntity):
    _attr_native_min_value = RAUM_SOLL_MIN
    _attr_native_max_value = RAUM_SOLL_MAX
    _attr_native_step = RAUM_SOLL_STEP
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "number", KEY)

    def _apply(self) -> None:
        self._attr_native_value = _temperature((self.coordinator.data or {}).get("raum_soll"))

    def _restore(self, last: State) -> None:
        try:
            self._attr_native_value = _temperature(float(last.state))
        except ValueError:
            self._attr_native_value = None

    async def async_set_native_value(self, value: float) -> None:
        """NaN/inf lehnt die Entity ab (HAs min/max-Pruefung laesst NaN durch), sonst geht der auf den Schritt
        gerundete Wert ans Add-on."""
        if not isfinite(value):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="wunsch_ungueltig")
        if self.coordinator.watchdog_status in (STATUS_ADDON_GESTOPPT, STATUS_REAGIERT_NICHT):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="addon_nicht_bereit")
        hinweise = (self.coordinator.data or {}).get("geraet_hinweise") or []
        if any(isinstance(h, dict) and h.get(HINWEIS_FELD_KEY) == HINWEIS_SOLL_QUELLE_AUS for h in hinweise):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="thermostat_aus")
        raum_soll = _auf_schritt(float(value))
        # Keine eigene Anzeige des gesetzten Werts: sie folgt dem naechsten Status-Ereignis (das Add-on sendet je
        # Geraete-Takt eines), also dem, was das Add-on wirklich anwendet (A6-41).
        self.hass.bus.async_fire(WUNSCH_EVENT, {"raum_soll": raum_soll}, context=self._context)
