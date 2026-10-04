from types import SimpleNamespace

import pytest
from homeassistant.components.hassio import AddonError, AddonManager

from .addon_fakes import FakeAddon, check_options
from .addon_schema import validate
from .flow_helpers import BRIDGE_OPTIONS, CF_OPTIONS, mock_addons

SCHEMA = {
    "name": "str", "secret": "password", "flag": "bool?", "interval": "int(10,600)?", "mode": "list(a|b)?",
    "base": "url?", "port": "port", "items": ["str?"],
}


def test_valid_options_pass():
    options = {"name": "x", "secret": "s", "flag": True, "interval": 300, "mode": "a", "base": "https://x.test", "port": 18830, "items": ["a", "b"]}
    assert validate(SCHEMA, options) == []


@pytest.mark.parametrize("options, fragment", [
    ({"name": "x", "secret": "s", "port": 18830, "extra": 1}, "unbekannter Schluessel 'extra'"),
    ({"secret": "s", "port": 18830}, "Pflichtfeld 'name' fehlt"),
    ({"name": 5, "secret": "s", "port": 18830}, "'name'"),
    ({"name": "x", "secret": "s", "port": 18830, "interval": 601}, "'interval'"),
    ({"name": "x", "secret": "s", "port": 18830, "interval": True}, "'interval'"),
    ({"name": "x", "secret": "s", "port": 18830, "mode": "c"}, "'mode'"),
    ({"name": "x", "secret": "s", "port": 0}, "'port'"),
    ({"name": "x", "secret": "s", "port": 18830, "items": "a"}, "'items'"),
    ({"name": "x", "secret": "s", "port": 18830, "base": "ftp://x"}, "'base'"),
])
def test_invalid_options_are_reported(options, fragment):
    assert any(fragment in problem for problem in validate(SCHEMA, options))


def test_partial_options_without_required_check():
    assert validate(SCHEMA, {"name": "x"}, require_all=False) == []


def test_unsupported_schema_type_is_reported():
    assert any("nicht unterstuetzt" in problem for problem in validate({"x": "email"}, {"x": "a@b"}))


async def test_fake_addon_rejects_options_the_addon_schema_does_not_know():
    addon = FakeAddon("a_heizungsbruecke", [])
    with pytest.raises(AddonError, match="unbekannter Schluessel 'erfunden'"):
        await addon.async_set_addon_options({"tenant_id": "t", "erfunden": 1})
    assert addon.options == {}


def test_check_options_requires_mandatory_keys_only_on_request():
    with pytest.raises(AddonError, match="Pflichtfeld 'tenant_id' fehlt"):
        check_options("heizungsbruecke", {"entity_curve_current": "number.x"}, require_all=True)
    check_options("heizungsbruecke", {"tenant_id": "t"})  # Teiloptionen: nur Schluessel und Typen
    check_options("unbekanntes_addon", {"egal": 1}, require_all=True)


def test_cloudflared_write_missing_a_mandatory_key_is_rejected():
    full = {"hostname": "h", "local_port": 18830, "service_token_id": "i", "service_token_secret": "s"}
    check_options("cloudflared_access_mqtt", full, require_all=True)
    with pytest.raises(AddonError, match="Pflichtfeld 'hostname' fehlt"):
        check_options("a_cloudflared_access_mqtt", {k: v for k, v in full.items() if k != "hostname"}, require_all=True)


async def test_wizard_write_path_demands_the_mandatory_options(hass, monkeypatch):
    """mock_addons (Schreibpfad des Wizards) verlangt auch die Pflichtfelder, fuer beide Add-ons."""
    mock_addons(hass, monkeypatch)
    # mock_addons ersetzt AddonManager.async_set_addon_options; das Objekt muss nur addon_slug tragen.
    bridge = SimpleNamespace(addon_slug="heizungsbruecke")
    cloudflared = SimpleNamespace(addon_slug="cloudflared_access_mqtt")
    write = AddonManager.async_set_addon_options

    await write(bridge, BRIDGE_OPTIONS)
    await write(cloudflared, CF_OPTIONS)
    # Seit Plan 3c sind die Hebel-Entities je Hebelsatz optional im Schema; Pflicht bleibt die Aussentemperatur.
    with pytest.raises(AddonError, match="Pflichtfeld 'entity_outdoor_temp' fehlt"):
        await write(bridge, {k: v for k, v in BRIDGE_OPTIONS.items() if k != "entity_outdoor_temp"})
    with pytest.raises(AddonError, match="Pflichtfeld 'hostname' fehlt"):
        await write(cloudflared, {k: v for k, v in CF_OPTIONS.items() if k != "hostname"})
