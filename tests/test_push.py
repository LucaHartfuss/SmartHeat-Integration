"""Push-Kern der Integration: Rot sofort und wiederholt, Orange einmal und in der Ruhezeit erst an deren Ende, Gelb nie,
Tageslimit mit Sammelnachricht, Ziele nur nach Muster, Zustand ueber einen Neustart, Logs ohne Dienst- und Kreisnamen."""
import json
import logging
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.smartheat.const import EINRICHTUNG_MAX_HANDYS
from custom_components.smartheat.push import Push

DIENST = "notify.mobile_app_pixel"
STORE = "smartheat.push"
TAG = 86400
REGELN = {"ruhe_von": 22, "ruhe_bis": 7, "tageslimit": 5}


def _um(freezer, uhrzeit, tag=11):
    """Die Uhr auf eine Ortszeit (Europa/Berlin, im November UTC+1) stellen."""
    freezer.move_to(f"2026-11-{tag:02d} {uhrzeit}+01:00")


@pytest.fixture
async def push(hass, hass_storage, freezer):
    await hass.config.async_set_time_zone("Europe/Berlin")
    hass.config.language = "de"
    _um(freezer, "12:00:00")
    ergebnis = Push(hass)
    await ergebnis.async_laden()
    ergebnis.ziele_setzen([DIENST], [], REGELN)
    return ergebnis


@pytest.fixture
def dienst(hass):
    """Der Notify-Dienst des Handys, aufgezeichnet."""
    return async_mock_service(hass, "notify", "mobile_app_pixel")


def _ausfall(hass, name="mobile_app_pixel"):
    """Ein Dienst, der scheitert, solange `zustand["ok"]` falsch ist; der Fehlertext nennt den Dienstnamen."""
    zustand = {"ok": False, "aufrufe": 0}

    async def handler(call):
        zustand["aufrufe"] += 1
        if not zustand["ok"]:
            raise HomeAssistantError(f"notify.{name} kaputt")

    hass.services.async_register("notify", name, handler)
    return zustand


async def _rot(push, key="waechter", code="addon_gestoppt", kreis=None):
    await push.melden(code, key, kreis, "rot", True)


async def _orange(push, key="hinweis_a", code="gesperrt", kreis=1):
    await push.melden(code, key, kreis, "orange", True)


# --- Rot ---

async def test_red_is_sent_at_once_with_the_title_of_the_code_in_the_language_of_home_assistant(push, dienst):
    await _rot(push)

    assert len(dienst) == 1
    assert dienst[0].data == {"title": "SmartHeat: Add-on läuft nicht", "message": "Bitte in Home Assistant unter Einstellungen → Reparaturen nachsehen."}


async def test_the_title_follows_the_language_and_falls_back_to_english(hass, push, dienst):
    hass.config.language = "en"
    await _rot(push)
    hass.config.language = "fr"
    await _rot(push, key="zweiter")

    assert dienst[0].data["title"] == "SmartHeat: add-on not running"
    assert dienst[1].data["title"] == dienst[0].data["title"]
    assert dienst[1].data["message"] == "Please check Settings → Repairs in Home Assistant."


@pytest.mark.parametrize("uhrzeit", ["22:00:00", "23:59:59", "03:00:00", "06:59:59"])
async def test_red_ignores_the_quiet_hours(push, dienst, freezer, uhrzeit):
    _um(freezer, uhrzeit)
    await _rot(push)

    assert len(dienst) == 1


async def test_red_is_never_collected_and_stays_outside_the_daily_limit(push, dienst):
    push.ziele_setzen([DIENST], [], {"ruhe_von": 22, "ruhe_bis": 7, "tageslimit": 1})
    await _orange(push, key="eins")
    for nummer in range(4):
        await _rot(push, key=f"rot{nummer}")
    await _orange(push, key="zwei")

    titel = [aufruf.data["title"] for aufruf in dienst]
    assert titel.count("SmartHeat: Add-on läuft nicht") == 4
    assert len(titel) == 6  # erste Orange, vier Rot, Sammelnachricht statt der zweiten Orange
    assert titel[-1] == "SmartHeat: mehrere Meldungen"


async def test_open_red_is_pushed_once_and_reminded_every_24_hours_while_it_stays_open(push, dienst, freezer):
    await _rot(push)
    await _rot(push)
    _um(freezer, "11:59:00", tag=12)
    await push.async_verarbeiten()
    assert len(dienst) == 1

    _um(freezer, "12:00:00", tag=12)
    await push.async_verarbeiten()
    assert len(dienst) == 2
    await push.async_verarbeiten()
    assert len(dienst) == 2

    _um(freezer, "12:00:00", tag=13)
    await push.async_verarbeiten()
    assert len(dienst) == 3


async def test_a_closed_red_is_not_reminded(push, dienst, freezer):
    await _rot(push)
    await push.melden("addon_gestoppt", "waechter", None, "rot", False)
    _um(freezer, "12:00:00", tag=13)
    await push.async_verarbeiten()

    assert len(dienst) == 1


async def test_red_that_closes_and_opens_again_is_a_new_push(push, dienst):
    await _rot(push)
    await push.melden("addon_gestoppt", "waechter", None, "rot", False)
    await _rot(push)

    assert len(dienst) == 2


async def test_a_red_with_a_new_code_under_the_same_key_is_pushed_again(push, dienst):
    await _rot(push, code="addon_gestoppt")
    await _rot(push, code="reagiert_nicht")

    assert [aufruf.data["title"] for aufruf in dienst] == [
        "SmartHeat: Add-on läuft nicht", "SmartHeat: Add-on meldet sich nicht"]


async def test_red_that_is_new_against_the_store_only_pushes_after_a_restart_of_home_assistant(hass, push, dienst, freezer):
    await _rot(push)
    neu = Push(hass)
    await neu.async_laden()
    neu.ziele_setzen([DIENST], [], REGELN)
    await _rot(neu)
    await _rot(neu, key="zweites", code="addon_fehlt")
    _um(freezer, "13:00:00")
    await neu.async_verarbeiten()

    assert [aufruf.data["title"] for aufruf in dienst] == [
        "SmartHeat: Add-on läuft nicht", "SmartHeat: Add-on nicht installiert"]


async def test_the_reminder_clock_survives_a_restart(hass, push, dienst, freezer):
    await _rot(push)
    neu = Push(hass)
    await neu.async_laden()
    neu.ziele_setzen([DIENST], [], REGELN)
    _um(freezer, "12:00:00", tag=12)
    await neu.async_verarbeiten()

    assert len(dienst) == 2


async def test_a_failed_red_is_retried_every_five_minutes_until_it_gets_through(hass, push, freezer):
    zustand = _ausfall(hass)
    await _rot(push)
    assert zustand["aufrufe"] == 1

    _um(freezer, "12:04:00")
    await push.async_verarbeiten()
    assert zustand["aufrufe"] == 1  # zu frueh
    _um(freezer, "12:05:00")
    await push.async_verarbeiten()
    assert zustand["aufrufe"] == 2

    zustand["ok"] = True
    _um(freezer, "12:10:00")
    await push.async_verarbeiten()
    assert zustand["aufrufe"] == 3
    _um(freezer, "12:20:00")
    await push.async_verarbeiten()
    assert zustand["aufrufe"] == 3  # gelungen: kein weiterer Versuch bis zur Erinnerung


async def test_a_red_that_cannot_be_sent_for_24_hours_is_given_up_until_the_next_reminder(hass, push, freezer):
    zustand = _ausfall(hass)
    await _rot(push)
    for stunde in range(1, 24):
        _um(freezer, f"{(12 + stunde) % 24:02d}:00:00", tag=11 + (12 + stunde) // 24)
        await push.async_verarbeiten()
    versuche = zustand["aufrufe"]
    assert versuche == 24

    _um(freezer, "12:00:00", tag=12)
    await push.async_verarbeiten()  # nach 24 h aufgegeben, der Zaehler beginnt neu
    assert zustand["aufrufe"] == versuche
    _um(freezer, "12:05:00", tag=12)
    await push.async_verarbeiten()
    assert zustand["aufrufe"] == versuche

    _um(freezer, "12:00:00", tag=13)  # Erinnerung: ein neuer Zyklus
    await push.async_verarbeiten()
    assert zustand["aufrufe"] == versuche + 1


async def test_a_missing_notify_service_is_a_failure_that_is_retried(hass, push, freezer):
    await _rot(push)  # kein Dienst registriert
    zustand = _ausfall(hass)
    zustand["ok"] = True
    _um(freezer, "12:05:00")
    await push.async_verarbeiten()

    assert zustand["aufrufe"] == 1


async def test_with_two_targets_one_that_gets_through_is_enough(hass, push, dienst):
    push.ziele_setzen([DIENST, "notify.mobile_app_zweit"], [], REGELN)
    _ausfall(hass, "mobile_app_zweit")
    await _rot(push)

    assert len(dienst) == 1
    assert push.ausstehend == 0


async def test_a_failure_leaves_the_store_with_the_count_of_failures_and_without_targets(hass, push, hass_storage):
    _ausfall(hass)
    await _rot(push)

    daten = hass_storage[STORE]["data"]
    eintrag = next(iter(daten["eintraege"].values()))
    assert eintrag["versuche"] == 1 and eintrag["letzte"] is None
    assert "mobile_app" not in json.dumps(daten)


# --- Orange und Gelb ---

async def test_orange_is_pushed_once_while_it_stays_open(push, dienst, freezer):
    await _orange(push)
    await _orange(push)
    _um(freezer, "18:00:00", tag=12)
    await push.async_verarbeiten()

    assert len(dienst) == 1
    assert dienst[0].data["title"] == "SmartHeat: Gerät gesperrt"


async def test_orange_is_not_pushed_again_after_a_restart(hass, push, dienst):
    await _orange(push)
    neu = Push(hass)
    await neu.async_laden()
    neu.ziele_setzen([DIENST], [], REGELN)
    await _orange(neu)

    assert len(dienst) == 1


@pytest.mark.parametrize("uhrzeit", ["22:00:00", "23:30:00", "02:00:00"])
async def test_orange_in_the_quiet_hours_waits_for_their_end_and_goes_out_only_if_still_open(push, dienst, freezer, uhrzeit):
    _um(freezer, uhrzeit)
    await _orange(push)
    await _orange(push, key="zweite", code="update_noetig")
    assert dienst == []

    _um(freezer, "06:59:00", tag=12)
    await push.async_verarbeiten()
    assert dienst == []

    await push.melden("update_noetig", "zweite", 1, "orange", False)  # inzwischen erledigt
    _um(freezer, "07:00:00", tag=12)
    await push.async_verarbeiten()
    assert [aufruf.data["title"] for aufruf in dienst] == ["SmartHeat: Gerät gesperrt"]
    await push.async_verarbeiten()
    assert len(dienst) == 1


async def test_orange_that_is_closed_in_the_quiet_hours_and_opens_again_later_is_a_new_push(push, dienst, freezer):
    _um(freezer, "23:00:00")
    await _orange(push)
    await push.melden("gesperrt", "hinweis_a", 1, "orange", False)
    _um(freezer, "08:00:00", tag=12)
    await push.async_verarbeiten()
    assert dienst == []
    await _orange(push)
    assert len(dienst) == 1


async def test_the_quiet_hours_use_the_values_of_the_rules(push, dienst, freezer):
    push.ziele_setzen([DIENST], [], {"ruhe_von": 8, "ruhe_bis": 10, "tageslimit": 5})
    _um(freezer, "09:00:00")
    await _orange(push)
    assert dienst == []
    _um(freezer, "10:00:00")
    await push.async_verarbeiten()
    assert len(dienst) == 1


async def test_the_quiet_hours_follow_the_time_zone_of_home_assistant(hass, push, dienst, freezer):
    freezer.move_to("2026-11-11 21:30:00+00:00")  # 22:30 in Berlin
    await _orange(push)
    assert dienst == []

    await hass.config.async_set_time_zone("UTC")
    await push.async_verarbeiten()
    assert len(dienst) == 1


async def test_yellow_is_never_pushed_and_closes_an_earlier_entry(push, dienst, freezer):
    await push.melden("batterie_schwach", "gelb1", 1, "gelb", True)
    await _orange(push)
    await push.melden("gesperrt", "hinweis_a", 1, "gelb", True)  # herabgestuft: nicht mehr offen fuer den Push
    _um(freezer, "12:00:00", tag=13)
    await push.async_verarbeiten()

    assert len(dienst) == 1  # nur die Orange davor
    assert push.eintraege == 0


async def test_an_unknown_level_is_never_pushed(push, dienst):
    await push.melden("gesperrt", "hinweis_a", 1, "violett", True)
    await push.melden("gesperrt", "hinweis_b", 1, None, True)

    assert dienst == [] and push.eintraege == 0


async def test_the_daily_limit_sends_one_collective_message_and_the_next_day_starts_again(push, dienst, freezer):
    push.ziele_setzen([DIENST], [], {"ruhe_von": 22, "ruhe_bis": 7, "tageslimit": 2})
    for nummer in range(5):
        await _orange(push, key=f"hinweis_{nummer}")

    titel = [aufruf.data["title"] for aufruf in dienst]
    assert titel == ["SmartHeat: Gerät gesperrt"] * 2 + ["SmartHeat: mehrere Meldungen"]
    assert dienst[-1].data["message"].startswith("Es liegen mehrere Meldungen vor.")

    _um(freezer, "00:30:00", tag=12)
    await push.async_verarbeiten()
    assert len(dienst) == 3
    _um(freezer, "12:00:00", tag=12)
    await _orange(push, key="hinweis_neu")
    assert len(dienst) == 4


async def test_the_default_limit_is_five_per_day(push, dienst):
    for nummer in range(7):
        await _orange(push, key=f"hinweis_{nummer}")

    assert len(dienst) == 6  # fuenf Meldungen und eine Sammelnachricht


async def test_orange_after_a_quiet_night_counts_against_the_limit_of_the_new_day(push, dienst, freezer):
    push.ziele_setzen([DIENST], [], {"ruhe_von": 22, "ruhe_bis": 7, "tageslimit": 2})
    _um(freezer, "23:00:00")
    for nummer in range(3):
        await _orange(push, key=f"hinweis_{nummer}")
    _um(freezer, "07:00:00", tag=12)
    await push.async_verarbeiten()

    assert [aufruf.data["title"] for aufruf in dienst] == ["SmartHeat: Gerät gesperrt"] * 2 + ["SmartHeat: mehrere Meldungen"]


async def test_switched_off_hints_are_silent_below_red_but_never_for_red(push, dienst, freezer):
    push.ziele_setzen([DIENST], ["gesperrt", "waechter"], REGELN)
    await _orange(push)  # Code in hinweise_aus
    await _orange(push, key="waechter", code="update_noetig")  # Schluessel in hinweise_aus
    await _rot(push, code="addon_gestoppt")  # Rot ignoriert hinweise_aus
    assert [aufruf.data["title"] for aufruf in dienst] == ["SmartHeat: Add-on läuft nicht"]

    push.ziele_setzen([DIENST], [], REGELN)
    _um(freezer, "12:00:00", tag=12)
    await push.async_verarbeiten()
    assert len(dienst) == 2  # nur die Erinnerung an Rot, die stummen Orange kommen nicht nachtraeglich


# --- Regeln ---

@pytest.mark.parametrize("regeln", [
    None, {}, "22-7", [], 5, {"tageslimit": 0}, {"tageslimit": 51}, {"tageslimit": -3}, {"tageslimit": True},
    {"tageslimit": "9"}, {"tageslimit": 2.5}, {"ruhe_von": 22, "ruhe_bis": 22}, {"ruhe_von": 24, "ruhe_bis": 7},
    {"ruhe_von": -1, "ruhe_bis": 7}, {"ruhe_von": "22", "ruhe_bis": 7}, {"ruhe_von": 22}, {"ruhe_bis": 7},
    {"ruhe_von": True, "ruhe_bis": False}, {"ruhe_von": 22, "ruhe_bis": 99},
])
async def test_rules_that_are_missing_or_out_of_range_fall_back_to_22_to_7_and_five_without_a_refusal(push, dienst, freezer, regeln):
    push.ziele_setzen([DIENST], [], regeln)

    _um(freezer, "23:00:00")
    await _orange(push, key="nachts")
    assert dienst == []  # Ruhezeit 22 bis 7
    _um(freezer, "12:00:00", tag=12)
    for nummer in range(6):
        await _orange(push, key=f"tags{nummer}")
    await push.async_verarbeiten()
    titel = [aufruf.data["title"] for aufruf in dienst]
    assert len(titel) == 6 and titel[-1] == "SmartHeat: mehrere Meldungen"  # Limit 5 (die Nachtmeldung zaehlt mit)


@pytest.mark.parametrize("regeln", [
    {"ruhe_von": 0, "ruhe_bis": 23, "tageslimit": 1}, {"ruhe_von": 23, "ruhe_bis": 0, "tageslimit": 50},
    {"ruhe_von": 1, "ruhe_bis": 2, "tageslimit": 7},
])
async def test_rules_inside_the_range_are_taken(push, regeln):
    assert push.ziele_setzen([DIENST], [], regeln) is False  # gleiche Ziele: nichts zu tun
    assert (push.ruhe_von, push.ruhe_bis, push.tageslimit) == (regeln["ruhe_von"], regeln["ruhe_bis"], regeln["tageslimit"])


async def test_a_bad_limit_does_not_take_a_good_quiet_time_with_it(push):
    push.ziele_setzen([DIENST], [], {"ruhe_von": 20, "ruhe_bis": 6, "tageslimit": 999})

    assert (push.ruhe_von, push.ruhe_bis, push.tageslimit) == (20, 6, 5)


# --- Ziele ---

@pytest.mark.parametrize("ungueltig", [
    "notify.other", "notify.mobile_app_", "notify.mobile_app_a b", "notify.mobile_app_A", "notify.mobile_app_x\n",
    "notify.mobile_app_x.y", "persistent_notification.create", "script.turn_on", "notify.mobile_app_ä", "",
    " notify.mobile_app_x", "notify.mobile_app_x ", 5, None, ["notify.mobile_app_x"], "mobile_app_x",
])
async def test_only_services_that_match_the_pattern_completely_are_called(hass, push, dienst, ungueltig):
    push.ziele_setzen([ungueltig, DIENST], [], REGELN)
    await _rot(push)

    assert len(dienst) == 1
    assert push.ziele == (DIENST,)


async def test_a_service_outside_the_pattern_is_never_called(hass, push):
    fremd = async_mock_service(hass, "notify", "alle_handys")
    skript = async_mock_service(hass, "script", "turn_on")
    push.ziele_setzen(["notify.alle_handys", "script.turn_on"], [], REGELN)
    await _rot(push)

    assert fremd == [] and skript == []


async def test_at_most_ten_targets_and_duplicates_count_once(hass, push):
    namen = [f"notify.mobile_app_h{nummer}" for nummer in range(15)]
    push.ziele_setzen(namen + [namen[0]], [], REGELN)

    assert push.ziele == tuple(namen[:EINRICHTUNG_MAX_HANDYS])


@pytest.mark.parametrize("roh", [None, "notify.mobile_app_x", {"a": 1}, 5])
async def test_a_field_that_is_not_a_list_means_no_push(push, dienst, roh):
    push.ziele_setzen(roh, roh, REGELN)
    await _rot(push)

    assert dienst == [] and push.ziele == ()


async def test_no_target_known_yet_keeps_red_waiting_and_a_later_list_sends_it(hass, hass_storage, freezer):
    await hass.config.async_set_time_zone("Europe/Berlin")
    hass.config.language = "de"
    _um(freezer, "12:00:00")
    dienst = async_mock_service(hass, "notify", "mobile_app_pixel")
    push = Push(hass)
    await push.async_laden()
    await _rot(push)
    assert dienst == [] and push.ausstehend == 1

    _um(freezer, "12:30:00")
    push.ziele_setzen([DIENST], [], REGELN)
    await push.async_verarbeiten()
    assert len(dienst) == 1 and push.ausstehend == 0


async def test_red_waiting_for_a_target_list_is_dropped_after_24_hours(hass, hass_storage, freezer):
    await hass.config.async_set_time_zone("Europe/Berlin")
    _um(freezer, "12:00:00")
    push = Push(hass)
    await push.async_laden()
    await _rot(push)
    _um(freezer, "12:00:00", tag=12)
    await push.async_verarbeiten()

    assert push.ausstehend == 0 and push.eintraege == 1


async def test_an_empty_target_list_means_push_off_and_nothing_waits(push, dienst):
    push.ziele_setzen([], [], REGELN)
    await _rot(push)
    await _orange(push)

    assert dienst == [] and push.ausstehend == 0


# --- Eingaben, Zustand ---

@pytest.mark.parametrize("code, key, kreis", [
    ("Gross", "waechter", None), ("a" * 41, "waechter", None), ("addon_gestoppt", "ä", None), ("", "waechter", None),
    ("addon_gestoppt", "waechter", 0), ("addon_gestoppt", "waechter", 5), ("addon_gestoppt", "waechter", True),
    ("addon_gestoppt", "waechter", "1"), (None, "waechter", None), ("addon_gestoppt", None, None),
])
async def test_input_that_is_not_a_code_is_ignored_without_an_error(push, dienst, code, key, kreis):
    await push.melden(code, key, kreis, "rot", True)

    assert dienst == [] and push.eintraege == 0


async def test_a_corrupt_store_starts_empty(hass, hass_storage):
    hass_storage[STORE] = {"version": 1, "minor_version": 1, "key": STORE, "data": {"eintraege": "kaputt", "tag": 5}}
    push = Push(hass)
    await push.async_laden()

    assert push.eintraege == 0


async def test_the_number_of_entries_is_capped(push, dienst):
    for nummer in range(150):
        await push.melden("gesperrt", f"hinweis_{nummer}", 1, "gelb", True)
        await push.melden("gesperrt", f"hinweis_{nummer}", 1, "orange", True)

    assert push.eintraege <= 100


async def test_the_tick_is_registered_and_runs_the_reminder(hass, push, dienst, freezer):
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    abraeumer = []
    push.async_start(SimpleNamespace(async_on_unload=abraeumer.append))
    await _rot(push)
    _um(freezer, "12:00:00", tag=12)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert len(dienst) == 2
    for abraeumen in abraeumer:  # der Takt endet mit dem Eintrag
        abraeumen()


# --- Protokoll ---

async def test_the_log_names_neither_the_service_nor_the_error_text(hass, push, caplog, freezer):
    caplog.set_level(logging.DEBUG)
    _ausfall(hass)
    await _rot(push, kreis=2)
    _um(freezer, "12:05:00")
    await push.async_verarbeiten()

    text = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("custom_components.smartheat"))
    assert "addon_gestoppt" in text and "Kreis 2" in text  # Code und Zahlen duerfen stehen
    assert "pixel" not in text and "kaputt" not in text and "notify" not in text
