"""Mini-Validator fuer die Schema-Grammatik der Add-on-`config.yaml` (Supervisor, soweit die
SmartHeat-Add-ons sie nutzen). Der Supervisor lehnt set_options mit unbekannten Schluesseln, falschen
Typen und fehlenden Pflichtfeldern ab (AddonError); der Fake in addon_fakes.py tut dasselbe, damit
Drift zwischen Wizard und Add-on-Schema in den Tests auffaellt und nicht erst beim Kunden (TP12e,
AU-023). Gegenstueck der Fixture: tests/fixtures/addon_schema.json (Contract-Check 32)."""
import re

_INT_RANGE = re.compile(r"int\((\d+),(\d+)\)")
_LIST_TYPE = re.compile(r"list\((.*)\)")


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_scalar(spec: str, value) -> str | None:
    """None, wenn value zu spec passt (ohne abschliessendes ?), sonst eine Beschreibung."""
    if spec in ("str", "password"):
        return None if isinstance(value, str) else "kein Text"
    if spec == "url":
        return None if isinstance(value, str) and value.startswith(("http://", "https://")) else "keine URL"
    if spec == "bool":
        return None if isinstance(value, bool) else "kein bool"
    if spec == "port":
        return None if _is_int(value) and 1 <= value <= 65535 else "kein Port"
    if (match := _INT_RANGE.fullmatch(spec)) is not None:
        low, high = int(match.group(1)), int(match.group(2))
        return None if _is_int(value) and low <= value <= high else f"nicht im Bereich {low}..{high}"
    if (match := _LIST_TYPE.fullmatch(spec)) is not None:
        allowed = match.group(1).split("|")
        return None if value in allowed else f"nicht in {allowed}"
    return f"Schema-Typ {spec!r} nicht unterstuetzt"


def _optional(spec) -> bool:
    if isinstance(spec, list):
        return bool(spec) and isinstance(spec[0], str) and spec[0].endswith("?")
    return spec.endswith("?")


def validate(schema: dict, options: dict, *, require_all: bool = True) -> list[str]:
    """Liste der Verstoesse (leer = gueltig). require_all=False ueberspringt die Pflichtfeld-Pruefung
    (Fixtures mit Teiloptionen)."""
    problems = [f"unbekannter Schluessel {key!r}" for key in options if key not in schema]
    for key, spec in schema.items():
        if key not in options or options[key] is None:
            if require_all and not _optional(spec):
                problems.append(f"Pflichtfeld {key!r} fehlt")
            continue
        value = options[key]
        if isinstance(spec, list):
            if not isinstance(value, list):
                problems.append(f"{key!r}: keine Liste")
                continue
            element = spec[0].removesuffix("?")
            problems.extend(
                f"{key!r}[{index}]: {text}" for index, item in enumerate(value) if (text := _check_scalar(element, item))
            )
            continue
        if (text := _check_scalar(spec.removesuffix("?"), value)) is not None:
            problems.append(f"{key!r}: {text} (Schema {spec!r}, Wert {value!r})")
    return problems
