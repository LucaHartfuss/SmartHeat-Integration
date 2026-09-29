"""Profilkatalog des Servers lesen (GET /catalog, Spec TP6 2.1; Suchregeln verbindlich in
docs/superpowers/specs/2026-09-26-profilkatalog-design.md, 3.1). Der Server liefert nur Daten,
keine Regex: Suffixe sind Literale mit hoechstens einem Platzhalter {circuit}. Eine ungueltige
Integrations-Beschreibung wird verworfen und geloggt, nicht der ganze Katalog. Unbekannte Felder
werden ignoriert."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

CIRCUIT_PLACEHOLDER = "{circuit}"
# shift_current bewusst nicht: der Zonen-Index entspricht nicht immer der Kreisnummer (Plan
# TP11, Praezisierung 9); ohne Treffer waehlt der Kunde die Zone selbst.
REQUIRED_CIRCUIT_ROLES = ("curve_current", "min_flow", "heat_limit")


@dataclass(frozen=True)
class RoleMatcher:
    entity_domain: str
    unique_id_suffix: str | None = None
    original_name_suffix: str | None = None

    def uid_pattern(self) -> re.Pattern | None:
        if self.unique_id_suffix is None:
            return None
        parts = [re.escape(part) for part in self.unique_id_suffix.split(CIRCUIT_PLACEHOLDER)]
        return re.compile(r"(\d+)".join(parts) + "$")


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
    uid, name = raw.get("unique_id_suffix"), raw.get("original_name_suffix")
    if (uid is None) == (name is None):
        raise _Invalid(f"{role}: genau eine Suchart erwartet")
    if uid is not None:
        if not isinstance(uid, str) or not uid:
            raise _Invalid(f"{role}: unique_id_suffix leer")
        placeholders = uid.count(CIRCUIT_PLACEHOLDER)
        # Kreisbezogene Rollen brauchen genau einen Platzhalter (daraus kommt die Kreisnummer),
        # anlagenweite keinen.
        if placeholders != (1 if circuit_scoped else 0):
            raise _Invalid(f"{role}: falsche Anzahl Platzhalter in {uid!r}")
        rest = uid.replace(CIRCUIT_PLACEHOLDER, "")
        if "{" in rest or "}" in rest:
            raise _Invalid(f"{role}: unbekannter Platzhalter in {uid!r}")
        return RoleMatcher(entity_domain, unique_id_suffix=uid)
    if circuit_scoped:
        raise _Invalid(f"{role}: kreisbezogene Rolle braucht unique_id_suffix")
    if not isinstance(name, str) or not name or "{" in name or "}" in name:
        raise _Invalid(f"{role}: original_name_suffix ungueltig")
    return RoleMatcher(entity_domain, original_name_suffix=name)


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
