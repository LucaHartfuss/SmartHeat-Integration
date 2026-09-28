"""Dynamische Hinweistexte in der UI-Sprache: description_placeholders werden von HA nie selbst
uebersetzt, deshalb aus dem top-level "common" der Uebersetzungen gelesen (HA cached die
Uebersetzungen). "common" statt eines eigenen Schluessels unter "config", weil hassfests
Uebersetzungs-Schema unter "config" keine zusaetzlichen Schluessel erlaubt (nur die von HA
vordefinierten wie step/error/abort/...), "common" aber als offene slug->String-Map vorgesehen ist."""
from homeassistant.core import HomeAssistant
from homeassistant.helpers import translation

from .const import DOMAIN


async def async_hint(hass: HomeAssistant, key: str, **placeholders: str) -> str:
    texts = await translation.async_get_translations(hass, hass.config.language, "common", integrations=[DOMAIN])
    text = texts.get(f"component.{DOMAIN}.common.{key}", key)
    return text.format(**placeholders) if placeholders else text
