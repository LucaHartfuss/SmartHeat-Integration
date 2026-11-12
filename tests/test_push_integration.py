"""Push im Zusammenspiel mit dem Coordinator: die Ziele kommen aus dem Status-Ereignis, nur die Rot-Befunde des
Waechters gehen als Push hinaus (Versions- und Mehrfach-Befunde nicht), ein Waechter ohne Befund schliesst nach dem Start
einen Eintrag aus der Zeit davor, das Loeschen des Eintrags loescht den Store."""
import pytest
from aiohasupervisor.models import AddonState
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.smartheat.const import PUSH_ABGLEICH_SECONDS, STATUS_EVENT

from .addon_fakes import make_device_entry, status_event
from .supervisor_fakes import BRIDGE_SLUG, installed_addon

CO = "custom_components.smartheat.coordinator"
TENANT = "wohnung1"
DIENST = "notify.mobile_app_pixel"
STORE = "smartheat.push"


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(f"{CO}._now", lambda: now["t"])
    return now


@pytest.fixture
def dienst(hass):
    return async_mock_service(hass, "notify", "mobile_app_pixel")


async def _setup(hass):
    entry = make_device_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _ereignis(hass, **felder):
    hass.bus.async_fire(STATUS_EVENT, status_event(TENANT, "regelt", **felder))
    await hass.async_block_till_done()


async def _pruefungen(hass, entry, clock, anzahl):
    for _ in range(anzahl):
        clock["t"] += 300
        await entry.runtime_data.async_check()
    await hass.async_block_till_done()


async def test_a_stopped_addon_waits_for_the_targets_of_the_event_and_is_then_pushed(
        hass, supervisor, clock, dienst):
    next(a for a in supervisor.installed if a.slug == BRIDGE_SLUG).state = AddonState.STOPPED
    entry = await _setup(hass)

    await _pruefungen(hass, entry, clock, 2)
    assert entry.runtime_data.watchdog_status == "addon_gestoppt"
    assert dienst == []  # noch kein Ereignis, also keine Ziele bekannt

    await _ereignis(hass, benachrichtigung=[DIENST])
    assert len(dienst) == 1
    assert dienst[0].data["title"] == "SmartHeat: add-on not running"


async def test_a_missing_addon_is_pushed_as_red(hass, supervisor, clock, dienst):
    supervisor.installed = []
    entry = await _setup(hass)
    await _ereignis(hass, benachrichtigung=[DIENST])

    await _pruefungen(hass, entry, clock, 3)

    assert entry.runtime_data.watchdog_status == "addon_fehlt"
    assert len(dienst) == 1


async def test_a_double_addon_is_not_pushed(hass, supervisor, clock, dienst):
    supervisor.installed = [installed_addon(BRIDGE_SLUG), installed_addon(BRIDGE_SLUG)]
    entry = await _setup(hass)
    await _ereignis(hass, benachrichtigung=[DIENST])

    await _pruefungen(hass, entry, clock, 2)

    assert entry.runtime_data.watchdog_status == "addon_mehrfach"
    assert dienst == []


@pytest.mark.parametrize("schema", [1, 99])
async def test_a_version_finding_is_not_pushed(hass, supervisor, clock, dienst, schema):
    entry = await _setup(hass)

    await _ereignis(hass, benachrichtigung=[DIENST], schema=schema)
    await _pruefungen(hass, entry, clock, 1)

    assert entry.runtime_data.version_status is not None
    assert dienst == []


async def test_an_event_without_the_push_fields_leaves_the_targets_empty(hass, supervisor, clock, dienst):
    entry = await _setup(hass)

    await _ereignis(hass)

    assert entry.runtime_data.push.ziele == ()


async def test_the_event_fields_set_targets_rules_and_muted_codes(hass, supervisor, clock, dienst):
    entry = await _setup(hass)

    await _ereignis(hass, benachrichtigung=[DIENST, "script.boese"], hinweise_aus=["gesperrt"],
                    push_regeln={"ruhe_von": 21, "ruhe_bis": 6, "tageslimit": 3})

    push = entry.runtime_data.push
    assert push.ziele == (DIENST,)
    assert (push.ruhe_von, push.ruhe_bis, push.tageslimit) == (21, 6, 3)


async def test_a_watchdog_without_finding_closes_an_entry_from_before_the_start_after_the_grace_period(
        hass, hass_storage, supervisor, clock, dienst):
    """Der Store kennt einen offenen Befund (HA wurde neu gestartet, das Add-on laeuft wieder): der Waechter schliesst
    ihn, sobald er lange genug gelaufen ist, um den Befund wiederzufinden; ein neuer Befund wird wieder gemeldet."""
    hass_storage[STORE] = {"version": 1, "minor_version": 1, "key": STORE, "data": {
        "tag": "", "gesendet": 0, "sammel": False, "eintraege": {"waechter|0": {
            "code": "addon_gestoppt", "key": "waechter", "kreis": None, "stufe": "rot", "ausstehend": False,
            "letzte": 1.0, "versuch": 1.0, "versuch_seit": 1.0, "versuche": 1}}}}
    entry = await _setup(hass)
    push = entry.runtime_data.push
    assert push.eintraege == 1

    await _ereignis(hass)
    await _pruefungen(hass, entry, clock, 1)
    assert push.eintraege == 1  # die Schonfrist ist noch nicht um

    clock["t"] += PUSH_ABGLEICH_SECONDS
    await _ereignis(hass)
    await _pruefungen(hass, entry, clock, 1)
    assert push.eintraege == 0

    next(a for a in supervisor.installed if a.slug == BRIDGE_SLUG).state = AddonState.STOPPED
    await _ereignis(hass, benachrichtigung=[DIENST])
    await _pruefungen(hass, entry, clock, 2)
    assert len(dienst) == 1


async def test_removing_the_entry_deletes_the_store_of_the_push(hass, hass_storage, supervisor, clock, dienst):
    supervisor.installed = []
    entry = await _setup(hass)
    await _ereignis(hass, benachrichtigung=[DIENST])
    await _pruefungen(hass, entry, clock, 3)
    assert STORE in hass_storage

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert STORE not in hass_storage
