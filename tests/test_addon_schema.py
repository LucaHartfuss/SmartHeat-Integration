import pytest
from homeassistant.components.hassio import AddonError

from .addon_fakes import FakeAddon, check_options
from .addon_schema import validate

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


def test_full_wizard_write_requires_the_mandatory_options():
    with pytest.raises(AddonError, match="Pflichtfeld 'tenant_id' fehlt"):
        check_options("heizungsbruecke", {"entity_curve_current": "number.x"})
    check_options("heizungsbruecke", {"tenant_id": "t"})  # Teiloptionen: nur Schluessel und Typen
    check_options("unbekanntes_addon", {"egal": 1})
