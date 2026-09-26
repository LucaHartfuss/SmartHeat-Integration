from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol

from custom_components.smartheat.api_client import ApiError, CannotConnect, InvalidAuth
from custom_components.smartheat.const import DOMAIN


def _catalog(*profiles):
    return {"catalog_version": 1, "profiles": list(profiles), "integrations": []}


def _enable_supervisor(hass, monkeypatch):
    """Simuliert eine Supervisor-Installation (HA OS/Supervised).

    Der Guard am Anfang von async_step_user (Critical 1: unhandled KeyError auf
    SUPERVISOR_TOKEN) prueft sowohl is_hassio(hass) (hass.config.components) als
    auch os.environ["SUPERVISOR_TOKEN"] -- beides muss fuer jeden Test gesetzt sein,
    der ueber den user-Schritt hinauskommen soll. Siehe auch
    test_user_step_aborts_when_not_supervisor fuer den Gegenfall.
    """
    hass.config.components.add("hassio")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-supervisor-token")

    # AddonManager.__init__ (siehe supervisor_client.async_get_addon_manager) resolved
    # sich selbst einen echten Supervisor-Client -- das schluege in diesem leichtgewichtigen
    # hass ohne echt geladene hassio-Integration mit einem KeyError fehl (hass.data[...]).
    # Der Platzhalter wird nie tatsaechlich benutzt: jeder Test, der bis _push_config_and_finish
    # kommt, patcht async_set_addon_options/async_restart_addon direkt auf der Klasse.
    monkeypatch.setattr(
        "homeassistant.components.hassio.addon_manager.get_supervisor_client",
        lambda hass: SimpleNamespace(),
    )
    # Slug-Aufloesung (Repository-Hash-Praefix, siehe Task 14) ist hier bewusst eine
    # Identitaetsfunktion -- die Tests in test_supervisor_client.py decken die eigentliche
    # Aufloesungslogik ab, hier soll nur die Config-Flow-Seite (welche Optionen/Restarts an
    # welchen -- unveraendert bare -- Slug gehen) getestet werden.
    monkeypatch.setattr(
        "custom_components.smartheat.supervisor_client.async_resolve_addon_slugs",
        AsyncMock(side_effect=lambda hass, repository_url, config_slugs: {slug: slug for slug in config_slugs}),
    )


async def test_user_step_aborts_when_not_supervisor(hass, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    assert result["type"] == "abort"
    assert result["reason"] == "not_supervisor"


async def test_user_step_shows_form_initially(hass, monkeypatch):
    _enable_supervisor(hass, monkeypatch)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    # Final-Review-Fix (Finding 2) Regressionsschutz: ein ganz normaler Erststart darf
    # keinen "session_expired"-Hinweis zeigen -- das Flag darf also nicht versehentlich
    # von Anfang an gesetzt sein.
    assert result["errors"] == {}


async def test_user_step_shows_invalid_auth_error(hass, monkeypatch):
    _enable_supervisor(hass, monkeypatch)
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.login",
        AsyncMock(side_effect=InvalidAuth("nope")),
    )

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "a@b.de", "password": "falsch"},
    )

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_step_proceeds_to_tenant_step_on_success(hass, monkeypatch):
    _enable_supervisor(hass, monkeypatch)
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.login",
        AsyncMock(return_value="tok123"),
    )
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.list_tenants",
        AsyncMock(return_value=[{"tenant_id": "wohnung1", "profile_id": "vaillant_gastherme_heizkoerper"}]),
    )

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "a@b.de", "password": "geheim"},
    )

    assert result["type"] == "form"
    assert result["step_id"] == "tenant"


async def _reach_tenant_step(hass, monkeypatch):
    _enable_supervisor(hass, monkeypatch)
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.login",
        AsyncMock(return_value="tok123"),
    )
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.list_tenants",
        AsyncMock(return_value=[{"tenant_id": "wohnung1", "profile_id": "vaillant_gastherme_heizkoerper"}]),
    )
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "a@b.de", "password": "geheim"},
    )


KPI_CAPABILITIES = {
    "has_flow_temperature": True, "has_return_temperature": False,
    "has_operating_mode": True, "has_water_pressure": True,
    "has_manufacturer_efficiency_sensor": True,
    "energy_channels": ["electrical_heating", "thermal_heating"],
}


async def _reach_profile_step(hass, monkeypatch, telemetry_capabilities=None):
    """telemetry_capabilities wird nur bei Bedarf ins erste Profil gemischt: ohne sie
    (Default) ueberspringt die Flow den optionalen kpi_metrics-Schritt, sodass alle
    bestehenden Tests (entities -> finish) unveraendert bleiben."""
    result = await _reach_tenant_step(hass, monkeypatch)
    first_profile = {
        "hersteller": "Vaillant", "erzeuger_typ": "Gastherme", "verteilsystem": "Heizkoerper",
        "profile_id": "vaillant_gastherme_heizkoerper", "verified": True,
    }
    if telemetry_capabilities is not None:
        first_profile["telemetry_capabilities"] = telemetry_capabilities
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.get_catalog",
        AsyncMock(return_value=_catalog(
            first_profile,
            {"hersteller": "Weishaupt", "erzeuger_typ": "Waermepumpe", "verteilsystem": "Fussbodenheizung",
             "profile_id": "weishaupt_waermepumpe_fussbodenheizung", "verified": False},
        )),
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"tenant_id": "wohnung1"},
    )
    return result


async def _reach_profile_step_with_two_verified_profiles(hass, monkeypatch):
    """Zwei VERIFIZIERTE Profile, die sich in genau einer Dimension unterscheiden --
    noetig um zu testen, dass eine Kombination aus (fuer sich genommen) gueltigen
    Einzel-Choices trotzdem als serverseitig unbekannte Kombination abgelehnt wird
    (z.B. Vaillant+Waermepumpe, fuer das es kein Profil gibt)."""
    result = await _reach_tenant_step(hass, monkeypatch)
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.get_catalog",
        AsyncMock(return_value=_catalog(
            {"hersteller": "Vaillant", "erzeuger_typ": "Gastherme", "verteilsystem": "Heizkoerper",
             "profile_id": "vaillant_gastherme_heizkoerper", "verified": True},
            {"hersteller": "Vaillant", "erzeuger_typ": "Waermepumpe", "verteilsystem": "Fussbodenheizung",
             "profile_id": "vaillant_waermepumpe_fussbodenheizung", "verified": True},
        )),
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"tenant_id": "wohnung1"},
    )


async def test_tenant_step_shows_cannot_connect_error(hass, monkeypatch):
    result = await _reach_tenant_step(hass, monkeypatch)
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.get_catalog",
        AsyncMock(side_effect=CannotConnect("nicht erreichbar")),
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"tenant_id": "wohnung1"},
    )

    assert result["type"] == "form"
    assert result["step_id"] == "tenant"
    assert result["errors"]["base"] == "cannot_connect"


async def test_tenant_step_shows_unknown_error_on_api_error(hass, monkeypatch):
    result = await _reach_tenant_step(hass, monkeypatch)
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.get_catalog",
        AsyncMock(side_effect=ApiError("kaputt")),
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"tenant_id": "wohnung1"},
    )

    assert result["type"] == "form"
    assert result["step_id"] == "tenant"
    assert result["errors"]["base"] == "unknown"


async def test_second_flow_with_same_tenant_aborts_as_already_configured(hass, monkeypatch):
    """I5: Single-Instance-Guard -- verhindert zwei Config-Entries fuer dieselbe Anlage
    (siehe async_set_unique_id/_abort_if_unique_id_configured in async_step_tenant)."""
    monkeypatch.setattr(
        "homeassistant.components.hassio.AddonManager.async_set_addon_options", AsyncMock()
    )
    monkeypatch.setattr(
        "homeassistant.components.hassio.AddonManager.async_restart_addon", AsyncMock()
    )
    first = await _reach_finish(hass, monkeypatch)
    assert first["type"] == "create_entry"

    second = await _reach_tenant_step(hass, monkeypatch)
    second = await hass.config_entries.flow.async_configure(
        second["flow_id"], {"tenant_id": "wohnung1"},
    )

    assert second["type"] == "abort"
    assert second["reason"] == "already_configured"


async def test_profile_step_aborts_when_no_verified_profiles(hass, monkeypatch):
    result = await _reach_tenant_step(hass, monkeypatch)
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.get_catalog",
        AsyncMock(return_value=_catalog(
            {"hersteller": "Weishaupt", "erzeuger_typ": "Waermepumpe", "verteilsystem": "Fussbodenheizung",
             "profile_id": "weishaupt_waermepumpe_fussbodenheizung", "verified": False},
        )),
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"tenant_id": "wohnung1"},
    )

    assert result["type"] == "abort"
    assert result["reason"] == "no_verified_profiles"


async def test_tenant_step_proceeds_to_profile_step(hass, monkeypatch):
    result = await _reach_profile_step(hass, monkeypatch)

    assert result["type"] == "form"
    assert result["step_id"] == "profile"


async def test_tenant_step_routes_back_to_user_step_on_invalid_auth(hass, monkeypatch):
    result = await _reach_tenant_step(hass, monkeypatch)
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.get_catalog",
        AsyncMock(side_effect=InvalidAuth("Sitzung abgelaufen")),
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"tenant_id": "wohnung1"},
    )

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    # Final-Review-Fix (Finding 2): der Reset zurueck auf den Login-Schritt darf nicht
    # stillschweigend passieren -- der Nutzer hat bis hierhin schon Login+Tenant-Auswahl
    # investiert und braucht eine Erklaerung, warum er wieder am Anfang steht, statt es
    # mit einem Absturz zu verwechseln.
    assert result["errors"] == {"base": "session_expired"}


def _schema_validator(schema: vol.Schema, field_name: str):
    (validator,) = [v for k, v in schema.schema.items() if str(k) == field_name]
    return validator


def _select_options(select_selector) -> list:
    return select_selector.config["options"]


def _select_values(select_selector) -> set:
    return {o["value"] if isinstance(o, dict) else o for o in _select_options(select_selector)}


async def test_profile_step_uses_real_select_dropdowns(hass, monkeypatch):
    """I3: hersteller/erzeuger_typ/verteilsystem muessen dieselbe SelectSelector-
    Komponente nutzen wie die Sensorauswahl im entities-Schritt (kein rohes vol.In)."""
    from homeassistant.helpers.selector import SelectSelector

    result = await _reach_profile_step(hass, monkeypatch)

    schema = result["data_schema"]
    for field in ("hersteller", "erzeuger_typ", "verteilsystem"):
        validator = _schema_validator(schema, field)
        assert isinstance(validator, SelectSelector)
        assert validator.config["mode"] == "dropdown"


async def test_profile_step_only_offers_verified_profile_dimensions(hass, monkeypatch):
    result = await _reach_profile_step(hass, monkeypatch)

    schema = result["data_schema"]
    # Direkter Beweis, dass nur die Dimensionen des verifizierten Profils (nicht auch
    # Weishaupt/Waermepumpe/Fussbodenheizung, verified=False) im Formular waehlbar sind.
    assert _select_values(_schema_validator(schema, "hersteller")) == {"Vaillant"}
    assert _select_values(_schema_validator(schema, "erzeuger_typ")) == {"Gastherme"}
    assert _select_values(_schema_validator(schema, "verteilsystem")) == {"Heizkoerper"}


async def test_profile_step_dropdown_options_have_umlaut_labels(hass, monkeypatch):
    result = await _reach_profile_step_with_two_verified_profiles(hass, monkeypatch)

    schema = result["data_schema"]
    erzeuger_typ_labels = {o["value"]: o["label"] for o in _select_options(_schema_validator(schema, "erzeuger_typ"))}
    assert erzeuger_typ_labels["Waermepumpe"] == "Wärmepumpe"
    verteilsystem_labels = {
        o["value"]: o["label"] for o in _select_options(_schema_validator(schema, "verteilsystem"))
    }
    assert verteilsystem_labels["Fussbodenheizung"] == "Fußbodenheizung"


async def test_profile_step_proceeds_to_entities_step(hass, monkeypatch):
    result = await _reach_profile_step(hass, monkeypatch)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"hersteller": "Vaillant", "erzeuger_typ": "Gastherme", "verteilsystem": "Heizkoerper"},
    )

    assert result["type"] == "form"
    assert result["step_id"] == "entities"


async def test_profile_step_rejects_unsupported_combination(hass, monkeypatch):
    result = await _reach_profile_step_with_two_verified_profiles(hass, monkeypatch)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        # Gastherme und Fussbodenheizung sind je fuer sich gueltige (verifizierte)
        # Choices, aber "Vaillant+Gastherme+Fussbodenheizung" existiert als Profil nicht.
        {"hersteller": "Vaillant", "erzeuger_typ": "Gastherme", "verteilsystem": "Fussbodenheizung"},
    )

    assert result["type"] == "form"
    assert result["step_id"] == "profile"
    assert result["errors"]["base"] == "profile_combination_unsupported"


async def test_entities_step_rejects_unit_mismatch(hass, monkeypatch):
    hass.states.async_set("sensor.rt", "20.0", {"unit_of_measurement": "K"})
    hass.states.async_set("sensor.target_rt", "20.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.outdoor", "5.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("number.curve", "0.5", {})
    hass.states.async_set("number.offset", "25.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("number.heat_limit", "15.0", {"unit_of_measurement": "°C"})
    result = await _reach_profile_step(hass, monkeypatch)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"hersteller": "Vaillant", "erzeuger_typ": "Gastherme", "verteilsystem": "Heizkoerper"},
    )

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "entity_room_actual": "sensor.rt",
        "entity_room_target": "sensor.target_rt",
        "entity_outdoor_temp": "sensor.outdoor",
        "entity_curve_current": "number.curve",
        "entity_offset_current": "number.offset",
        "entity_heat_limit": "number.heat_limit",
    })

    assert result["type"] == "form"
    assert result["step_id"] == "entities"
    assert result["errors"]["entity_room_actual"] == "unit_mismatch"


async def test_entities_step_flattens_climate_room_target():
    from custom_components.smartheat.config_flow import _resolve_entities
    from unittest.mock import MagicMock

    hass = MagicMock()
    def _get(entity_id):
        if entity_id == "climate.wohnzimmer":
            return MagicMock(attributes={})
        return MagicMock(attributes={"unit_of_measurement": "°C"})
    hass.states.get.side_effect = _get

    resolved, errors = _resolve_entities(hass, {
        "entity_room_actual": "sensor.rt",
        "entity_room_target": "climate.wohnzimmer",
        "entity_outdoor_temp": "sensor.outdoor",
        "entity_curve_current": "number.curve",
        "entity_offset_current": "number.offset",
        "entity_heat_limit": "number.heat_limit",
    })

    assert errors == {}
    assert resolved["entity_room_target"] == "climate.wohnzimmer::temperature"


def _provisioning_response():
    return {
        "username": "wohnung1_abc", "password": "geheim-mqtt", "acl_snippet": "...",
        "cloudflared_hostname": "mqtt-verify.hartfussha.org", "cloudflared_local_port": 18830,
        "cloudflared_service_token_id": "cf-id", "cloudflared_service_token_secret": "cf-secret",
    }


async def _reach_finish(hass, monkeypatch, provision_exception=None):
    """Durchlaeuft die Flow bis (und ueber) den finish-Schritt.

    provision_exception erlaubt es Tests, provision() statt eines erfolgreichen
    Ergebnisses eine Exception werfen zu lassen (siehe
    test_finish_step_routes_back_to_entities_on_provisioning_failure), ohne die
    gesamte Setup-Logik hier zu duplizieren.
    """
    hass.states.async_set("sensor.rt", "20.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.target_rt", "20.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.outdoor", "5.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("number.curve", "0.5", {})
    hass.states.async_set("number.offset", "25.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("number.heat_limit", "15.0", {"unit_of_measurement": "°C"})
    if provision_exception is not None:
        provision_mock = AsyncMock(side_effect=provision_exception)
    else:
        provision_mock = AsyncMock(return_value=_provisioning_response())
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.provision", provision_mock
    )
    result = await _reach_profile_step(hass, monkeypatch)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"hersteller": "Vaillant", "erzeuger_typ": "Gastherme", "verteilsystem": "Heizkoerper"},
    )
    return await hass.config_entries.flow.async_configure(result["flow_id"], {
        "entity_room_actual": "sensor.rt", "entity_room_target": "sensor.target_rt",
        "entity_outdoor_temp": "sensor.outdoor", "entity_curve_current": "number.curve",
        "entity_offset_current": "number.offset", "entity_heat_limit": "number.heat_limit",
    })


async def test_finish_pushes_options_to_both_addons_and_creates_entry(hass, monkeypatch):
    pushed = []
    restarted = []

    # Plain async functions statt AsyncMock: eine AsyncMock-Instanz als Klassenattribut ist
    # kein Descriptor, "self" wird beim Aufruf ueber eine Instanz NICHT automatisch gebunden
    # (siehe .superpowers/sdd/.../progress.md, Task 12) -- hier wird self.addon_slug aber
    # gebraucht, um pro Add-on-Instanz zuzuordnen, welcher (bare, dank der oben in
    # _enable_supervisor gepatchten Identitaets-Slug-Aufloesung unveraenderte) Slug betroffen war.
    async def _record_options(self, config):
        pushed.append((self.addon_slug, config))

    async def _record_restart(self):
        restarted.append(self.addon_slug)

    monkeypatch.setattr(
        "homeassistant.components.hassio.AddonManager.async_set_addon_options", _record_options
    )
    monkeypatch.setattr(
        "homeassistant.components.hassio.AddonManager.async_restart_addon", _record_restart
    )

    result = await _reach_finish(hass, monkeypatch)

    assert result["type"] == "create_entry"
    assert result["data"] == {"tenant_id": "wohnung1", "profile_id": "vaillant_gastherme_heizkoerper"}
    pushed_slugs = {slug for slug, _ in pushed}
    assert pushed_slugs == {"heizungsbruecke", "cloudflared_access_mqtt"}
    heizungsbruecke_options = next(o for slug, o in pushed if slug == "heizungsbruecke")
    assert heizungsbruecke_options["tenant_id"] == "wohnung1"
    assert heizungsbruecke_options["profile"] == "vaillant_gastherme_heizkoerper"
    assert heizungsbruecke_options["mqtt_username"] == "wohnung1_abc"
    assert heizungsbruecke_options["mqtt_password"] == "geheim-mqtt"
    assert heizungsbruecke_options["entity_room_actual"] == "sensor.rt"
    cloudflared_options = next(o for slug, o in pushed if slug == "cloudflared_access_mqtt")
    assert cloudflared_options == {
        "hostname": "mqtt-verify.hartfussha.org", "local_port": 18830,
        "service_token_id": "cf-id", "service_token_secret": "cf-secret",
    }
    assert set(restarted) == {"heizungsbruecke", "cloudflared_access_mqtt"}


async def test_finish_shows_retry_step_with_credentials_on_supervisor_failure(hass, monkeypatch):
    from homeassistant.components.hassio import AddonError

    monkeypatch.setattr(
        "homeassistant.components.hassio.AddonManager.async_set_addon_options",
        AsyncMock(side_effect=AddonError("Supervisor nicht erreichbar")),
    )

    result = await _reach_finish(hass, monkeypatch)

    assert result["type"] == "form"
    assert result["step_id"] == "retry_push"
    assert result["description_placeholders"]["mqtt_username"] == "wohnung1_abc"
    # Task 2b: fuer den generischen AddonError-Fall (im Gegensatz zu AmbiguousAddonMatchError
    # unten) gibt es keinen spezifischen Loesungshinweis -- Regressionsschutz, dass der
    # generische Fall nicht ploetzlich Ambiguous-Text zeigt. Seit dem KPI-Review-Fix zeigt
    # er stattdessen die (bereinigte) Supervisor-Meldung.
    assert result["description_placeholders"]["error_detail"] == "Supervisor nicht erreichbar"


async def test_retry_push_succeeds_without_reprovisioning(hass, monkeypatch):
    from homeassistant.components.hassio import AddonError

    from custom_components.smartheat.config_flow import HeizungsserverClient

    set_options_mock = AsyncMock(side_effect=AddonError("kaputt"))
    monkeypatch.setattr(
        "homeassistant.components.hassio.AddonManager.async_set_addon_options", set_options_mock
    )
    monkeypatch.setattr(
        "homeassistant.components.hassio.AddonManager.async_restart_addon", AsyncMock()
    )

    result = await _reach_finish(hass, monkeypatch)
    assert result["step_id"] == "retry_push"

    # _reach_finish patcht HeizungsserverClient.provision bereits selbst (mit
    # AsyncMock(return_value=_provisioning_response())) -- die tatsaechlich installierte
    # Mock-Instanz hier abgreifen statt sie vorher redundant (und wirkungslos, da
    # monkeypatch.setattr "last wins" ist) selbst zu patchen.
    provision_mock = HeizungsserverClient.provision

    set_options_mock.side_effect = None
    set_options_mock.return_value = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == "create_entry"
    assert provision_mock.call_count == 1  # nicht erneut aufgerufen beim Retry


async def test_finish_step_routes_back_to_entities_on_provisioning_failure(hass, monkeypatch):
    result = await _reach_finish(hass, monkeypatch, provision_exception=ApiError("Provisioning kaputt"))

    assert result["type"] == "form"
    assert result["step_id"] == "entities"
    assert result["errors"]["base"] == "provisioning_failed"


async def test_finish_step_routes_back_to_user_step_on_invalid_auth(hass, monkeypatch):
    result = await _reach_finish(hass, monkeypatch, provision_exception=InvalidAuth("Sitzung abgelaufen"))

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    # Final-Review-Fix (Finding 2): an dieser Stelle im Flow hat der Nutzer bereits
    # Login, Tenant, Profil UND alle sechs Entity-Zuordnungen gemacht -- ein
    # kommentarloser Reset waere hier am schlimmsten. Muss denselben Hinweis zeigen wie
    # der Tenant-Schritt oben.
    assert result["errors"] == {"base": "session_expired"}


async def test_finish_step_routes_back_to_user_step_on_real_401_from_provision(
    hass, monkeypatch, aiohttp_client, socket_enabled
):
    """Task 2a: der Vorgaenger-Test oben mockt provision() direkt auf InvalidAuth --
    das haette auch dann gruen gezeigt, wenn provision() selbst nie InvalidAuth wirft
    (nur ApiError, siehe api_client.py), weil hier gar nicht provision()s eigene
    401-Behandlung durchlaufen wird. Dieser Test laesst provision() unangetastet und
    schickt einen echten HTTP-401 durch die tatsaechliche Implementierung, um genau
    diese Luecke (die den Re-Login-Branch in async_step_finish tot liegen liess) zu
    schliessen."""
    from aiohttp import web

    async def handler(request):
        return web.json_response({"error": "Sitzung abgelaufen"}, status=401)

    app = web.Application()
    app.router.add_post("/tenants/wohnung1/provision", handler)
    fake_server = await aiohttp_client(app)
    fake_base_url = str(fake_server.make_url("")).rstrip("/")
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.DEFAULT_HEIZUNGSSERVER_BASE_URL", fake_base_url
    )

    hass.states.async_set("sensor.rt", "20.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.target_rt", "20.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.outdoor", "5.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("number.curve", "0.5", {})
    hass.states.async_set("number.offset", "25.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("number.heat_limit", "15.0", {"unit_of_measurement": "°C"})

    # login/list_tenants/get_catalog bleiben wie ueberall sonst gemockt (_reach_profile_step) --
    # nur provision() selbst laeuft tatsaechlich gegen den obigen Fake-Server, der ein reales
    # HTTP-401 liefert.
    result = await _reach_profile_step(hass, monkeypatch)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"hersteller": "Vaillant", "erzeuger_typ": "Gastherme", "verteilsystem": "Heizkoerper"},
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "entity_room_actual": "sensor.rt", "entity_room_target": "sensor.target_rt",
        "entity_outdoor_temp": "sensor.outdoor", "entity_curve_current": "number.curve",
        "entity_offset_current": "number.offset", "entity_heat_limit": "number.heat_limit",
    })

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "session_expired"}


async def test_successful_flow_creates_a_loaded_config_entry(hass, monkeypatch):
    from homeassistant.config_entries import ConfigEntryState

    monkeypatch.setattr(
        "homeassistant.components.hassio.AddonManager.async_set_addon_options", AsyncMock()
    )
    monkeypatch.setattr(
        "homeassistant.components.hassio.AddonManager.async_restart_addon", AsyncMock()
    )

    result = await _reach_finish(hass, monkeypatch)
    assert result["type"] == "create_entry"

    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].state == ConfigEntryState.LOADED


async def test_finish_resolves_both_addons_from_a_single_supervisor_call(hass, monkeypatch):
    # _enable_supervisor (invoked internally via _reach_tenant_step, which every
    # _reach_finish call goes through) already patches async_resolve_addon_slugs to an
    # AsyncMock identity function -- rather than pre-patching our own (which
    # _enable_supervisor's later call would silently clobber, since it runs *after* any
    # patch a test applies before awaiting _reach_finish), inspect that same installed
    # mock's call_count afterwards. It's the exact function _push_config_and_finish now
    # calls via async_get_addon_managers, so this still verifies si-3: one resolution
    # call for both add-ons, not two (previously async_get_addon_manager was called
    # once per add-on).
    monkeypatch.setattr(
        "homeassistant.components.hassio.AddonManager.async_set_addon_options", AsyncMock()
    )
    monkeypatch.setattr(
        "homeassistant.components.hassio.AddonManager.async_restart_addon", AsyncMock()
    )

    result = await _reach_finish(hass, monkeypatch)

    assert result["type"] == "create_entry"
    from custom_components.smartheat import supervisor_client
    assert supervisor_client.async_resolve_addon_slugs.call_count == 1


async def test_finish_shows_retry_step_on_ambiguous_addon_match(hass, monkeypatch):
    from custom_components.smartheat.supervisor_client import AmbiguousAddonMatchError

    # Reimplements _reach_finish's tail instead of calling it directly: _enable_supervisor
    # (via _reach_tenant_step, reached inside _reach_profile_step below) installs its own
    # identity AsyncMock for async_resolve_addon_slugs -- a patch applied *before* that call
    # would just be overwritten by it. Patching the raising mock in *after* _reach_profile_step
    # returns (i.e. after _enable_supervisor has already run) makes it stick through the
    # remaining profile/entities steps and into _push_config_and_finish.
    hass.states.async_set("sensor.rt", "20.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.target_rt", "20.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.outdoor", "5.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("number.curve", "0.5", {})
    hass.states.async_set("number.offset", "25.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("number.heat_limit", "15.0", {"unit_of_measurement": "°C"})
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.provision",
        AsyncMock(return_value=_provisioning_response()),
    )
    result = await _reach_profile_step(hass, monkeypatch)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"hersteller": "Vaillant", "erzeuger_typ": "Gastherme", "verteilsystem": "Heizkoerper"},
    )
    monkeypatch.setattr(
        "custom_components.smartheat.supervisor_client.async_resolve_addon_slugs",
        AsyncMock(side_effect=AmbiguousAddonMatchError("mehrdeutig")),
    )

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "entity_room_actual": "sensor.rt", "entity_room_target": "sensor.target_rt",
        "entity_outdoor_temp": "sensor.outdoor", "entity_curve_current": "number.curve",
        "entity_offset_current": "number.offset", "entity_heat_limit": "number.heat_limit",
    })

    assert result["type"] == "form"
    assert result["step_id"] == "retry_push"
    # Task 2b: im Gegensatz zu AddonError/AddonNotFoundError (siehe die generischen Tests
    # oben, test_finish_shows_retry_step_with_credentials_on_supervisor_failure und
    # test_finish_shows_generic_hint_on_addon_not_found) braucht AmbiguousAddonMatchError
    # einen konkreten Loesungshinweis in der UI, weil der Nutzer hier tatsaechlich etwas
    # Bestimmtes tun muss (doppelte Add-on-Installation im Supervisor entfernen), statt es
    # nur erneut zu versuchen.
    #
    # Final-Review-Fix (Finding 1): "Supervisor" allein war kein taugliches Kriterium --
    # das Wort ist in beiden Sprachen identisch, ein Regressions-auf-hartcodiertes-Deutsch
    # waere hier nie aufgefallen. Stattdessen exakt gegen den lokalisierten Text aus
    # translations/en.json pruefen (hass.config.language ist im Test-Harness per Default
    # "en") und zusaetzlich sicherstellen, dass der deutsche Text NICHT drin ist.
    error_detail = result["description_placeholders"]["error_detail"]
    assert error_detail != ""
    assert error_detail == (
        "Multiple matching add-on installations were found. Please remove the "
        "duplicate/outdated installation in the Supervisor, then try again."
    )
    assert "doppelte" not in error_detail

    # Konsistenz-Check aus dem Brief: der Fehlertyp muss auch beim erneuten Anzeigen des
    # Formulars (async_step_retry_push mit user_input=None, z.B. nach einem Reload) erhalten
    # bleiben, nicht nur direkt nach dem ersten Fehlschlag.
    flow = hass.config_entries.flow._progress[result["flow_id"]]
    redisplayed = await flow.async_step_retry_push(None)
    assert redisplayed["description_placeholders"]["error_detail"] == error_detail


async def test_finish_shows_retry_step_on_ambiguous_addon_match_localized_to_german(hass, monkeypatch):
    """Final-Review-Fix (Finding 1): derselbe Ablauf wie oben, aber mit
    hass.config.language == "de" -- beweist, dass der Hinweistext tatsaechlich aus den
    Uebersetzungsdateien nachgeschlagen wird (translation.async_get_translations),
    nicht ein hartcodierter deutscher Python-String ist, der zufaellig auch fuer
    Deutsch passt."""
    from custom_components.smartheat.supervisor_client import AmbiguousAddonMatchError

    hass.config.language = "de"
    hass.states.async_set("sensor.rt", "20.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.target_rt", "20.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.outdoor", "5.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("number.curve", "0.5", {})
    hass.states.async_set("number.offset", "25.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("number.heat_limit", "15.0", {"unit_of_measurement": "°C"})
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.provision",
        AsyncMock(return_value=_provisioning_response()),
    )
    result = await _reach_profile_step(hass, monkeypatch)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"hersteller": "Vaillant", "erzeuger_typ": "Gastherme", "verteilsystem": "Heizkoerper"},
    )
    monkeypatch.setattr(
        "custom_components.smartheat.supervisor_client.async_resolve_addon_slugs",
        AsyncMock(side_effect=AmbiguousAddonMatchError("mehrdeutig")),
    )

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "entity_room_actual": "sensor.rt", "entity_room_target": "sensor.target_rt",
        "entity_outdoor_temp": "sensor.outdoor", "entity_curve_current": "number.curve",
        "entity_offset_current": "number.offset", "entity_heat_limit": "number.heat_limit",
    })

    assert result["type"] == "form"
    assert result["step_id"] == "retry_push"
    error_detail = result["description_placeholders"]["error_detail"]
    assert error_detail == (
        "Mehrere passende Add-on-Installationen gefunden. Bitte die doppelte/"
        "veraltete Installation im Supervisor entfernen, dann erneut versuchen."
    )


async def test_finish_shows_generic_hint_on_addon_not_found(hass, monkeypatch):
    # Task 2b Regressionsschutz: AddonNotFoundError (wie AddonError) bekommt weiterhin den
    # bisherigen generischen Text (leeres error_detail), keinen der Ambiguous-spezifischen
    # Hinweistexte.
    from custom_components.smartheat.supervisor_client import AddonNotFoundError

    hass.states.async_set("sensor.rt", "20.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.target_rt", "20.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.outdoor", "5.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("number.curve", "0.5", {})
    hass.states.async_set("number.offset", "25.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("number.heat_limit", "15.0", {"unit_of_measurement": "°C"})
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.provision",
        AsyncMock(return_value=_provisioning_response()),
    )
    result = await _reach_profile_step(hass, monkeypatch)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"hersteller": "Vaillant", "erzeuger_typ": "Gastherme", "verteilsystem": "Heizkoerper"},
    )
    monkeypatch.setattr(
        "custom_components.smartheat.supervisor_client.async_resolve_addon_slugs",
        AsyncMock(side_effect=AddonNotFoundError("nicht gefunden")),
    )

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "entity_room_actual": "sensor.rt", "entity_room_target": "sensor.target_rt",
        "entity_outdoor_temp": "sensor.outdoor", "entity_curve_current": "number.curve",
        "entity_offset_current": "number.offset", "entity_heat_limit": "number.heat_limit",
    })

    assert result["type"] == "form"
    assert result["step_id"] == "retry_push"
    assert result["description_placeholders"]["error_detail"] == ""


async def _reach_kpi_metrics_step(hass, monkeypatch, capabilities=KPI_CAPABILITIES):
    result = await _reach_profile_step(hass, monkeypatch, telemetry_capabilities=capabilities)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"hersteller": "Vaillant", "erzeuger_typ": "Gastherme", "verteilsystem": "Heizkoerper"},
    )
    hass.states.async_set("sensor.rt", "20.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.target_rt", "20.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.outdoor", "5.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("number.curve", "0.5", {})
    hass.states.async_set("number.offset", "25.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("number.heat_limit", "15.0", {"unit_of_measurement": "°C"})
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "entity_room_actual": "sensor.rt", "entity_room_target": "sensor.target_rt",
            "entity_outdoor_temp": "sensor.outdoor", "entity_curve_current": "number.curve",
            "entity_offset_current": "number.offset", "entity_heat_limit": "number.heat_limit",
        },
    )


def _mock_provision(monkeypatch):
    monkeypatch.setattr(
        "custom_components.smartheat.config_flow.HeizungsserverClient.provision",
        AsyncMock(return_value=_provisioning_response()),
    )
    # Nach provision() geht die Flow in den Add-on-Push -- hier folgenlos stubben.
    monkeypatch.setattr(
        "homeassistant.components.hassio.AddonManager.async_set_addon_options", AsyncMock()
    )
    monkeypatch.setattr(
        "homeassistant.components.hassio.AddonManager.async_restart_addon", AsyncMock()
    )


async def test_entities_step_proceeds_to_kpi_metrics_step(hass, monkeypatch):
    result = await _reach_kpi_metrics_step(hass, monkeypatch)

    assert result["type"] == "form"
    assert result["step_id"] == "kpi_metrics"


async def test_kpi_metrics_step_only_offers_fields_the_profile_supports(hass, monkeypatch):
    result = await _reach_kpi_metrics_step(hass, monkeypatch)

    schema_fields = {str(key) for key in result["data_schema"].schema}
    assert "entity_flow_temperature" in schema_fields
    assert "entity_return_temperature" not in schema_fields
    assert "entity_energy_electrical_heating" in schema_fields
    assert "entity_energy_thermal_dhw" not in schema_fields


async def test_kpi_metrics_step_all_fields_are_optional_and_can_be_skipped(hass, monkeypatch):
    result = await _reach_kpi_metrics_step(hass, monkeypatch)

    for key in result["data_schema"].schema:
        assert not isinstance(key, vol.Required)


async def test_kpi_metrics_step_validates_state_class_for_energy_fields(hass, monkeypatch):
    result = await _reach_kpi_metrics_step(hass, monkeypatch)
    hass.states.async_set("sensor.energy", "100.0", {"state_class": "measurement"})

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"entity_energy_electrical_heating": "sensor.energy"},
    )

    assert result["type"] == "form"
    assert result["step_id"] == "kpi_metrics"
    assert result["errors"]["entity_energy_electrical_heating"] == "state_class_expected_total_increasing"


async def test_kpi_metrics_step_validates_state_class_for_temperature_fields(hass, monkeypatch):
    result = await _reach_kpi_metrics_step(hass, monkeypatch)
    hass.states.async_set("sensor.flow", "45.0", {"state_class": "total_increasing"})

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"entity_flow_temperature": "sensor.flow"},
    )

    assert result["type"] == "form"
    assert result["errors"]["entity_flow_temperature"] == "state_class_expected_measurement"


async def test_kpi_metrics_step_reports_missing_entity(hass, monkeypatch):
    result = await _reach_kpi_metrics_step(hass, monkeypatch)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"entity_flow_temperature": "sensor.does_not_exist"},
    )

    assert result["step_id"] == "kpi_metrics"
    assert result["errors"]["entity_flow_temperature"] == "entity_not_found"


async def test_kpi_metrics_step_operating_mode_needs_no_state_class(hass, monkeypatch):
    result = await _reach_kpi_metrics_step(hass, monkeypatch)
    hass.states.async_set("sensor.mode", "heating", {})
    _mock_provision(monkeypatch)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"entity_operating_mode": "sensor.mode"},
    )

    assert result["type"] == "create_entry"


async def test_kpi_metrics_step_submitting_empty_form_proceeds_to_finish(hass, monkeypatch):
    result = await _reach_kpi_metrics_step(hass, monkeypatch)
    _mock_provision(monkeypatch)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == "create_entry"


async def test_kpi_metrics_entities_are_merged_into_heizungsbruecke_options(hass, monkeypatch):
    pushed = []

    async def _record_options(self, config):
        pushed.append((self.addon_slug, config))

    async def _noop_restart(self):
        return None

    result = await _reach_kpi_metrics_step(hass, monkeypatch)
    hass.states.async_set("sensor.flow", "45.0", {"state_class": "measurement"})
    hass.states.async_set("sensor.energy", "100.0", {"state_class": "total_increasing"})
    _mock_provision(monkeypatch)
    monkeypatch.setattr(
        "homeassistant.components.hassio.AddonManager.async_set_addon_options", _record_options
    )
    monkeypatch.setattr(
        "homeassistant.components.hassio.AddonManager.async_restart_addon", _noop_restart
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"entity_flow_temperature": "sensor.flow", "entity_energy_thermal_heating": "sensor.energy"},
    )

    assert result["type"] == "create_entry"
    options = next(o for slug, o in pushed if slug == "heizungsbruecke")
    assert options["entity_flow_temperature"] == "sensor.flow"
    assert options["entity_energy_thermal_heating"] == "sensor.energy"
    assert options["entity_room_actual"] == "sensor.rt"


@pytest.mark.parametrize("capabilities", [None, {}, {"energy_channels": []}, {"has_flow_temperature": False}])
async def test_kpi_metrics_step_skipped_when_profile_has_no_usable_capabilities(
    hass, monkeypatch, capabilities
):
    """Fehlender Key, None, leeres Dict: kein AttributeError/KeyError, Schritt entfaellt."""
    _mock_provision(monkeypatch)
    result = await _reach_kpi_metrics_step(hass, monkeypatch, capabilities=capabilities)

    assert result["type"] == "create_entry"


async def test_kpi_metrics_step_ignores_unknown_energy_channels(hass, monkeypatch, caplog):
    caps = {"energy_channels": ["thermal_heating", "bogus_channel"]}
    result = await _reach_kpi_metrics_step(hass, monkeypatch, capabilities=caps)

    fields = {str(k) for k in result["data_schema"].schema}
    assert fields == {"entity_energy_thermal_heating"}
    assert "bogus_channel" in caplog.text


async def test_kpi_metrics_step_skipped_when_only_unknown_energy_channels(hass, monkeypatch):
    _mock_provision(monkeypatch)
    result = await _reach_kpi_metrics_step(
        hass, monkeypatch, capabilities={"energy_channels": ["bogus_a", "bogus_b"]}
    )

    assert result["type"] == "create_entry"


async def test_finish_shows_addon_error_detail_without_secrets(hass, monkeypatch):
    from homeassistant.components.hassio import AddonError

    result = await _reach_kpi_metrics_step(hass, monkeypatch)
    hass.states.async_set("sensor.flow", "45.0", {"state_class": "measurement"})
    _mock_provision(monkeypatch)
    monkeypatch.setattr(
        "homeassistant.components.hassio.AddonManager.async_set_addon_options",
        AsyncMock(side_effect=AddonError(
            "Unknown option 'entity_flow_temperature' (pw geheim-mqtt)")),
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"entity_flow_temperature": "sensor.flow"},
    )

    assert result["step_id"] == "retry_push"
    detail = result["description_placeholders"]["error_detail"]
    assert "entity_flow_temperature" in detail
    assert "geheim-mqtt" not in detail
