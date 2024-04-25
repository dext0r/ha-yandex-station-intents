from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import Component
from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    component: Component = hass.data[DOMAIN]
    entry_data = component.entry_datas[entry.entry_id]

    try:
        scenarios: Any = await entry_data.quasar.async_get_scenarios()
    except Exception as e:
        scenarios = e

    return {
        "yaml_config": component.yaml_config,
        "devices": entry_data.quasar.devices,
        "scenarios": scenarios,
        "intents": entry_data.intent_manager.intents,
    }
