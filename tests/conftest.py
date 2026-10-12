from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def rollback(monkeypatch):
    """Rueckbau des Wizards (TP12c) in Flow-Tests aufzeichnen statt ausfuehren; test_setup_rollback.py
    testet die echten Funktionen (dort nicht ueber config_flow importiert)."""
    # Erst hier importieren (nicht auf Modulebene): so haengt die Sammlung der Tests nicht an config_flow, und der
    # Monkeypatch findet custom_components.smartheat auch bei Testdateien ohne eigenen Integrationsimport.
    import custom_components.smartheat.config_flow  # noqa: F401

    first, reconfigure, server_only = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr("custom_components.smartheat.config_flow.async_rollback_first_setup", first, raising=False)
    monkeypatch.setattr("custom_components.smartheat.config_flow.async_rollback_reconfigure", reconfigure, raising=False)
    monkeypatch.setattr("custom_components.smartheat.config_flow.async_rollback_server_only", server_only, raising=False)
    return SimpleNamespace(first=first, reconfigure=reconfigure, server_only=server_only)
