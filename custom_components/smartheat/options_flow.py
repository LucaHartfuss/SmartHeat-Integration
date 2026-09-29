"""Optionen ohne Login (Spec TP7 2.2): Handy-Empfaenger, Hinweis-Schalter, Raumfuehler,
Batteriesensoren und Solltemperatur. Dieselben harten Pruefungen und Warnungen wie im Wizard.
Gespeichert wird in entry.options und, zusammengefuehrt, in den Optionen der Heizungsbruecke --
Zugangsdaten, cloudflared und Server bleiben unberuehrt. Danach Neustart der Heizungsbruecke und
Warten auf das Status-Event mit der neuen setup_id; ein Timeout speichert trotzdem."""
from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol
from homeassistant.components.hassio import AddonError
from homeassistant.config_entries import OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import detection, validation
from .addon_control import WAIT_DONE, WAIT_FAILED, StatusListener, async_update_addon_options
from .const import (
    ADDON_SPECS,
    DATA_INCOMPLETE,
    HINT_CATEGORIES,
    OPTION_BATTERY_ENTITIES,
    OPTION_ENTITY_ROOM_TARGET,
    OPTION_NOTIFY_HINTS_OFF,
    OPTION_NOTIFY_SERVICES,
    OPTION_ROOM_SENSORS,
    OPTION_SETUP_ID,
    ROLE_DOMAINS,
    ROOM_SENSOR_DOMAINS,
    STATUS_KONFIGURATIONSFEHLER,
    STATUS_REGELT,
    STATUS_WAIT_SECONDS,
    STATUS_ZUGANG_ABGELEHNT,
)
from .flow_progress import ProgressFlowMixin
from .supervisor_client import (
    AddonNotFoundError,
    AddonOutdatedError,
    AmbiguousAddonMatchError,
    async_get_addon_managers,
)
from .texts import async_hint

_LOGGER = logging.getLogger(__name__)

ROOM_FIELDS = (OPTION_ROOM_SENSORS, OPTION_ENTITY_ROOM_TARGET)


def _hint_field(category: str) -> str:
    return f"hint_{category}"


class SmartHeatOptionsFlow(ProgressFlowMixin, OptionsFlow):
    def __init__(self) -> None:
        self._input: dict = {}
        self._new: dict | None = None
        self._warnings: dict[str, list[str]] = {}
        self._setup_id: str | None = None
        self._status: StatusListener | None = None
        self._error = ""

    @callback
    def async_remove(self) -> None:
        if self._status is not None:
            self._status.close()
            self._status = None

    def _services(self) -> list[str]:
        return detection.mobile_app_services(self.hass.services.async_services_for_domain("notify"))

    def _notify_options(self, services: list[str]) -> list[str]:
        """Aktuell registrierte Handys plus gespeicherte, aber gerade nicht registrierte (Begleit-
        App kurz offline oder noch nicht geladen). Sonst wuerde das Feld beim Oeffnen entweder ganz
        verschwinden oder ein gespeichertes Handy aus der Auswahl fallen, und ein Speichern ohne
        Absicht des Kunden wuerde es aus notify_services entfernen."""
        stored = self.config_entry.options.get(OPTION_NOTIFY_SERVICES, [])
        return [*services, *[s for s in stored if s not in services]]

    def _suggested(self, notify_options: list[str]) -> dict:
        if self._input:
            return self._input  # nach einem Fehler die Auswahl des Kunden
        options = self.config_entry.options
        return {
            OPTION_ROOM_SENSORS: [validation.entity_of(ref) for ref in options.get(OPTION_ROOM_SENSORS, [])],
            OPTION_ENTITY_ROOM_TARGET: validation.entity_of(options.get(OPTION_ENTITY_ROOM_TARGET, "")),
            OPTION_NOTIFY_SERVICES: [s for s in notify_options if s in options.get(OPTION_NOTIFY_SERVICES, [])],
            OPTION_BATTERY_ENTITIES: list(options.get(OPTION_BATTERY_ENTITIES, [])),
        }

    def _schema(self, notify_options: list[str]) -> vol.Schema:
        hints_off = self.config_entry.options.get(OPTION_NOTIFY_HINTS_OFF, [])
        schema: dict[vol.Marker, Any] = {
            vol.Required(OPTION_ROOM_SENSORS): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=ROOM_SENSOR_DOMAINS, multiple=True),
            ),
            vol.Required(OPTION_ENTITY_ROOM_TARGET): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=ROLE_DOMAINS[OPTION_ENTITY_ROOM_TARGET]),
            ),
            vol.Optional(OPTION_BATTERY_ENTITIES): selector.EntitySelector(selector.EntitySelectorConfig(
                domain=["sensor", "binary_sensor"], device_class="battery", multiple=True,
            )),
        }
        if notify_options:
            schema[vol.Optional(OPTION_NOTIFY_SERVICES)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=notify_options, multiple=True, mode=selector.SelectSelectorMode.LIST,
                ),
            )
        for category in HINT_CATEGORIES:
            schema[vol.Required(_hint_field(category), default=category not in hints_off)] = bool
        return vol.Schema(schema)

    async def async_step_init(self, user_input: dict | None = None):
        if self.config_entry.data.get(DATA_INCOMPLETE):
            return self.async_abort(reason="setup_incomplete")
        notify_options = self._notify_options(self._services())
        errors: dict[str, str] = {}
        if user_input is not None:
            self._input = dict(user_input)
            new, errors = self._validate(user_input, notify_options)
            if not errors:
                self._new = new
                self._warnings = self._collect_warnings(new)
                if self._warnings:
                    return await self.async_step_confirm()
                return await self.async_step_apply()
        return self.async_show_form(
            step_id="init", errors=errors,
            data_schema=self.add_suggested_values_to_schema(
                self._schema(notify_options), self._suggested(notify_options),
            ),
        )

    def _validate(self, user_input: dict, notify_options: list[str]) -> tuple[dict, dict[str, str]]:
        refs, target, errors = validation.check_rooms(
            self.hass, user_input.get(OPTION_ROOM_SENSORS) or [], user_input[OPTION_ENTITY_ROOM_TARGET],
        )
        if not errors:
            plant = self.config_entry.data.get("entities", {})
            duplicates = validation.duplicate_fields({
                OPTION_ROOM_SENSORS: refs, OPTION_ENTITY_ROOM_TARGET: [target],
                **{field: [entity] for field, entity in plant.items()},
            })
            errors = {field: error for field, error in duplicates.items() if field in ROOM_FIELDS}
        chosen = user_input.get(OPTION_NOTIFY_SERVICES) or []
        new = {
            OPTION_ROOM_SENSORS: refs,
            OPTION_ENTITY_ROOM_TARGET: target,
            OPTION_NOTIFY_SERVICES: [service for service in notify_options if service in chosen],
            OPTION_BATTERY_ENTITIES: list(user_input.get(OPTION_BATTERY_ENTITIES) or []),
            OPTION_NOTIFY_HINTS_OFF: [c for c in HINT_CATEGORIES if not user_input.get(_hint_field(c), True)],
        }
        return new, errors

    def _collect_warnings(self, new: dict) -> dict[str, list[str]]:
        refs = [*new[OPTION_ROOM_SENSORS], new[OPTION_ENTITY_ROOM_TARGET]]
        return validation.collect_warnings(self.hass, refs, new[OPTION_ROOM_SENSORS])

    async def async_step_confirm(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            if all(user_input.get(f"confirm_{kind}") for kind in self._warnings):
                return await self.async_step_apply()
            errors["base"] = "warnings_not_confirmed"
        lines = [
            f"- {await async_hint(self.hass, f'warning_{kind}', entities=', '.join(entities))}"
            for kind, entities in self._warnings.items()
        ]
        return self.async_show_form(
            step_id="confirm", errors=errors,
            data_schema=vol.Schema({vol.Required(f"confirm_{kind}", default=False): bool for kind in self._warnings}),
            description_placeholders={"warnings": "\n".join(lines)},
        )

    async def async_step_apply(self, user_input: dict | None = None):
        return await self._run_progress("apply", "apply", self._apply)

    async def _progress_error_step(self) -> str:
        self._error = await async_hint(self.hass, "setup_unexpected")
        return "failed"

    async def _apply(self) -> str:
        new = self._new
        assert new is not None  # async_step_apply() folgt nur nach async_step_init(), das self._new setzt
        self._setup_id = uuid.uuid4().hex
        if self._status is None:
            self._status = StatusListener(self.hass, self.config_entry.data["tenant_id"])
        try:
            heizungsbruecke, _ = await async_get_addon_managers(self.hass, ADDON_SPECS)
            await async_update_addon_options(heizungsbruecke, {**new, OPTION_SETUP_ID: self._setup_id})
            await heizungsbruecke.async_restart_addon()
        except (AddonNotFoundError, AmbiguousAddonMatchError, AddonOutdatedError, AddonError) as error:
            # Nicht den Supervisor-Text zeigen: er kann Optionswerte zitieren.
            _LOGGER.warning("Optionen konnten nicht ins Add-on geschrieben werden: %s", type(error).__name__)
            self._error = await async_hint(self.hass, "options_addon_failed")
            return "failed"
        outcome, grund = await self._status.async_wait(
            setup_id=self._setup_id, done=frozenset({STATUS_REGELT}),
            failed=frozenset({STATUS_KONFIGURATIONSFEHLER, STATUS_ZUGANG_ABGELEHNT}), timeout=STATUS_WAIT_SECONDS,
        )
        if outcome == WAIT_FAILED:
            self._error = grund or ""
            return "failed"
        return "save" if outcome == WAIT_DONE else "timeout"

    async def async_step_save(self, user_input: dict | None = None):
        new = self._new
        assert new is not None  # async_step_save() folgt nur nach _apply(), das self._new voraussetzt
        return self.async_create_entry(data=new)

    async def async_step_timeout(self, user_input: dict | None = None):
        """Spec TP7 2.5: speichern trotzdem, der Status ist an den Entities sichtbar."""
        if user_input is not None:
            new = self._new
            assert new is not None  # siehe async_step_save()
            return self.async_create_entry(data=new)
        return self.async_show_form(step_id="timeout", data_schema=vol.Schema({}))

    async def async_step_failed(self, user_input: dict | None = None):
        if user_input is not None:
            return await self.async_step_init()
        return self.async_show_form(
            step_id="failed", data_schema=vol.Schema({}), description_placeholders={"grund": self._error},
        )
