"""Geraetebaum: ein Geraet fuer die Anlage und je Heizkreis eines, dynamisch nach der Kreisliste des Status-Ereignisses
(Name aus dem Abo des Kreises, Rueckfall in der Uebersetzung, Kreis faellt erst nach zwei Ereignissen mit gleicher
Konfigurationsversion weg, Obergrenze der Kreise, Handloeschen nur fuer nicht mehr gemeldete Kreise)."""
import copy
import json
import logging
from pathlib import Path

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.smartheat import async_remove_config_entry_device
from custom_components.smartheat.const import DOMAIN, EINRICHTUNG_MAX_KREISE, STATUS_EVENT

from .addon_fakes import make_device_entry, status_event

TENANT = "wohnung1"
ANLAGE = (DOMAIN, TENANT)
_COMPONENT = Path(__file__).parent.parent / "custom_components" / "smartheat"


def _kennung(nummer):
    return (DOMAIN, f"{TENANT}_k{nummer}")


def _kreis(nummer, name="auto"):
    kreis = copy.deepcopy(status_event(TENANT, "regelt")["kreise"][0])
    kreis.update({"kreis": nummer, "name": f"Kreis-{nummer}" if name == "auto" else name})
    return kreis


def _ereignis(nummern=(1, 2), konfig_version=7, **overrides):
    kreise = overrides.pop("kreise", None)
    if kreise is None:
        kreise = [_kreis(n) for n in nummern]
    ereignis = status_event(TENANT, "regelt", kreise=kreise, **overrides)
    if konfig_version is not None:
        ereignis["konfig_version"] = konfig_version
    return ereignis


async def _setup(hass):
    entry = make_device_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _fire(hass, event):
    hass.bus.async_fire(STATUS_EVENT, event)
    await hass.async_block_till_done()


def _geraet(hass, kennung):
    eintraege = hass.config_entries.async_entries(DOMAIN)
    return dr.async_get(hass).async_get_device_by_identifier(kennung, eintraege[0].entry_id)


def _kreis_geraete(hass, entry):
    return [d for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
            if any(k[1].startswith(f"{TENANT}_k") for k in d.identifiers)]


# --- Anlage ---

async def test_die_anlage_ist_ein_geraet_das_vor_dem_ersten_ereignis_den_produktnamen_traegt(hass):
    entry = await _setup(hass)

    anlage = _geraet(hass, ANLAGE)
    assert anlage is not None and entry.entry_id in anlage.config_entries
    assert anlage.name == "SmartHeat" and anlage.manufacturer == "SmartHeat"
    assert anlage.via_device_id is None and anlage.configuration_url is None


async def test_die_anlage_nimmt_name_und_version_aus_dem_ereignis(hass):
    await _setup(hass)

    await _fire(hass, _ereignis(anlage_name="Haus am See", addon_version="1.2.3"))

    anlage = _geraet(hass, ANLAGE)
    assert anlage.name == "Haus am See" and anlage.sw_version == "1.2.3"
    assert anlage.configuration_url is None
    assert anlage.model == "Home Assistant" and anlage.model_id == "vaillant_vrc720"


@pytest.mark.parametrize("name", [None, "", "   ", 7, ["x"]])
async def test_ohne_brauchbaren_anlagennamen_gilt_der_produktname(hass, name):
    await _setup(hass)
    await _fire(hass, _ereignis(anlage_name="Haus am See"))

    await _fire(hass, _ereignis(anlage_name=name))

    assert _geraet(hass, ANLAGE).name == "SmartHeat"


async def test_ein_vom_nutzer_vergebener_name_der_anlage_bleibt(hass):
    await _setup(hass)
    await _fire(hass, _ereignis(anlage_name="Haus"))
    anlage = _geraet(hass, ANLAGE)
    dr.async_get(hass).async_update_device(anlage.id, name_by_user="Meine Heizung")

    await _fire(hass, _ereignis(anlage_name="Haus neu"))

    anlage = _geraet(hass, ANLAGE)
    assert anlage.name == "Haus neu" and anlage.name_by_user == "Meine Heizung"


# --- Kreise ---

async def test_je_kreis_ein_geraet_mit_dem_namen_des_abos_ueber_der_anlage(hass):
    entry = await _setup(hass)

    await _fire(hass, _ereignis(nummern=(1, 2)))

    anlage = _geraet(hass, ANLAGE)
    for nummer in (1, 2):
        kreis = _geraet(hass, _kennung(nummer))
        assert kreis is not None and entry.entry_id in kreis.config_entries
        assert kreis.name == f"Kreis-{nummer}" and kreis.via_device_id == anlage.id
        assert kreis.manufacturer == "SmartHeat" and kreis.configuration_url is None
    assert len(_kreis_geraete(hass, entry)) == 2


async def test_der_name_des_kreises_folgt_dem_abo_ein_umbenennen_des_nutzers_bleibt(hass):
    await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1,)))
    kreis = _geraet(hass, _kennung(1))
    dr.async_get(hass).async_update_device(kreis.id, name_by_user="Bad")

    await _fire(hass, _ereignis(kreise=[_kreis(1, "Obergeschoss")]))

    kreis = _geraet(hass, _kennung(1))
    assert kreis.name == "Obergeschoss" and kreis.name_by_user == "Bad"


@pytest.mark.parametrize("name", [None, "", "  ", 3, {"a": 1}])
async def test_ohne_brauchbaren_namen_gilt_der_rueckfall_der_uebersetzung(hass, name):
    await _setup(hass)

    await _fire(hass, _ereignis(kreise=[_kreis(1), _kreis(3, name)]))

    assert _geraet(hass, _kennung(3)).name == "Heating circuit 3"


async def test_der_rueckfall_folgt_der_sprache_von_home_assistant(hass):
    hass.config.language = "de"
    await _setup(hass)

    await _fire(hass, _ereignis(kreise=[_kreis(1, None)]))

    assert _geraet(hass, _kennung(1)).name == "Heizkreis 1"


async def test_ein_zu_langer_name_wird_gekuerzt(hass):
    await _setup(hass)

    await _fire(hass, _ereignis(kreise=[_kreis(1, "x" * 500)]))

    assert _geraet(hass, _kennung(1)).name == "x" * 100


async def test_ein_neuer_kreis_im_ereignis_legt_sein_geraet_an_ohne_die_uebrigen_zu_aendern(hass):
    entry = await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1,)))
    erstes = _geraet(hass, _kennung(1))

    await _fire(hass, _ereignis(nummern=(1, 2)))

    assert _geraet(hass, _kennung(1)).id == erstes.id
    assert _geraet(hass, _kennung(2)) is not None and len(_kreis_geraete(hass, entry)) == 2


async def test_ein_kreis_ohne_gueltige_nummer_oder_doppelt_aendert_den_baum_nicht(hass):
    entry = await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1, 2)))

    for kreise in ([_kreis(1), _kreis(1)], [_kreis(1), _kreis(0)], [_kreis(1), _kreis(True)]):
        await _fire(hass, _ereignis(kreise=kreise))

    assert len(_kreis_geraete(hass, entry)) == 2


# --- Wegfall nach zwei Ereignissen mit gleicher Konfigurationsversion ---

async def test_ein_kreis_faellt_erst_nach_zwei_ereignissen_mit_gleicher_version_weg(hass):
    entry = await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1, 2)))

    await _fire(hass, _ereignis(nummern=(1,)))
    assert _geraet(hass, _kennung(2)) is not None  # ein einzelner Ausreisser raeumt nichts ab

    await _fire(hass, _ereignis(nummern=(1,)))
    assert _geraet(hass, _kennung(2)) is None
    assert _geraet(hass, _kennung(1)) is not None and len(_kreis_geraete(hass, entry)) == 1
    assert 2 not in entry.runtime_data.kreise


async def test_eine_andere_konfigurationsversion_im_zweiten_ereignis_zaehlt_neu(hass):
    await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1, 2), konfig_version=7))

    await _fire(hass, _ereignis(nummern=(1,), konfig_version=7))
    await _fire(hass, _ereignis(nummern=(1,), konfig_version=8))
    assert _geraet(hass, _kennung(2)) is not None

    await _fire(hass, _ereignis(nummern=(1,), konfig_version=8))
    assert _geraet(hass, _kennung(2)) is None


async def test_kehrt_der_kreis_zwischen_zwei_ereignissen_zurueck_beginnt_die_zaehlung_neu(hass):
    await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1, 2)))

    await _fire(hass, _ereignis(nummern=(1,)))
    await _fire(hass, _ereignis(nummern=(1, 2)))
    await _fire(hass, _ereignis(nummern=(1,)))

    assert _geraet(hass, _kennung(2)) is not None


async def test_ohne_konfigurationsversion_im_ereignis_faellt_kein_kreisgeraet_weg(hass):
    """Ohne Version fehlt der Beleg, dass die Kreisliste gewollt kleiner ist: das Geraet bleibt, auch nach vielen
    Ereignissen; entfernen kann es dann nur der Kunde von Hand."""
    await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1, 2), konfig_version=None))

    for _ in range(4):
        await _fire(hass, _ereignis(nummern=(1,), konfig_version=None))

    assert _geraet(hass, _kennung(2)) is not None


async def test_ein_ereignis_ohne_version_zwischen_zwei_mit_version_unterbricht_die_zaehlung(hass):
    await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1, 2), konfig_version=7))
    await _fire(hass, _ereignis(nummern=(1,), konfig_version=7))

    await _fire(hass, _ereignis(nummern=(1,), konfig_version=None))
    await _fire(hass, _ereignis(nummern=(1,), konfig_version=7))

    assert _geraet(hass, _kennung(2)) is not None
    await _fire(hass, _ereignis(nummern=(1,), konfig_version=7))
    assert _geraet(hass, _kennung(2)) is None


async def test_eine_leere_kreisliste_zaehlt_nie_als_beleg_fuer_den_wegfall(hass):
    await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1, 2), konfig_version=7))

    for _ in range(3):
        await _fire(hass, _ereignis(kreise=[], konfig_version=7))
    assert _geraet(hass, _kennung(1)) is not None and _geraet(hass, _kennung(2)) is not None

    # Die leere Liste unterbricht auch eine begonnene Zaehlung.
    await _fire(hass, _ereignis(nummern=(1,), konfig_version=7))
    await _fire(hass, _ereignis(kreise=[], konfig_version=7))
    await _fire(hass, _ereignis(nummern=(1,), konfig_version=7))
    assert _geraet(hass, _kennung(2)) is not None


async def test_ein_ungueltiges_ereignis_zaehlt_nicht_als_zweites(hass):
    await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1, 2)))
    await _fire(hass, _ereignis(nummern=(1,)))

    await _fire(hass, _ereignis(kreise=[_kreis(1), _kreis(1)]))  # verworfen
    assert _geraet(hass, _kennung(2)) is not None

    await _fire(hass, _ereignis(nummern=(1,)))
    assert _geraet(hass, _kennung(2)) is None


async def test_nach_dem_neustart_fehlt_ein_altes_kreisgeraet_erst_nach_zwei_ereignissen(hass):
    entry = await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1, 2)))
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _geraet(hass, _kennung(2)) is not None  # kein Abraeumen ohne Ereignis

    await _fire(hass, _ereignis(nummern=(1,)))
    assert _geraet(hass, _kennung(2)) is not None
    await _fire(hass, _ereignis(nummern=(1,)))

    assert _geraet(hass, _kennung(2)) is None


async def test_mit_dem_kreisgeraet_verschwinden_seine_entities(hass):
    entry = await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1, 2)))
    kreis = _geraet(hass, _kennung(2))
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", DOMAIN, f"{TENANT}_k2_zustand", config_entry=entry, device_id=kreis.id)
    eigene = registry.async_get_or_create("sensor", DOMAIN, f"{TENANT}_k1_zustand", config_entry=entry,
                                          device_id=_geraet(hass, _kennung(1)).id)

    await _fire(hass, _ereignis(nummern=(1,)))
    await _fire(hass, _ereignis(nummern=(1,)))

    unique_ids = {e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    assert f"{TENANT}_k2_zustand" not in unique_ids and eigene.unique_id in unique_ids


async def test_die_anlage_und_ihre_entities_bleiben_wenn_die_kreisliste_leer_ist(hass):
    entry = await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1, 2)))

    await _fire(hass, _ereignis(kreise=[]))
    await _fire(hass, _ereignis(kreise=[]))

    assert _geraet(hass, ANLAGE) is not None and len(_kreis_geraete(hass, entry)) == 2
    assert hass.states.get("sensor.smartheat_status") is not None


# --- Obergrenze ---

async def test_die_obergrenze_der_kreise_ist_die_lokale(hass):
    assert EINRICHTUNG_MAX_KREISE == 4
    entry = await _setup(hass)

    await _fire(hass, _ereignis(nummern=tuple(range(1, EINRICHTUNG_MAX_KREISE + 1))))

    assert len(_kreis_geraete(hass, entry)) == EINRICHTUNG_MAX_KREISE


async def test_mehr_kreise_als_die_obergrenze_lehnt_das_ereignis_ab_mit_log_ohne_inhalt(hass, caplog):
    entry = await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1, 2)))
    zu_viele = [_kreis(n, f"Geheim-{n}") for n in range(1, EINRICHTUNG_MAX_KREISE + 2)]

    with caplog.at_level(logging.WARNING):
        await _fire(hass, _ereignis(kreise=zu_viele, anlage_name="Geheimname"))

    assert len(_kreis_geraete(hass, entry)) == 2 and _geraet(hass, _kennung(5)) is None
    assert _geraet(hass, ANLAGE).name != "Geheimname"
    assert "Geheim" not in caplog.text
    assert any(r.levelno == logging.WARNING and "Kreis" in r.getMessage() for r in caplog.records)


async def test_eine_kreisnummer_ueber_der_obergrenze_lehnt_das_ereignis_ab(hass, caplog):
    entry = await _setup(hass)

    with caplog.at_level(logging.WARNING):
        await _fire(hass, _ereignis(kreise=[_kreis(1), _kreis(EINRICHTUNG_MAX_KREISE + 1, "Geheim")]))

    assert not _kreis_geraete(hass, entry) and "Geheim" not in caplog.text


# --- Handloeschen ---

async def test_ein_kreisgeraet_laesst_sich_von_hand_loeschen_nur_wenn_der_kreis_nicht_mehr_gemeldet_wird(hass):
    entry = await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1, 2)))
    await _fire(hass, _ereignis(nummern=(1,)))  # Kreis 2 fehlt, ist aber noch nicht abgeraeumt
    kreis1, kreis2 = _geraet(hass, _kennung(1)), _geraet(hass, _kennung(2))

    assert await async_remove_config_entry_device(hass, entry, kreis1) is False
    assert await async_remove_config_entry_device(hass, entry, kreis2) is True


async def test_die_anlage_laesst_sich_nicht_von_hand_loeschen(hass):
    entry = await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1,)))

    assert await async_remove_config_entry_device(hass, entry, _geraet(hass, ANLAGE)) is False


async def test_vor_dem_ersten_ereignis_laesst_sich_kein_kreisgeraet_von_hand_loeschen(hass):
    entry = await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1,)))
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert await async_remove_config_entry_device(hass, entry, _geraet(hass, _kennung(1))) is False


# --- Entities haengen an ihrem Geraet ---

async def test_die_entities_haengen_am_geraet_der_anlage_bzw_ihres_kreises(hass):
    entry = await _setup(hass)
    await _fire(hass, _ereignis(nummern=(1, 2)))

    anlage = _geraet(hass, ANLAGE)
    kreise = {d.id: d for d in _kreis_geraete(hass, entry)}
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert len(kreise) == 2
    for e in entities:
        if f"{TENANT}_k" in e.unique_id:
            assert e.device_id in kreise, e.unique_id
        else:
            assert e.device_id == anlage.id, e.unique_id


def test_das_geraet_des_kreises_hat_in_allen_sprachen_einen_rueckfallnamen():
    for pfad in ("strings.json", "translations/en.json", "translations/de.json"):
        text = json.loads((_COMPONENT / pfad).read_text(encoding="utf-8"))["device"]["kreis"]["name"]
        assert "{kreis}" in text, pfad
