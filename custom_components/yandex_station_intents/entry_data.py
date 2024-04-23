from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.helpers.typing import ConfigType

from custom_components.yandex_station_intents import CONF_AUTOSYNC
from custom_components.yandex_station_intents.const import CONF_MODE, ConnectionMode

if TYPE_CHECKING:
    from custom_components.yandex_station_intents import EventStream, IntentManager, YandexQuasar


@dataclass
class ConfigEntryData:
    yaml_config: ConfigType
    quasar: YandexQuasar
    intent_manager: IntentManager
    event_stream: EventStream | None = None

    @property
    def autosync(self) -> bool:
        return bool(self.yaml_config.get(CONF_AUTOSYNC, True))

    @property
    def connection_mode(self) -> ConnectionMode:
        return ConnectionMode(self.yaml_config.get(CONF_MODE, ConnectionMode.WEBSOCKET))
