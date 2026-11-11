"""Basis der SmartHeat-Entities: ein Geraet je Tenant, Wert aus dem Coordinator, bis dahin der
zuletzt gespeicherte Stand (RestoreEntity). Die Entities haengen am Geraet der Anlage."""
from __future__ import annotations

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import State, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.restore_state import RestoreEntity

from .const import entity_id
from .coordinator import SmartHeatCoordinator
from .geraete import anlage_kennung


class SmartHeatEntity(RestoreEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: SmartHeatCoordinator, platform: str, key: str) -> None:
        self.coordinator = coordinator
        tenant_id = coordinator.tenant_id
        self.entity_id = entity_id(platform, tenant_id, key)
        self._attr_unique_id = f"{tenant_id}_{key}"
        self._attr_translation_key = key
        # Name, Modell und Version des Geraets setzt der Coordinator aus dem Status-Ereignis (geraete.py).
        self._attr_device_info = DeviceInfo(identifiers={anlage_kennung(tenant_id)})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._has_value():
            self._apply()
        elif (last := await self.async_get_last_state()) is not None and last.state not in (
            STATE_UNKNOWN, STATE_UNAVAILABLE,
        ):
            self._restore(last)
        self.async_on_remove(async_dispatcher_connect(self.hass, self.coordinator.signal, self._handle_update))

    @property
    def available(self) -> bool:
        """Nicht verfuegbar, solange Add-on und Integration kein gemeinsames Schema sprechen: die zuletzt gelesenen
        Werte waeren dann veraltet. Der Status-Sensor bleibt verfuegbar und nennt den Befund."""
        return self.coordinator.version_status is None

    @callback
    def _handle_update(self) -> None:
        if self._has_value():
            self._apply()
        self.async_write_ha_state()

    def _has_value(self) -> bool:
        return self.coordinator.data is not None

    def _apply(self) -> None:
        raise NotImplementedError

    def _restore(self, last: State) -> None:
        raise NotImplementedError
