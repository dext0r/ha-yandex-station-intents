from collections.abc import Awaitable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import Component
from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    component: Component = hass.data[DOMAIN]
    entry_data = component.entry_datas[entry.entry_id]

    async def _safe(awaitable: Awaitable[Any]) -> Any:
        try:
            return await awaitable
        except Exception as e:
            return e

    return {
        "yaml_config": component.yaml_config,
        "devices": entry_data.quasar.devices,
        "scenarios": await _safe(entry_data.quasar.async_get_scenarios()),
        "intents": entry_data.intent_manager.intents,
    }
