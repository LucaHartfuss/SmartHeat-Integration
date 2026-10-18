import pytest

# supervisor_fakes stellt die Fixture `supervisor` (Attrappe des Supervisor-Clients) bereit.
pytest_plugins = ["pytest_homeassistant_custom_component", "tests.supervisor_fakes"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
