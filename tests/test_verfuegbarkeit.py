"""Verfuegbarkeit: Hat der Waechter einen Befund (Add-on gestoppt, reagiert nicht) oder spricht das Add-on ein nicht
gelesenes Schema, sind alle Entities ausser dem Status nicht verfuegbar, in jedem Kreis und an der Anlage; Wunsch
und Dienst lehnen ab. Der Uebergang nach "nicht verfuegbar" und zurueck steht je einmal als INFO im Log (nur der
Befundcode, nie Namen oder Werte). Die Zeit kommt nie von der Wanduhr."""
import logging

import pytest
from homeassistant.components.number import DATA_COMPONENT
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.smartheat.const import DOMAIN, STATUS_EVENT, WUNSCH_EVENT

from .addon_fakes import make_device_entry, status_event

CO = "custom_components.smartheat.coordinator"
TENANT = "wohnung1"
STATUS = "sensor.smartheat_status"
WUNSCH = "number.smartheat_k1_wunschtemperatur"
ANLAGE = ("binary_sensor.smartheat_notbetrieb", "binary_sensor.smartheat_server_verbunden",
          "sensor.smartheat_datenfehler", "sensor.smartheat_addon_version")
KREIS = ("sensor.smartheat_k{n}_zustand", "sensor.smartheat_k{n}_kaelteschutz", "sensor.smartheat_k{n}_raumtemperatur",
         "sensor.smartheat_k{n}_befunde", "number.smartheat_k{n}_wunschtemperatur")


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(f"{CO}._now", lambda: now["t"])
    return now


async def _laufend(hass):
    entry = make_device_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.bus.async_fire(STATUS_EVENT, {**status_event(TENANT, "regelt"), "mindest_integration": "1.0.0"})
    await hass.async_block_till_done()
    return entry.runtime_data


def _alle(hass):
    return [hass.states.get(e.format(n=n)) for e in KREIS for n in (1, 2)] + [hass.states.get(e) for e in ANLAGE]


def _verfuegbar(zustaende):
    assert all(zustand is not None for zustand in zustaende)
    return [zustand.state != STATE_UNAVAILABLE for zustand in zustaende]


def _infos(caplog):
    return [r.getMessage() for r in caplog.records if r.name == CO and r.levelno == logging.INFO
            and "verfuegbar" in r.getMessage()]


async def test_ein_doppeltes_addon_laesst_alle_entities_verfuegbar(hass, clock):
    coordinator = await _laufend(hass)

    coordinator._set_watchdog_status("addon_mehrfach")
    await hass.async_block_till_done()

    assert all(_verfuegbar(_alle(hass)))
    assert hass.states.get(STATUS).state == "addon_mehrfach"


@pytest.mark.parametrize("befund", ["addon_gestoppt", "reagiert_nicht", "addon_fehlt"])
async def test_ein_waechterbefund_macht_alle_entities_ausser_dem_status_nicht_verfuegbar(hass, clock, befund):
    coordinator = await _laufend(hass)
    assert all(_verfuegbar(_alle(hass)))

    coordinator._set_watchdog_status(befund)
    await hass.async_block_till_done()

    assert _verfuegbar(_alle(hass)) == [False] * len(_alle(hass))
    assert hass.states.get(STATUS).state == befund

    coordinator._set_watchdog_status(None)
    await hass.async_block_till_done()

    assert all(_verfuegbar(_alle(hass)))
    assert hass.states.get(STATUS).state == "regelt"


async def test_ein_nicht_gelesenes_schema_laesst_den_status_verfuegbar_und_alle_anderen_nicht(hass, clock):
    await _laufend(hass)

    hass.bus.async_fire(STATUS_EVENT, {**status_event(TENANT, "regelt"), "schema": 5})
    await hass.async_block_till_done()

    assert _verfuegbar(_alle(hass)) == [False] * len(_alle(hass))
    assert hass.states.get(STATUS).state == "integration_veraltet"


async def test_die_verfuegbarkeit_eines_kreises_folgt_dem_befund_und_seine_werte_kehren_zurueck(hass, clock):
    coordinator = await _laufend(hass)
    zustand_2 = hass.states.get("sensor.smartheat_k2_zustand").state
    assert zustand_2 != STATE_UNAVAILABLE

    coordinator._set_watchdog_status("reagiert_nicht")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.smartheat_k2_zustand").state == STATE_UNAVAILABLE

    coordinator._set_watchdog_status(None)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.smartheat_k2_zustand").state == zustand_2


async def test_der_wunsch_lehnt_beim_waechterbefund_mit_der_uebersetzung_ab_und_der_dienst_erreicht_nichts(hass, clock):
    coordinator = await _laufend(hass)
    coordinator._set_watchdog_status("addon_gestoppt")
    await hass.async_block_till_done()
    wuensche = async_capture_events(hass, WUNSCH_EVENT)

    with pytest.raises(ServiceValidationError) as fehler:
        await hass.data[DATA_COMPONENT].get_entity(WUNSCH).async_set_native_value(22.0)
    assert (fehler.value.translation_domain, fehler.value.translation_key) == (DOMAIN, "addon_nicht_bereit")

    await hass.services.async_call("number", "set_value", {"entity_id": WUNSCH, "value": 22.0}, blocking=True)
    await hass.async_block_till_done()
    assert wuensche == []


async def test_der_uebergang_steht_je_einmal_im_log_und_ohne_inhalt(hass, clock, caplog):
    coordinator = await _laufend(hass)
    caplog.clear()

    with caplog.at_level(logging.INFO, logger=CO):
        coordinator._set_watchdog_status("reagiert_nicht")
        coordinator._set_watchdog_status("reagiert_nicht")
        coordinator._set_watchdog_status("addon_gestoppt")  # weiter nicht verfuegbar: kein zweiter Uebergang
        hass.bus.async_fire(STATUS_EVENT, {**status_event(TENANT, "regelt"), "schema": 5})
        await hass.async_block_till_done()  # zusaetzlich ein Versionsbefund: weiter nicht verfuegbar
        assert len(_infos(caplog)) == 1

        coordinator._set_watchdog_status(None)  # der Versionsbefund besteht: weiter nicht verfuegbar
        assert len(_infos(caplog)) == 1

        hass.bus.async_fire(STATUS_EVENT, status_event(TENANT, "regelt"))
        await hass.async_block_till_done()

    meldungen = _infos(caplog)
    assert len(meldungen) == 2
    assert "nicht verfuegbar" in meldungen[0] and "reagiert_nicht" in meldungen[0]
    assert "wieder verfuegbar" in meldungen[1]
    assert TENANT not in caplog.text and "shg-" not in caplog.text


async def test_ohne_befund_gibt_es_keine_meldung_im_log(hass, clock, caplog):
    with caplog.at_level(logging.INFO, logger=CO):
        coordinator = await _laufend(hass)
        coordinator._set_watchdog_status(None)
        await hass.async_block_till_done()

    assert _infos(caplog) == []
