import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

# aiohttp's default resolver (aiodns/pycares) starts a global daemon thread
# ("_run_safe_shutdown_loop") lazily on first use, the first time any test
# creates a real aiohttp.ClientSession (e.g. via the aiohttp_client fixture).
# pytest-homeassistant-custom-component's strict per-test thread-leak check
# would then flag that thread as "new" on whichever test happens to run
# first. Starting it once here, at conftest import time (before any test's
# before/after thread snapshot is taken), avoids that false positive without
# weakening the leak check itself.
try:
    import pycares

    pycares.Channel()
except ImportError:
    pass


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
