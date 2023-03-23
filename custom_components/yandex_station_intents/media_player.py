from __future__ import annotations

import logging
from typing import Optional

from homeassistant.components.media_player import SUPPORT_PLAY_MEDIA, MediaPlayerDeviceClass, MediaPlayerEntity
from homeassistant.components.media_player.const import SUPPORT_TURN_OFF, SUPPORT_TURN_ON
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_INTENT_MANAGER, DOMAIN, INTENT_PLAYER_NAME
from .yandex_intent import IntentManager

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    manager: IntentManager = hass.data[DOMAIN][entry.entry_id][DATA_INTENT_MANAGER]

    async_add_entities([YandexStationIntentMediaPlayer(manager)])


# noinspection PyAbstractClass
class YandexStationIntentMediaPlayer(MediaPlayerEntity):
    def __init__(self, manager: IntentManager):
        self._manager = manager

    @property
    def name(self):
        return INTENT_PLAYER_NAME

    @property
    def state(self) -> str | None:
        return STATE_OFF

    @property
    def supported_features(self):
        return SUPPORT_TURN_ON | SUPPORT_TURN_OFF | SUPPORT_PLAY_MEDIA

    @property
    def device_class(self) -> Optional[str]:
        return MediaPlayerDeviceClass.TV

    async def async_play_media(self, media_type: str, media_id: str, **kwargs):
        self._manager.event_from_id(int(media_id))

    def turn_on(self):
        pass

    def turn_off(self):
        pass
