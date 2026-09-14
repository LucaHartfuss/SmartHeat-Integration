import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

# aiohttp's default resolver (aiodns/pycares) starts a global daemon thread
# ("_run_safe_shutdown_loop") lazily on first use, the first time any test
# creates a real aiohttp.ClientSession (e.g. via the aiohttp_client fixture
# used in test_api_client.py). pytest-homeassistant-custom-component's strict
# per-test thread-leak check (verify_cleanup in its plugins.py) then flags
# that thread as "new" on whichever test happens to run first.
#
# Upstream home-assistant/core fixed this exact issue in PR #146733 ("Ignore
# lingering pycares shutdown thread") by adding the thread name to an
# ignore-list inside verify_cleanup itself. The installed
# pytest-homeassistant-custom-component version here (0.13.205) predates that
# fix -- its verify_cleanup has no ignore-list hook, only a hardcoded
# `isinstance(thread, threading._DummyThread) or thread.name.startswith("waitpid-")`
# check (see plugins.py:405) -- so that upstream-sanctioned mechanism isn't
# available to us yet. As a defensive stand-in, start the thread once here,
# at conftest import time, before any test's before/after thread snapshot is
# taken, so it's never seen as newly created by an individual test. This is
# scoped to be replaced by the upstream ignore-list mechanism once this repo
# upgrades past the fix.
#
# pycares/aiodns are declared explicitly (and pinned) in requirements_test.txt
# for this reason, rather than relying on them arriving as an undeclared
# transitive dependency of aiohttp's optional resolver extra. Broad exception
# handling here is deliberate: this runs unconditionally at import time for
# every test file in this repo (tests/conftest.py), so a failure of the
# native-extension constructor call itself -- not just an ImportError -- must
# never be allowed to abort collection for the whole suite.
try:
    import pycares

    pycares.Channel()
except Exception:  # noqa: BLE001 -- see comment above: must never abort collection.
    pass


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
