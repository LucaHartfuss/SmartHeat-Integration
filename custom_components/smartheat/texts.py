"""Dynamische Hinweistexte in der UI-Sprache: description_placeholders werden von HA nie selbst
uebersetzt, deshalb aus dem top-level "common" der Uebersetzungen gelesen (HA cached die
Uebersetzungen). "common" statt eines eigenen Schluessels unter "config", weil hassfests
Uebersetzungs-Schema unter "config" keine zusaetzlichen Schluessel erlaubt (nur die von HA
vordefinierten wie step/error/abort/...), "common" aber als offene slug->String-Map vorgesehen ist.
Dazu `klartext` fuer jeden Text, der nicht von uns stammt und in einer Markdown-Beschreibung steht."""
import re

from homeassistant.core import HomeAssistant
from homeassistant.helpers import translation

from .const import DOMAIN, ERROR_TEXT_MAX

# Markdown-Zeichen, dazu `&` (HTML-Entitaeten) und `:`, `.`, `@`: ohne sie verlinkt Markdown nackte Adressen selbst
# (`https://…`, `www.…`, E-Mail-Adressen). Ein Backslash vor einem ASCII-Satzzeichen zeigt dieses woertlich.
_MARKDOWN = re.compile(r"([\\`*_\[\]()<>#!|~&:.@])")
_STEUERZEICHEN = re.compile(r"[\x00-\x1f\x7f-\x9f\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}]")
# Zero-width- und Bidi-Steuerzeichen: sie verstecken Zeichen oder drehen den Text optisch um.
_UNSICHTBAR = re.compile(
    r"[\N{ZERO WIDTH SPACE}-\N{RIGHT-TO-LEFT MARK}\N{LEFT-TO-RIGHT EMBEDDING}-\N{RIGHT-TO-LEFT OVERRIDE}"
    r"\N{LEFT-TO-RIGHT ISOLATE}-\N{POP DIRECTIONAL ISOLATE}\N{ZERO WIDTH NO-BREAK SPACE}]")
# Liste, Linie, Setext-Ueberschrift; `*`, `>`, `#`, Ziffer mit Punkt maskiert _MARKDOWN
_BLOCKANFANG = re.compile(r"^[-+=]")


def klartext(text: object) -> str:
    """Ein Text, der nicht von uns stammt (Geraet, Server), als Klartext fuer eine Markdown-Beschreibung von Home
    Assistant: Zeilenumbrueche und andere Steuerzeichen zu Leerzeichen, Zero-width- und Bidi-Zeichen weg, fuehrender
    Leerraum weg (sonst Codeblock), auf ERROR_TEXT_MAX Zeichen gekuerzt, dann jedes Markdown-Zeichen sowie `&`, `:`,
    `.` und `@` mit `\\` maskiert und ein fuehrendes `-`, `+`, `=` ebenso -- weder ein Link (auch kein automatisch
    erkannter) noch Formatierung noch ein Block (Liste, Linie, Ueberschrift) kommt durch."""
    sauber = _UNSICHTBAR.sub("", _STEUERZEICHEN.sub(" ", str(text or ""))).lstrip()
    maskiert = _MARKDOWN.sub(r"\\\1", sauber[:ERROR_TEXT_MAX])
    return _BLOCKANFANG.sub(r"\\\g<0>", maskiert)


async def async_hint(hass: HomeAssistant, key: str, **placeholders: str) -> str:
    texts = await translation.async_get_translations(hass, hass.config.language, "common", integrations=[DOMAIN])
    text = texts.get(f"component.{DOMAIN}.common.{key}", key)
    return text.format(**placeholders) if placeholders else text


async def async_role_label(hass: HomeAssistant, rolle: str, *, schritt: str = "anlagenwerte") -> str:
    """Bezeichnung einer Rolle bzw. eines Felds in der UI-Sprache (Feldtext des Schritts, Vorgabe Anlagenwerte), sonst
    der Schluessel."""
    texts = await translation.async_get_translations(hass, hass.config.language, "config", integrations=[DOMAIN])
    return texts.get(f"component.{DOMAIN}.config.step.{schritt}.data.{rolle}", rolle)
