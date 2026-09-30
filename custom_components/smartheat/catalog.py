"""Profilkatalog des Servers lesen (GET /catalog, Spec TP6 2.1; Suchregeln verbindlich in
docs/superpowers/specs/2026-09-26-profilkatalog-design.md, 3.1). Der Server liefert nur Daten,
keine Regex: Suffixe sind Literale mit hoechstens einem Platzhalter {circuit} oder (Katalog v2) {index} samt circuit_in_name. Eine ungueltige
Integrations-Beschreibung wird verworfen und geloggt, nicht der ganze Katalog. Unbekannte Felder
werden ignoriert."""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

CIRCUIT_PLACEHOLDER = "{circuit}"
# Beliebige Nummer in der unique_id, nicht die Kreisnummer (Katalog v2, TP12c); der Kreis steht dann
# im Namen (circuit_in_name).
INDEX_PLACEHOLDER = "{index}"
# shift_current bewusst nicht: die Zone wird ueber circuit_in_name dem Kreis zugeordnet (Katalog v2,
# TP12c); ohne Treffer waehlt der Kunde die Zone selbst.
REQUIRED_CIRCUIT_ROLES = ("curve_current", "min_flow", "heat_limit")
POLL_INTERVAL_UNITS = {"s": 1, "min": 60}


@dataclass(frozen=True)
class RoleMatcher:
    entity_domain: str
    unique_id_suffix: str | None = None
    original_name_suffix: str | None = None
    circuit_in_name: str | None = None

    def uid_pattern(self) -> re.Pattern | None:
        if self.unique_id_suffix is None:
            return None
        escaped = re.escape(self.unique_id_suffix)
        for placeholder in (CIRCUIT_PLACEHOLDER, INDEX_PLACEHOLDER):
            escaped = escaped.replace(re.escape(placeholder), r"(\d+)")
        return re.compile(escaped + "$")

    def name_circuit(self, original_name: str | None) -> str | None:
        """Kreisnummer an der Stelle von {circuit} in circuit_in_name (ohne Gross-/Kleinschreibung),
        None ohne circuit_in_name oder ohne Treffer. Der letzte Treffer zaehlt: mypyllant haengt
        " (Circuit N)" hinter den frei waehlbaren Zonennamen an, der selbst so etwas enthalten kann."""
        if self.circuit_in_name is None or not original_name:
            return None
        before, _, after = self.circuit_in_name.partition(CIRCUIT_PLACEHOLDER)
        found = re.findall(re.escape(before.lower()) + r"(\d+)" + re.escape(after.lower()), original_name.lower())
        return found[-1] if found else None


@dataclass(frozen=True)
class PollIntervalOption:
    """Abfrageintervall der Heizungs-Integration in deren ConfigEntry.options (AU-017)."""
    key: str
    unit: str
    default: int

    def seconds(self, options: Mapping) -> float | None:
        """Intervall in Sekunden; None, wenn der gespeicherte Wert keine Zahl ist."""
        raw = options.get(self.key, self.default)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        return float(raw) * POLL_INTERVAL_UNITS[self.unit]


@dataclass(frozen=True)
class ErzeugerTypHint:
    model_contains: str
    erzeuger_typ: str


@dataclass(frozen=True)
class IntegrationDescriptor:
    domain: str
    label: str
    hersteller: str
    circuit_roles: dict[str, RoleMatcher]
    system_roles: dict[str, RoleMatcher]
    erzeuger_typ_hints: tuple[ErzeugerTypHint, ...]
    poll_interval_option: PollIntervalOption | None = None


class _Invalid(ValueError):
    pass


def _text(raw: dict, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise _Invalid(f"'{key}' fehlt oder ist leer")
    return value


def _matcher(role: str, raw, *, circuit_scoped: bool) -> RoleMatcher:
    if not isinstance(raw, dict):
        raise _Invalid(f"{role}: kein Objekt")
    entity_domain = _text(raw, "entity_domain")
    uid, name, in_name = raw.get("unique_id_suffix"), raw.get("original_name_suffix"), raw.get("circuit_in_name")
    if (uid is None) == (name is None):
        raise _Invalid(f"{role}: genau eine Suchart erwartet")
    if uid is None:
        if circuit_scoped:
            raise _Invalid(f"{role}: kreisbezogene Rolle braucht unique_id_suffix")
        if in_name is not None:
            raise _Invalid(f"{role}: circuit_in_name nur mit unique_id_suffix")
        if not isinstance(name, str) or not name or "{" in name or "}" in name:
            raise _Invalid(f"{role}: original_name_suffix ungueltig")
        return RoleMatcher(entity_domain, original_name_suffix=name)
    if not isinstance(uid, str) or not uid:
        raise _Invalid(f"{role}: unique_id_suffix leer")
    circuits, indexes = uid.count(CIRCUIT_PLACEHOLDER), uid.count(INDEX_PLACEHOLDER)
    rest = uid.replace(CIRCUIT_PLACEHOLDER, "").replace(INDEX_PLACEHOLDER, "")
    if "{" in rest or "}" in rest:
        raise _Invalid(f"{role}: unbekannter Platzhalter in {uid!r}")
    if in_name is None:
        # Kreisbezogene Rollen brauchen genau einen {circuit} (daraus kommt die Kreisnummer),
        # anlagenweite keinen; {index} nur zusammen mit circuit_in_name.
        if indexes or circuits != (1 if circuit_scoped else 0):
            raise _Invalid(f"{role}: falsche Platzhalter in {uid!r}")
        return RoleMatcher(entity_domain, unique_id_suffix=uid)
    if not circuit_scoped or role in REQUIRED_CIRCUIT_ROLES:
        raise _Invalid(f"{role}: circuit_in_name hier nicht erlaubt")
    if circuits or indexes != 1:
        raise _Invalid(f"{role}: circuit_in_name braucht genau einen {INDEX_PLACEHOLDER} und keinen {CIRCUIT_PLACEHOLDER}")
    if (
        not isinstance(in_name, str) or in_name.count(CIRCUIT_PLACEHOLDER) != 1
        or "{" in in_name.replace(CIRCUIT_PLACEHOLDER, "") or "}" in in_name.replace(CIRCUIT_PLACEHOLDER, "")
    ):
        raise _Invalid(f"{role}: circuit_in_name ungueltig")
    return RoleMatcher(entity_domain, unique_id_suffix=uid, circuit_in_name=in_name)


def _poll_interval(raw) -> PollIntervalOption | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _Invalid("poll_interval_option: kein Objekt")
    key, unit, default = raw.get("key"), raw.get("unit"), raw.get("default")
    if not isinstance(key, str) or not key:
        raise _Invalid("poll_interval_option: key fehlt")
    if unit not in POLL_INTERVAL_UNITS:
        raise _Invalid("poll_interval_option: unbekannte Einheit")
    if isinstance(default, bool) or not isinstance(default, int) or default <= 0:
        raise _Invalid("poll_interval_option: default ungueltig")
    return PollIntervalOption(key, unit, default)


def _roles(raw, *, circuit_scoped: bool) -> dict[str, RoleMatcher]:
    if not isinstance(raw, dict):
        raise _Invalid("Rollen sind kein Objekt")
    return {role: _matcher(role, value, circuit_scoped=circuit_scoped) for role, value in raw.items()}


def _descriptor(raw) -> IntegrationDescriptor:
    if not isinstance(raw, dict):
        raise _Invalid("kein Objekt")
    circuit_roles = _roles(raw.get("circuit_scoped_roles"), circuit_scoped=True)
    missing = [role for role in REQUIRED_CIRCUIT_ROLES if role not in circuit_roles]
    if missing:
        raise _Invalid(f"Pflichtrolle(n) fehlen: {', '.join(missing)}")
    hints = []
    for hint in raw.get("erzeuger_typ_hints") or []:
        if not isinstance(hint, dict):
            raise _Invalid("erzeuger_typ_hints: kein Objekt")
        hints.append(ErzeugerTypHint(_text(hint, "model_contains"), _text(hint, "erzeuger_typ")))
    return IntegrationDescriptor(
        domain=_text(raw, "domain"),
        label=_text(raw, "label"),
        hersteller=_text(raw, "hersteller"),
        circuit_roles=circuit_roles,
        system_roles=_roles(raw.get("system_roles", {}), circuit_scoped=False),
        erzeuger_typ_hints=tuple(hints),
        poll_interval_option=_poll_interval(raw.get("poll_interval_option")),
    )


def parse_integrations(catalog: dict) -> list[IntegrationDescriptor]:
    descriptors = []
    for raw in catalog.get("integrations") or []:
        try:
            descriptors.append(_descriptor(raw))
        except _Invalid as error:
            domain = raw.get("domain") if isinstance(raw, dict) else None
            _LOGGER.warning("Integrations-Beschreibung %r aus dem Katalog verworfen: %s", domain, error)
    return descriptors


def verified_profiles(catalog: dict) -> list[dict]:
    return [
        profile for profile in catalog.get("profiles") or []
        if isinstance(profile, dict) and profile.get("verified") is True
        and all(isinstance(profile.get(key), str) for key in ("profile_id", "hersteller", "erzeuger_typ", "verteilsystem"))
    ]
