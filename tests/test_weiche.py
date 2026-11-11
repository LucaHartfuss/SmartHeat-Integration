"""Versionsweiche: ein Status-Ereignis des eigenen Geraets in einem Schema, das die Integration nicht liest, ergibt
`integration_veraltet` (Schema hoeher als alle gelesenen) bzw. `addon_veraltet` (niedriger als alle); dazu ein Repair
mit `mindest_integration` aus dem Kern, die uebrigen Entities nicht verfuegbar, Wunsch und Dienst lehnen ab. Ein
unbekannter Status oder Hinweis-Schluessel verwirft das Ereignis nicht. Die Zeit kommt nie von der Wanduhr."""
import json
from pathlib import Path

import pytest
from homeassistant.components.number import DATA_COMPONENT
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.smartheat.const import (
    DOMAIN,
    INTEGRATION_EVENT,
    STATUS_EVENT,
    STATUS_SENSOR_VALUES,
    WUNSCH_EVENT,
)

from .addon_fakes import make_device_entry, status_event

CO = "custom_components.smartheat.coordinator"
TENANT = "wohnung1"
STATUS = "sensor.smartheat_status"
BOOST = "sensor.smartheat_k1_boost"
NOTBETRIEB = "binary_sensor.smartheat_notbetrieb"
WUNSCH = "number.smartheat_k1_wunschtemperatur"
KERN = "1.4.0"
_COMPONENT = Path(__file__).parent.parent / "custom_components" / "smartheat"


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(f"{CO}._now", lambda: now["t"])
    return now


async def _setup(hass):
    entry = make_device_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _fire(hass, event):
    hass.bus.async_fire(STATUS_EVENT, event)
    await hass.async_block_till_done()


def _ereignis(schema=4, status="regelt", **overrides):
    return {**status_event(TENANT, status, **overrides), "schema": schema, "mindest_integration": KERN}


def _issues(hass):
    return {issue_id: issue for (domain, issue_id), issue in ir.async_get(hass).issues.items() if domain == DOMAIN}


def _entity(hass, entity_id):
    return hass.data[DATA_COMPONENT].get_entity(entity_id)


@pytest.mark.parametrize(("schema", "befund"), [(5, "integration_veraltet"), (99, "integration_veraltet"),
                                                (3, "addon_veraltet"), (1, "addon_veraltet")])
async def test_ein_nicht_gelesenes_schema_ergibt_den_befund_mit_repair_und_nicht_verfuegbaren_entities(
        hass, clock, schema, befund):
    entry = await _setup(hass)
    await _fire(hass, _ereignis())  # vorher lesbar: danach sind die Entities nicht verfuegbar, nicht nur unbekannt
    assert hass.states.get(BOOST).state != STATE_UNAVAILABLE

    await _fire(hass, _ereignis(schema=schema))

    assert entry.runtime_data.status == befund
    assert hass.states.get(STATUS).state == befund
    for entity_id in (BOOST, NOTBETRIEB, WUNSCH):
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE, entity_id
    issue = _issues(hass)[befund]
    assert issue.translation_key == befund and not issue.is_fixable
    assert issue.translation_placeholders == ({"mindest_integration": KERN} if befund == "integration_veraltet"
                                              else None)
    assert set(_issues(hass)) & {"integration_veraltet", "addon_veraltet"} == {befund}


async def test_der_befund_gilt_auch_ohne_vorher_gelesenes_ereignis(hass, clock):
    await _setup(hass)

    await _fire(hass, _ereignis(schema=5))

    assert hass.states.get(STATUS).state == "integration_veraltet"
    assert hass.states.get(NOTBETRIEB).state == STATE_UNAVAILABLE
    # Ein nicht gelesenes Ereignis nennt keinen Kreis: Kreis-Entities entstehen daraus nicht.
    assert hass.states.get(BOOST) is None and hass.states.get(WUNSCH) is None


@pytest.mark.parametrize("kern", [None, "", "x" * 200, 7, ["1.4.0"], "1.4 <b>", "www.setwell-update.de",
                                  "1.4", "1..4.0", "1.4.0.", "a.b.c", "1.4.0/evil", "1.4.0" + "x" * 30,
                                  "1.4.0-www.evil.de", "1.4.0+a@b", "1.4.0-a:b", "1.4.0-a/b"])
async def test_ohne_brauchbare_mindestversion_zeigt_das_repair_den_text_ohne_version(hass, clock, kern):
    await _setup(hass)
    ereignis = {**_ereignis(schema=5), "mindest_integration": kern}

    await _fire(hass, ereignis)

    issue = _issues(hass)["integration_veraltet"]
    assert issue.translation_key == "integration_veraltet_ohne_version"
    assert not issue.translation_placeholders
    assert "x" * 200 not in json.dumps(issue.translation_placeholders or {})


@pytest.mark.parametrize("kern", ["1.4.0", "2.10.3", "1.4.0-rc1", "1.4.0+build5", "1.4.0.dev2"])
async def test_eine_strenge_mindestversion_wird_im_repair_genannt(hass, clock, kern):
    await _setup(hass)

    await _fire(hass, {**_ereignis(schema=5), "mindest_integration": kern})

    issue = _issues(hass)["integration_veraltet"]
    assert issue.translation_key == "integration_veraltet"
    assert issue.translation_placeholders == {"mindest_integration": kern}


@pytest.mark.parametrize("schema", ["9" * 5000, 10**30, 4.5, ["x"], {"a": 1}])
async def test_das_rohe_schema_eines_fremden_ereignisses_landet_nicht_im_log(hass, clock, caplog, schema):
    await _setup(hass)
    caplog.clear()

    await _fire(hass, {**_ereignis(), "schema": schema})

    assert "9" * 50 not in caplog.text and "1000000000000000000000000000000" not in caplog.text
    assert "'a': 1" not in caplog.text and "['x']" not in caplog.text
    assert max((len(zeile) for zeile in caplog.text.splitlines()), default=0) < 400


async def test_ein_lesbares_ereignis_hebt_den_befund_und_das_repair_wieder_auf(hass, clock):
    await _setup(hass)
    await _fire(hass, _ereignis(schema=5))

    await _fire(hass, _ereignis(raum_soll=21.5))

    assert hass.states.get(STATUS).state == "regelt"
    assert hass.states.get(BOOST).state != STATE_UNAVAILABLE
    assert float(hass.states.get(WUNSCH).state) == 21.5
    assert not set(_issues(hass)) & {"integration_veraltet", "addon_veraltet"}


async def test_der_wechsel_von_einem_befund_zum_anderen_tauscht_das_repair(hass, clock):
    await _setup(hass)
    await _fire(hass, _ereignis(schema=5))
    await _fire(hass, _ereignis(schema=3))

    assert hass.states.get(STATUS).state == "addon_veraltet"
    assert set(_issues(hass)) == {"addon_veraltet"}


async def test_ein_fremdes_geraet_und_ein_schema_ohne_zahl_aendern_den_befund_nicht(hass, clock):
    await _setup(hass)

    await _fire(hass, {**_ereignis(schema=5), "geraet_id": "shg-b"})
    await _fire(hass, {**_ereignis(), "schema": "x"})
    await _fire(hass, {**_ereignis(), "schema": True})
    await _fire(hass, {k: v for k, v in _ereignis().items() if k != "schema"})

    assert hass.states.get(STATUS).state == STATE_UNKNOWN
    assert not _issues(hass)


@pytest.mark.parametrize("befund", ["integration_veraltet", "addon_veraltet"])
async def test_der_wunsch_lehnt_beim_befund_mit_der_uebersetzung_ab(hass, clock, befund):
    await _setup(hass)
    await _fire(hass, _ereignis())
    await _fire(hass, _ereignis(schema=5 if befund == "integration_veraltet" else 3))
    wuensche = async_capture_events(hass, WUNSCH_EVENT)

    with pytest.raises(ServiceValidationError) as fehler:
        await _entity(hass, WUNSCH).async_set_native_value(22.0)

    assert (fehler.value.translation_domain, fehler.value.translation_key) == (DOMAIN, befund)
    await hass.async_block_till_done()
    assert wuensche == []


async def test_der_dienst_set_value_ueberspringt_die_nicht_verfuegbare_entity_still_und_feuert_nichts(hass, clock):
    """Home Assistant ruft den Dienst fuer eine nicht verfuegbare Entity gar nicht auf: keine Ablehnung mit
    Uebersetzung, aber auch kein Wunsch-Ereignis (die Ablehnung gilt nur fuer den direkten Aufruf der Entity)."""
    await _setup(hass)
    await _fire(hass, _ereignis())
    await _fire(hass, _ereignis(schema=5))
    assert hass.states.get(WUNSCH).state == STATE_UNAVAILABLE
    wuensche = async_capture_events(hass, WUNSCH_EVENT)

    await hass.services.async_call("number", "set_value", {"entity_id": WUNSCH, "value": 22.0}, blocking=True)
    await hass.async_block_till_done()

    assert wuensche == []


async def test_der_befund_hat_vorrang_vor_dem_zustand_des_addons_aber_nicht_vor_dem_waechter(hass, clock):
    entry = await _setup(hass)
    await _fire(hass, _ereignis(status="notbetrieb"))
    assert hass.states.get(STATUS).state == "notbetrieb"
    await _fire(hass, _ereignis(schema=5))
    assert hass.states.get(STATUS).state == "integration_veraltet"

    entry.runtime_data._set_watchdog_status("reagiert_nicht")
    await hass.async_block_till_done()

    assert hass.states.get(STATUS).state == "reagiert_nicht"


async def test_entladen_entfernt_das_repair(hass, clock):
    entry = await _setup(hass)
    await _fire(hass, _ereignis(schema=5))
    assert "integration_veraltet" in _issues(hass)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert "integration_veraltet" not in _issues(hass)


# --- unbekannter Status oder Code verwirft das Ereignis nicht ---

@pytest.mark.parametrize("status", ["neu_unbekannt", 7, None])
async def test_ein_unbekannter_status_verwirft_das_ereignis_nicht(hass, clock, status):
    entry = await _setup(hass)

    await _fire(hass, {**_ereignis(raum_soll=21.5, boost="komfort"), "status": status})

    assert hass.states.get(STATUS).state == STATE_UNKNOWN
    assert float(hass.states.get(WUNSCH).state) == 21.5
    assert hass.states.get(BOOST).state == "komfort"
    assert entry.runtime_data.kreis(1)["raum_soll"] == 21.5
    assert not set(_issues(hass)) & {"integration_veraltet", "addon_veraltet"}


async def test_ein_fehlender_status_verwirft_das_ereignis_nicht(hass, clock):
    entry = await _setup(hass)

    await _fire(hass, {k: v for k, v in _ereignis(raum_soll=21.5).items() if k != "status"})

    assert float(hass.states.get(WUNSCH).state) == 21.5
    assert entry.runtime_data.status is None


async def test_ein_unbekannter_hinweisschluessel_ergibt_den_rueckfall_und_das_ereignis_gilt(hass, clock):
    await _setup(hass)
    hinweis = {"key": "neuer_hinweis", "stufe": "info", "text": "Etwas Neues"}

    await _fire(hass, _ereignis(raum_soll=21.5, geraet_hinweise=[hinweis]))

    assert float(hass.states.get(WUNSCH).state) == 21.5
    assert _issues(hass)["hinweis_neuer_hinweis"].translation_key == "geraet_hinweis"


# --- Aushandlung nach v5_schemas ---

async def test_meldet_das_addon_ein_gelesenes_schema_sendet_die_integration_in_genau_diesem(hass, clock, monkeypatch):
    monkeypatch.setattr(f"{CO}.V5_SCHEMAS", (3, 4))
    wuensche = async_capture_events(hass, WUNSCH_EVENT)
    integration = async_capture_events(hass, INTEGRATION_EVENT)
    await _setup(hass)
    assert integration[0].data["v5_schemas"] == [3, 4]

    await _fire(hass, _ereignis(schema=3, raum_soll=21.5))
    assert hass.states.get(STATUS).state == "regelt"
    await _entity(hass, WUNSCH).async_set_native_value(22.0)
    await _fire(hass, _ereignis(schema=4, raum_soll=22.0))
    await _entity(hass, WUNSCH).async_set_native_value(22.5)

    assert [e.data["schema"] for e in wuensche] == [3, 4]


async def test_mit_mehreren_gelesenen_schemata_zaehlt_hoeher_und_niedriger_als_alle(hass, clock, monkeypatch):
    monkeypatch.setattr(f"{CO}.V5_SCHEMAS", (3, 4))
    await _setup(hass)

    await _fire(hass, _ereignis(schema=5))
    assert hass.states.get(STATUS).state == "integration_veraltet"
    await _fire(hass, _ereignis(schema=2))
    assert hass.states.get(STATUS).state == "addon_veraltet"
    await _fire(hass, _ereignis(schema=3))
    assert hass.states.get(STATUS).state == "regelt"


# --- Texte nur ueber die Uebersetzungsdateien ---

@pytest.mark.parametrize("path", ["strings.json", "translations/en.json", "translations/de.json"])
def test_befunde_haben_status_text_repair_und_ablehnung_in_jeder_uebersetzung(path):
    texte = json.loads((_COMPONENT / path).read_text(encoding="utf-8"))
    for befund in ("integration_veraltet", "addon_veraltet"):
        assert befund in STATUS_SENSOR_VALUES
        assert texte["entity"]["sensor"]["status"]["state"][befund].strip()
        assert texte["exceptions"][befund]["message"].strip()
        assert texte["issues"][befund]["title"].strip() and texte["issues"][befund]["description"].strip()
    assert "{mindest_integration}" in texte["issues"]["integration_veraltet"]["description"]
    ohne = texte["issues"]["integration_veraltet_ohne_version"]
    assert ohne["title"].strip() and "{" not in ohne["description"]
    assert set(texte["entity"]["sensor"]["status"]["state"]) == set(STATUS_SENSOR_VALUES)


async def test_die_repair_texte_sind_aufloesbar(hass, clock):
    await _setup(hass)
    await _fire(hass, _ereignis(schema=5))
    issue = _issues(hass)["integration_veraltet"]

    for sprache in ("en", "de"):
        texte = await async_get_translations(hass, sprache, "issues", [DOMAIN])
        beschreibung = texte[f"component.{DOMAIN}.issues.{issue.translation_key}.description"]
        assert KERN in beschreibung.format(**issue.translation_placeholders)
