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
    first, reconfigure = AsyncMock(), AsyncMock()
    monkeypatch.setattr("custom_components.smartheat.config_flow.async_rollback_first_setup", first, raising=False)
    monkeypatch.setattr("custom_components.smartheat.config_flow.async_rollback_reconfigure", reconfigure, raising=False)
    return SimpleNamespace(first=first, reconfigure=reconfigure)
