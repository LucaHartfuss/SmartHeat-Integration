"""Dynamische Hinweistexte in der UI-Sprache: description_placeholders werden von HA nie selbst
uebersetzt, deshalb aus config.hints der Uebersetzungen gelesen (HA cached die Uebersetzungen)."""
from homeassistant.core import HomeAssistant
from homeassistant.helpers import translation

from .const import DOMAIN


async def async_hint(hass: HomeAssistant, key: str, **placeholders: str) -> str:
    texts = await translation.async_get_translations(hass, hass.config.language, "config", integrations=[DOMAIN])
    text = texts.get(f"component.{DOMAIN}.config.hints.{key}", key)
    return text.format(**placeholders) if placeholders else text
