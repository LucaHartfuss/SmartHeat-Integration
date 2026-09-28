"""Gemeinsame Fortschritts-Mechanik fuer Config- und Options-Flow: ein Hintergrund-Task je
Fortschritts-Schritt, `async_show_progress`/`async_show_progress_done` je nach Task-Stand.
Extrahiert aus dem urspruenglich in beiden Flows duplizierten `_progress` (Controller-Ruling F5,
TP7 Task 16); Fehlertext und Folgeschritt bei einer unerwarteten Ausnahme bleiben Sache des
jeweiligen Flows (`_progress_error_step`)."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class ProgressFlowMixin:
    """Erwartet vom Flow `hass` sowie `async_show_progress`/`async_show_progress_done` (beide von
    HAs `FlowHandler`, Basis von ConfigFlow und OptionsFlow) und implementiert selbst
    `_progress_error_step()` -- setzt den flow-eigenen Fehlertext und liefert den Schritt fuer eine
    unerwartete Ausnahme im Job."""

    _progress_task: asyncio.Task | None = None

    # Von der jeweiligen Flow-Basisklasse (ConfigFlow/OptionsFlow) bereitgestellt; hier nur als
    # Typ-Deklaration fuer den Checker, da der Mixin selbst keine von beiden erbt -- ohne
    # Zuweisung, also ohne Laufzeitwirkung (echte Werte kommen von der Basisklasse).
    if TYPE_CHECKING:
        hass: HomeAssistant

        def async_show_progress(self, **kwargs: Any) -> Any: ...

        def async_show_progress_done(self, **kwargs: Any) -> Any: ...

    async def _progress_error_step(self) -> str:
        """Wird von jedem konkreten Flow ueberschrieben (config_flow.py, options_flow.py)."""
        raise NotImplementedError

    async def _run_progress(self, step_id: str, progress_action: str, job):
        if self._progress_task is None:
            # Nicht eager: sonst kann der Job schon vor der done()-Pruefung fertig sein, und der
            # Schritt gaebe nie ein Fortschrittsergebnis zurueck.
            self._progress_task = self.hass.async_create_task(job(), eager_start=False)
        if not self._progress_task.done():
            return self.async_show_progress(
                step_id=step_id, progress_action=progress_action, progress_task=self._progress_task,
            )
        task, self._progress_task = self._progress_task, None
        try:
            next_step = task.result()
        except Exception:
            _LOGGER.exception("Unerwarteter Fehler im Fortschritts-Schritt %s", step_id)
            next_step = await self._progress_error_step()
        return self.async_show_progress_done(next_step_id=next_step)
