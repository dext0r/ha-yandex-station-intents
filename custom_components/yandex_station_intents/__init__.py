from __future__ import annotations

import logging
import re
from typing import Final

from homeassistant.components import media_player
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, SERVICE_RELOAD
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.reload import async_integration_yaml_config
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import (
    CONF_AUTOSYNC,
    CONF_INTENT_EXTRA_PHRASES,
    CONF_INTENT_SAY_PHRASE,
    CONF_INTENTS,
    CONF_MODE,
    DATA_EVENT_STREAM,
    DATA_INTENT_MANAGER,
    DATA_QUASAR,
    DOMAIN,
    INTENT_PLAYER_NAME,
    MODE_DEVICE,
    MODE_WEBSOCKET,
    NOTIFICATION_TITLE,
)
from .intent import Intent, IntentManager
from .yandex_quasar import EventStream, YandexQuasar
from .yandex_session import YandexSession

_LOGGER = logging.getLogger(__name__)

PLATFORMS: Final[list[str]] = [media_player.DOMAIN]


def intent_config_validate(intent_config):
    if intent_config is None:
        return {}
    elif isinstance(intent_config, str):
        return {CONF_INTENT_SAY_PHRASE: intent_config}

    return intent_config


def intent_name_validate(name: str) -> str:
    if not re.search(r'^[а-яё0-9 ]+$', name, re.IGNORECASE):
        _LOGGER.error(f'Недопустимая фраза {name!r}: разрешены только кириллица, цифры и пробелы')
        raise vol.Invalid('Разрешены только кириллица, цифры и пробелы')

    return name


CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Optional(CONF_INTENTS, default={}): vol.Schema({
            vol.All(cv.string, intent_name_validate): vol.All(intent_config_validate, vol.Schema({
                vol.Optional(CONF_INTENT_EXTRA_PHRASES): [vol.All(cv.string, intent_name_validate)],
                vol.Optional(CONF_INTENT_SAY_PHRASE): cv.string
            })),
        }),
        vol.Optional(CONF_MODE, default=MODE_WEBSOCKET): vol.In([MODE_WEBSOCKET, MODE_DEVICE]),
        vol.Optional(CONF_AUTOSYNC, default=True): cv.boolean
    }, extra=vol.ALLOW_EXTRA),
}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass: HomeAssistant, yaml_config: ConfigType):
    hass.data[DOMAIN] = {}

    async def _handle_reload(_: ServiceCall):
        # не поддерживается несколько аккаунтов, поэтому линейно
        for entry in hass.config_entries.async_entries(DOMAIN):
            config = await async_integration_yaml_config(hass, DOMAIN)
            _async_update_config_entry_from_yaml(hass, entry, config)
            await hass.config_entries.async_reload(entry.entry_id)

            if not entry.data[CONF_AUTOSYNC]:
                quasar = hass.data[DOMAIN][entry.entry_id][DATA_QUASAR]
                manager = hass.data[DOMAIN][entry.entry_id][DATA_INTENT_MANAGER]
                await _async_setup_intents(manager.intents, quasar)

    hass.helpers.service.async_register_admin_service(DOMAIN, SERVICE_RELOAD, _handle_reload)

    async def _clear_scenarios(_: ServiceCall):
        for entry in hass.config_entries.async_entries(DOMAIN):
            quasar = hass.data[DOMAIN][entry.entry_id][DATA_QUASAR]
            await quasar.clear_scenarios()

    hass.services.async_register(DOMAIN, 'clear_scenarios', _clear_scenarios)

    for config_entry in hass.config_entries.async_entries(DOMAIN):
        _async_update_config_entry_from_yaml(hass, config_entry, yaml_config)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    session = YandexSession(hass, entry)
    try:
        if not await session.refresh_cookies():
            hass.components.persistent_notification.async_create(
                'Необходимо заново авторизоваться в Яндексе. Для этого удалите интеграцию и [добавьте '
                'снова](/config/integrations).',
                title=NOTIFICATION_TITLE
            )
            return False

        manager = IntentManager(hass, entry)
        quasar = YandexQuasar(session)
        await quasar.async_init()
    except Exception as e:
        raise ConfigEntryNotReady(e)

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_QUASAR: quasar,
        DATA_INTENT_MANAGER: manager,
        DATA_EVENT_STREAM: None
    }

    if entry.data[CONF_MODE] == MODE_DEVICE:
        hass.config_entries.async_setup_platforms(entry, PLATFORMS)

        device_id = await quasar.async_get_intent_player_device_id()
        if not device_id:
            hass.components.persistent_notification.async_create(
                f'Служебный плеер **{INTENT_PLAYER_NAME}** не найден в УДЯ. Убедитесь, что он разрешён в фильтрах в '
                f'компоненте Yandex Smart Home, обновите список устройств в УДЯ и перезагрузите эту интеграцию.',
                title=NOTIFICATION_TITLE
            )
            return False

        hass.loop.create_task(_async_setup_intents(manager.intents, quasar, device_id))
    else:
        event_stream = EventStream(hass, session, quasar, manager)
        await event_stream.async_init()
        hass.data[DOMAIN][entry.entry_id][DATA_EVENT_STREAM] = event_stream

        hass.loop.create_task(event_stream.connect())
        entry.async_on_unload(
            hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP, event_stream.disconnect
            )
        )

        if entry.data[CONF_AUTOSYNC]:
            hass.loop.create_task(_async_setup_intents(manager.intents, quasar))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    event_stream = hass.data[DOMAIN][entry.entry_id][DATA_EVENT_STREAM]
    if event_stream:
        hass.async_create_task(event_stream.disconnect())

    if entry.data[CONF_MODE] == MODE_DEVICE:
        unload_ok = await hass.config_entries.async_unload_platforms(
            entry, PLATFORMS
        )

        if unload_ok:
            hass.data[DOMAIN].pop(entry.entry_id)

        return unload_ok

    return True


@callback
def _async_update_config_entry_from_yaml(hass: HomeAssistant, entry: ConfigEntry, yaml_config: ConfigType | None):
    data = entry.data.copy()

    if yaml_config and DOMAIN in yaml_config:
        data.update(yaml_config[DOMAIN])
    else:
        data.update({
            CONF_INTENTS: {},
            CONF_MODE: MODE_WEBSOCKET,
            CONF_AUTOSYNC: True
        })

    hass.config_entries.async_update_entry(entry, data=data)


# noinspection PyBroadException
async def _async_setup_intents(intents: list[Intent],
                               quasar: YandexQuasar,
                               target_device_id: str | None = None):
    await quasar.delete_stale_intents(intents)

    quasar_intents = await quasar.async_get_intents()

    for item in intents:
        try:
            await quasar.async_add_or_update_intent(
                intent=item,
                intent_quasar_id=quasar_intents.get(item.name),
                target_device_id=target_device_id
            )
        except Exception:
            _LOGGER.exception(f'Ошибка создания или обновления сценария {item.scenario_name!r}')
