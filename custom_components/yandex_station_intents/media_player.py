import logging
from typing import Any

from homeassistant.components.media_player import MediaPlayerDeviceClass, MediaPlayerEntity
from homeassistant.components.media_player.const import MediaPlayerEntityFeature, MediaPlayerState
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import Component, ConfigEntryData
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    component: Component = hass.data[DOMAIN]
    async_add_entities([YandexStationIntentMediaPlayer(component.entry_datas[entry.entry_id])])


class YandexStationIntentMediaPlayer(MediaPlayerEntity):
    def __init__(self, entry_data: ConfigEntryData):
        self.entity_id = entry_data.media_player_entity_id

        self._entry_data = entry_data

    @property
    def name(self) -> str:
        return self._entry_data.media_player_name

    @property
    def state(self) -> MediaPlayerState | None:
        return MediaPlayerState.OFF

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        return (
            MediaPlayerEntityFeature.TURN_ON | MediaPlayerEntityFeature.TURN_OFF | MediaPlayerEntityFeature.PLAY_MEDIA
        )

    @property
    def device_class(self) -> MediaPlayerDeviceClass | None:
        return MediaPlayerDeviceClass.TV

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        self._entry_data.intent_manager.event_from_id(int(media_id))

    def turn_on(self) -> None:
        pass

    def turn_off(self) -> None:
        pass
