from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components import media_player
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.typing import ConfigType

from custom_components.yandex_station_intents import CONF_AUTOSYNC
from custom_components.yandex_station_intents.const import (
    CONF_MODE,
    CONF_UID,
    DOMAIN,
    INTENT_PLAYER_NAME_PREFIX,
    ConnectionMode,
)

if TYPE_CHECKING:
    from custom_components.yandex_station_intents import EventStream, IntentManager, YandexQuasar


@dataclass
class ConfigEntryData:
    entry: ConfigEntry
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

    @property
    def media_player_entity_id(self) -> str:
        return f"{media_player.DOMAIN}.{DOMAIN}_{self.entry.data[CONF_UID]}"

    @property
    def media_player_name(self) -> str:
        return f"{INTENT_PLAYER_NAME_PREFIX} {self.entry.data[CONF_UID]}"
