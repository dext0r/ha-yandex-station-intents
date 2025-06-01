import asyncio
from dataclasses import dataclass
import logging
import re
from typing import Final

from homeassistant.components import media_player
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, SERVICE_RELOAD
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv, issue_registry as ir, template as template_helper
from homeassistant.helpers.reload import async_integration_yaml_config
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import (
    CLEAR_CONFIRM_KEY,
    CLEAR_CONFIRM_TEXT,
    CONF_ACCOUNTS,
    CONF_AUTOSYNC,
    CONF_INTENT_EXECUTE_COMMAND,
    CONF_INTENT_EXTRA_PHRASES,
    CONF_INTENT_SAY_PHRASE,
    CONF_INTENTS,
    CONF_MODE,
    CONF_UID,
    DOMAIN,
    ConnectionMode,
)
from .entry_data import ConfigEntryData
from .yandex_intent import Intent, IntentManager
from .yandex_quasar import Device, EventStream, YandexQuasar
from .yandex_session import AuthException, YandexSession

_LOGGER = logging.getLogger(__name__)

PLATFORMS: Final[list[str]] = [media_player.DOMAIN]
ISSUE_ID_MISSING_INTENT_PLAYER = "missing_intent_player"


def intents_config_validate(intents_config: ConfigType) -> ConfigType:
    names = set(map(lambda s: s.lower(), intents_config.keys()))
    execute_commands = set(
        [
            c[CONF_INTENT_EXECUTE_COMMAND].template.lower()
            for c in intents_config.values()
            if CONF_INTENT_EXECUTE_COMMAND in c
        ]
    )

    forbidden_phrases = execute_commands & names
    if forbidden_phrases:
        raise vol.Invalid(f"Недопустимо использовать команды в активационных фразах: {forbidden_phrases}")

    for name, intent_config in intents_config.items():
        if (
            isinstance(intent_config.get(CONF_INTENT_SAY_PHRASE), template_helper.Template)
            and CONF_INTENT_EXECUTE_COMMAND in intent_config
        ):
            raise vol.Invalid(f"Недопустимо совместное использование execute_command и шаблонной say_phrase в {name!r}")

    return intents_config


def intent_item_validate(intent_item: str | ConfigType | None) -> ConfigType:
    if intent_item is None:
        return {}
    elif isinstance(intent_item, str):
        return {CONF_INTENT_SAY_PHRASE: intent_item}

    return intent_item


def intent_name_validate(name: str) -> str:
    if not re.search(r"^[а-яё0-9 ]+$", name, re.IGNORECASE):
        _LOGGER.error(f"Недопустимая фраза {name!r}: разрешены только кириллица, цифры и пробелы")
        raise vol.Invalid("Разрешены только кириллица, цифры и пробелы")

    return name


def string_or_template(value: str) -> str | template_helper.Template:
    value = cv.string(value)
    if template_helper.is_template_string(value):
        return cv.template(value)

    return value


CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_INTENTS, default={}): vol.Schema(
                    vol.All(
                        {
                            vol.All(cv.string, intent_name_validate): vol.All(
                                intent_item_validate,
                                vol.Schema(
                                    {
                                        vol.Optional(CONF_INTENT_EXTRA_PHRASES): [
                                            vol.All(cv.string, intent_name_validate)
                                        ],
                                        vol.Optional(CONF_INTENT_SAY_PHRASE): string_or_template,
                                        vol.Optional(CONF_INTENT_EXECUTE_COMMAND): cv.template,
                                        vol.Optional(CONF_ACCOUNTS): vol.All(cv.ensure_list, [cv.string]),
                                    }
                                ),
                            ),
                        },
                        intents_config_validate,
                    )
                ),
                vol.Optional(CONF_MODE, default=ConnectionMode.WEBSOCKET): vol.In(
                    [ConnectionMode.WEBSOCKET, ConnectionMode.DEVICE]
                ),
                vol.Optional(CONF_AUTOSYNC, default=True): cv.boolean,
            },
            extra=vol.ALLOW_EXTRA,
        ),
    },
    extra=vol.ALLOW_EXTRA,
)


@dataclass
class Component:
    yaml_config: ConfigType
    entry_datas: dict[str, ConfigEntryData]

    def get_intents_config(self, entry: ConfigEntry) -> ConfigType:
        intents_config: ConfigType = {}
        for name, config in self.yaml_config.get(CONF_INTENTS, {}).items():
            if accounts := config.get(CONF_ACCOUNTS):
                if entry.unique_id not in accounts:
                    continue

            intents_config[name] = config

        return intents_config


async def async_setup(hass: HomeAssistant, yaml_config: ConfigType) -> bool:
    hass.data[DOMAIN] = component = Component(yaml_config.get(DOMAIN, {}), {})

    async def _handle_reload(_: ServiceCall) -> None:
        component.yaml_config = (await async_integration_yaml_config(hass, DOMAIN) or {}).get(DOMAIN, {})

        for entry in hass.config_entries.async_entries(DOMAIN):
            await hass.config_entries.async_reload(entry.entry_id)

        await asyncio.gather(
            *(
                _async_setup_intents(
                    entry_data.intent_manager.intents,
                    entry_data.quasar,
                    entry_data.quasar.get_intent_player_device(entry_data.media_player_entity_id),
                )
                for entry_data in component.entry_datas.values()
                if not entry_data.autosync
            ),
            return_exceptions=True,
        )

    async_register_admin_service(hass, DOMAIN, SERVICE_RELOAD, _handle_reload)

    async def _clear_scenarios(service: ServiceCall) -> None:
        if service.data.get(CLEAR_CONFIRM_KEY, "").lower() != CLEAR_CONFIRM_TEXT:
            raise HomeAssistantError("Необходимо подтверждение, ознакомьтесь с документацией")

        for entry_data in component.entry_datas.values():
            await entry_data.quasar.clear_scenarios()

    hass.services.async_register(DOMAIN, "clear_scenarios", _clear_scenarios)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    component: Component = hass.data[DOMAIN]
    session = YandexSession(hass, entry)
    try:
        if not await session.async_validate() or CONF_UID not in entry.data:
            await session.async_refresh()

        manager = IntentManager(hass, entry, component.get_intents_config(entry))
        quasar = YandexQuasar(session)
        await quasar.async_init()
    except Exception as e:
        raise ConfigEntryNotReady(e)

    entry_data = ConfigEntryData(entry, yaml_config=component.yaml_config, quasar=quasar, intent_manager=manager)
    component.entry_datas[entry.entry_id] = entry_data
    intent_player_device = quasar.get_intent_player_device(entry_data.media_player_entity_id)

    if entry_data.connection_mode == ConnectionMode.DEVICE:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        if not intent_player_device:
            ir.async_create_issue(
                hass,
                DOMAIN,
                f"{ISSUE_ID_MISSING_INTENT_PLAYER}_{entry.entry_id}",
                is_fixable=False,
                severity=ir.IssueSeverity.CRITICAL,
                translation_key=ISSUE_ID_MISSING_INTENT_PLAYER,
                translation_placeholders={
                    "entity": entry.title,
                    "player_entity_id": entry_data.media_player_entity_id,
                    "player_name": entry_data.media_player_name,
                },
            )
            _LOGGER.error(f"В УДЯ не найден служебный плеер {entry_data.media_player_name}")
            return False

        ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_ID_MISSING_INTENT_PLAYER}_{entry.entry_id}")
    else:
        event_stream = EventStream(hass, session, quasar, manager)
        component.entry_datas[entry.entry_id].event_stream = event_stream
        hass.loop.create_task(event_stream.connect())
        entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, event_stream.disconnect))

    if entry_data.autosync:
        hass.loop.create_task(_async_setup_intents(manager.intents, quasar, intent_player_device))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    component: Component = hass.data[DOMAIN]
    entry_data = component.entry_datas[entry.entry_id]
    entry_data.quasar.stop()

    if entry_data.event_stream:
        hass.async_create_task(entry_data.event_stream.disconnect())

    if entry_data.connection_mode == ConnectionMode.DEVICE:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

        if unload_ok:
            component.entry_datas.pop(entry.entry_id)

        return unload_ok

    return True


async def _async_setup_intents(
    intents: list[Intent], quasar: YandexQuasar, intent_player_device: Device | None = None
) -> None:
    await quasar.delete_stale_intents(intents)

    quasar_intents = await quasar.async_get_intents()

    for item in intents:
        if not quasar.running:
            break

        try:
            await quasar.async_add_or_update_intent(
                intent=item,
                intent_quasar_id=quasar_intents.get(item.name),
                intent_player_device=intent_player_device,
            )
        except AuthException:
            _LOGGER.exception(
                f"Ошибка создания или обновления сценария {item.scenario_name!r}, синхронизация остановлена"
            )
            break
        except Exception:
            _LOGGER.exception(f"Ошибка создания или обновления сценария {item.scenario_name!r}")
