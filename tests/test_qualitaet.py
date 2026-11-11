"""Qualitaetsnachweis der Integration: quality_scale.yaml gegen die Regelliste der eingesetzten HA-Version, Manifest,
PARALLEL_UPDATES je Plattform und die Messung der Testabdeckung mit ihrer Schwelle."""
import importlib
import tomllib
from pathlib import Path

import homeassistant.components as ha_components
import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
KOMPONENTE = REPO / "custom_components" / "smartheat"
STATUS = {"done", "todo", "exempt"}
# Die Regelliste der Integration Quality Scale (54 Regeln, nach Stufen), so wie HA sie in den quality_scale.yaml seiner
# Komponenten fuehrt.
REGELN = {
    "action-setup", "appropriate-polling", "brands", "common-modules", "config-flow-test-coverage", "config-flow",
    "dependency-transparency", "docs-actions", "docs-high-level-description", "docs-installation-instructions",
    "docs-removal-instructions", "entity-event-setup", "entity-unique-id", "has-entity-name", "runtime-data",
    "test-before-configure", "test-before-setup", "unique-config-entry",
    "action-exceptions", "config-entry-unloading", "docs-configuration-parameters", "docs-installation-parameters",
    "entity-unavailable", "integration-owner", "log-when-unavailable", "parallel-updates", "reauthentication-flow",
    "test-coverage",
    "devices", "diagnostics", "discovery-update-info", "discovery", "docs-data-update", "docs-examples",
    "docs-known-limitations", "docs-supported-devices", "docs-supported-functions", "docs-troubleshooting",
    "docs-use-cases", "docs-triggers", "docs-conditions", "dynamic-devices", "entity-category",
    "entity-device-class", "entity-disabled-by-default", "entity-translations", "exception-translations",
    "icon-translations", "reconfiguration-flow", "repair-issues", "stale-devices",
    "async-dependency", "inject-websession", "strict-typing",
}
# Regeln, die heute erfuellt sind und es bleiben muessen (kein Rueckfall auf todo). Offen sind bewusst config-flow
# (data_description fehlt), config-flow-test-coverage, brands, die Aktionen und die Anleitung.
ERFUELLT = {
    "common-modules", "entity-unique-id", "has-entity-name", "runtime-data", "test-before-configure",
    "unique-config-entry", "action-exceptions", "config-entry-unloading", "entity-unavailable", "integration-owner",
    "log-when-unavailable", "parallel-updates", "test-coverage", "devices", "diagnostics", "icon-translations",
}


def _regeln() -> dict:
    daten = yaml.safe_load((KOMPONENTE / "quality_scale.yaml").read_text(encoding="utf-8"))
    assert set(daten) == {"rules"}
    return daten["rules"]


def test_regelliste_vollstaendig_und_ohne_fremde_regel():
    assert set(_regeln()) == REGELN


def test_regelliste_deckt_sich_mit_der_eingesetzten_ha_version():
    """Kommt eine Regel hinzu oder faellt eine weg, wird die Pruefung rot und die Datei wird nachgezogen."""
    namen: set[str] = set()
    for datei in Path(next(iter(ha_components.__path__))).glob("*/quality_scale.yaml"):
        namen |= set(yaml.safe_load(datei.read_text(encoding="utf-8"))["rules"])
    assert namen == REGELN


def test_jede_regel_hat_einen_gueltigen_stand_mit_grund():
    for name, wert in _regeln().items():
        status = wert if isinstance(wert, str) else wert["status"]
        assert status in STATUS, name
        if status != "done":
            assert isinstance(wert, dict), name
            assert str(wert.get("comment", "")).strip(), f"{name}: {status} ohne Begruendung"


def test_zugesagte_regeln_sind_erfuellt_oder_ausgenommen():
    regeln = _regeln()
    offen = {
        name for name in ERFUELLT
        if (regeln[name] if isinstance(regeln[name], str) else regeln[name]["status"]) == "todo"}
    assert not offen


@pytest.mark.parametrize("modul", ["sensor", "binary_sensor", "number"])
def test_parallel_updates_je_plattform(modul):
    """Die Entities lesen nur den Zustand des Coordinators (Push) und feuern hoechstens ein lokales Ereignis: keine
    Begrenzung noetig, aber ausdruecklich gesetzt."""
    plattform = importlib.import_module(f"custom_components.smartheat.{modul}")
    assert plattform.PARALLEL_UPDATES == 0


def test_manifest_schluessel_und_reihenfolge():
    """Gleiche Schluesselreihenfolge wie hassfest sie verlangt: domain, name, dann alphabetisch."""
    import json
    manifest = json.loads((KOMPONENTE / "manifest.json").read_text(encoding="utf-8"))
    schluessel = list(manifest)
    assert schluessel[:2] == ["domain", "name"]
    assert schluessel[2:] == sorted(schluessel[2:])
    assert manifest["integration_type"] == "hub"
    assert manifest["iot_class"] == "local_push"
    assert manifest["requirements"] == []


def test_abdeckung_wird_gemessen_mit_schwelle():
    """Die Messung laeuft in jedem Testlauf (pytest-cov aus der HA-Testumgebung) und faellt unter der Schwelle durch."""
    konfig = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["coverage"]
    assert konfig["run"]["source"] == ["custom_components/smartheat"]
    assert konfig["report"]["fail_under"] >= 95
    check = (REPO / "scripts" / "check.sh").read_text(encoding="utf-8")
    assert "--cov" in check
