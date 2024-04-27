from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

from homeassistant.components import media_player
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import InvalidStateError, ServiceNotFound
from homeassistant.helpers.template import Template
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt

from .const import (
    CONF_INTENT_EXECUTE_COMMAND,
    CONF_INTENT_EXTRA_PHRASES,
    CONF_INTENT_SAY_PHRASE,
    EVENT_NAME,
    INTENT_ID_MARKER,
    STATION_STUB_COMMAND,
)

_LOGGER = logging.getLogger(__name__)

COMMAND_EXECUTION_LOOP_THRESHOLD = 4
COMMAND_EXECUTION_LOOP_WINDOW = timedelta(seconds=3)


@dataclass
class Intent:
    id: int
    name: str
    trigger_phrases: list[str]
    say_phrase: str | None = None
    say_phrase_template: Template | None = None
    execute_command: Template | None = None

    @property
    def scenario_name(self) -> str:
        return f"{INTENT_ID_MARKER} {self.name}"

    @property
    def scenario_step_value(self) -> str:
        rv = STATION_STUB_COMMAND
        if self.say_phrase and not self.execute_command:
            rv = self.say_phrase

        rv += INTENT_ID_MARKER
        rv += BaseConverter.encode(self.id)

        if len(rv) > 100:
            raise ValueError(f"Слишком длинная произносимая фраза: {rv!r}")

        return rv


class BaseConverter:
    _base_chars = ",.:"
    _digits = "01234567890"

    @classmethod
    def _convert(cls, number: int | str, from_digits: str, to_digits: str) -> str | int:
        x = 0
        for digit in str(number):
            x = x * len(from_digits) + from_digits.index(digit)

        if x == 0:
            rv = to_digits[0]
        else:
            rv = ""
            while x > 0:
                rv = to_digits[x % len(to_digits)] + rv
                x = int(x // len(to_digits))

        return rv

    @classmethod
    def encode(cls, number: int) -> str:
        return str(cls._convert(number, cls._digits, cls._base_chars))

    @classmethod
    def decode(cls, number: str) -> int:
        return int(cls._convert(number, cls._base_chars, cls._digits))


class IntentManager:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, intents_config: ConfigType) -> None:
        self._hass = hass
        self._entry = entry
        self._last_command_at: datetime | None = None
        self._command_execution_loop_count: int = 0

        self.intents: list[Intent] = []

        for idx, name in enumerate(sorted(intents_config.keys(), key=lambda k: k.lower()), 0):
            config = intents_config[name]
            say_phrase = config.get(CONF_INTENT_SAY_PHRASE)
            intent = Intent(
                id=idx,
                name=name,
                say_phrase=say_phrase if not isinstance(say_phrase, Template) else None,
                say_phrase_template=say_phrase if isinstance(say_phrase, Template) else None,
                trigger_phrases=[name] + config.get(CONF_INTENT_EXTRA_PHRASES, []),
                execute_command=config.get(CONF_INTENT_EXECUTE_COMMAND),
            )
            self.intents.append(intent)

    def event_from_id(self, intent_id: int) -> None:
        if intent_id < len(self.intents):
            text = self.intents[intent_id].name
            _LOGGER.debug(f"Получена команда: {text}")
            self._hass.bus.async_fire(EVENT_NAME, {"text": text, "account": self._entry.unique_id})

    async def async_handle_phrase(
        self, phrase: str, event_data: ConfigType, yandex_station_entity_id: str | None
    ) -> None:
        intent = self._intent_from_phrase(phrase)
        if intent:
            event_data.update({"text": intent.name, "account": self._entry.unique_id})
            _LOGGER.debug(f"Получена команда: {event_data!r}")
            self._hass.bus.async_fire(EVENT_NAME, event_data)

            try:
                if not yandex_station_entity_id:
                    raise InvalidStateError

                if intent.execute_command:
                    await self._execute_command(intent, event_data, yandex_station_entity_id)

                if intent.say_phrase_template:
                    await self._tts(intent, event_data, yandex_station_entity_id)
            except (ServiceNotFound, InvalidStateError):
                _LOGGER.warning(
                    f"В Home Assistant не найдена колонка для события {phrase!r}. "
                    f"Интеграция Yandex.Station установлена и настроена?"
                )
        else:
            _LOGGER.warning(f"Не найден интент для события {phrase}")

    async def _execute_command(self, intent: Intent, event_data: ConfigType, yandex_station_entity_id: str) -> None:
        assert intent.execute_command

        if self._detect_command_loop():
            return

        intent.execute_command.hass = self._hass

        await self._hass.services.async_call(
            media_player.DOMAIN,
            media_player.SERVICE_PLAY_MEDIA,
            {
                ATTR_ENTITY_ID: yandex_station_entity_id,
                media_player.ATTR_MEDIA_CONTENT_TYPE: "command",
                media_player.ATTR_MEDIA_CONTENT_ID: intent.execute_command.async_render(
                    variables={"event": event_data}
                ),
            },
        )

    async def _tts(self, intent: Intent, event_data: ConfigType, yandex_station_entity_id: str) -> None:
        assert intent.say_phrase_template

        intent.say_phrase_template.hass = self._hass

        await self._hass.services.async_call(
            media_player.DOMAIN,
            media_player.SERVICE_PLAY_MEDIA,
            {
                ATTR_ENTITY_ID: yandex_station_entity_id,
                media_player.ATTR_MEDIA_CONTENT_TYPE: "text",
                media_player.ATTR_MEDIA_CONTENT_ID: intent.say_phrase_template.async_render(
                    variables={"event": event_data}
                ),
            },
        )

    def _detect_command_loop(self) -> bool:
        if self._last_command_at and self._last_command_at + COMMAND_EXECUTION_LOOP_WINDOW > dt.now():
            self._command_execution_loop_count += 1
            if self._command_execution_loop_count >= COMMAND_EXECUTION_LOOP_THRESHOLD:
                _LOGGER.error(
                    "Обнаружена частая отправка команд на колонку. "
                    "Похоже, что исполняемая команда совпадает с одной из активационных фраз."
                )
                return True
        else:
            self._command_execution_loop_count = 0

        self._last_command_at = dt.now()
        return False

    def _intent_from_phrase(self, phrase: str) -> Intent | None:
        if INTENT_ID_MARKER not in phrase:
            return None

        intent_id = BaseConverter.decode(phrase.split(INTENT_ID_MARKER, 1)[1])
        if intent_id < len(self.intents):
            return self.intents[intent_id]

        return None
