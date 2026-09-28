"""Optionen ohne Login (Spec TP7 2.2); vollstaendig in Task 16 des TP7-Plans."""
from __future__ import annotations

from homeassistant.config_entries import OptionsFlow

# Warte-Budget auf das Status-Event; Tests setzen es herunter (flow_helpers.fast_status_wait).
from .const import STATUS_WAIT_SECONDS  # noqa: F401


class SmartHeatOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input=None):
        return self.async_abort(reason="setup_incomplete")
