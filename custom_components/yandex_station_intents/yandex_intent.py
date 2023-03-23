from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_INTENT_EXTRA_PHRASES,
    CONF_INTENT_SAY_PHRASE,
    CONF_INTENTS,
    EVENT_NAME,
    INTENT_ID_MARKER,
    STATION_STUB_COMMAND,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class Intent:
    id: int
    name: str
    trigger_phrases: list[str]
    say_phrase: str | None = None

    @property
    def scenario_name(self):
        return f'{INTENT_ID_MARKER} {self.name}'

    @property
    def as_phrase(self) -> str:
        rv = STATION_STUB_COMMAND
        if self.say_phrase:
            rv = self.say_phrase

        rv += INTENT_ID_MARKER
        rv += BaseConverter.encode(self.id)

        if len(rv) > 100:
            raise ValueError(f'Слишком длинная произносимая фраза: {rv!r}')

        return rv


class BaseConverter:
    _base_chars = ',.:'
    _digits = '01234567890'

    @classmethod
    def _convert(cls, number: int | str, from_digits: str, to_digits: str):
        x = 0
        for digit in str(number):
            x = x * len(from_digits) + from_digits.index(digit)

        if x == 0:
            rv = to_digits[0]
        else:
            rv = ''
            while x > 0:
                digit = x % len(to_digits)
                rv = to_digits[digit] + rv
                x = int(x // len(to_digits))

        return rv

    @classmethod
    def encode(cls, number: int) -> str:
        return cls._convert(number, cls._digits, cls._base_chars)

    @classmethod
    def decode(cls, number: str) -> int:
        return int(cls._convert(number, cls._base_chars, cls._digits))


class IntentManager:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self._hass = hass

        self.intents: list[Intent] = []

        for idx, (name, config) in enumerate(entry.data[CONF_INTENTS].items(), 0):
            intent = Intent(
                id=idx,
                name=name,
                say_phrase=config.get(CONF_INTENT_SAY_PHRASE),
                trigger_phrases=[name] + config.get(CONF_INTENT_EXTRA_PHRASES, []),
            )
            self.intents.append(intent)

    def event_from_id(self, intent_id: int):
        if intent_id < len(self.intents):
            text = self.intents[intent_id].name
            _LOGGER.debug(f'Получена команда: {text}')
            self._hass.bus.async_fire(EVENT_NAME, {'text': text})

    def event_from_phrase(self, phrase: str, event_data: dict):
        intent = self._intent_from_phrase(phrase)
        if intent:
            event_data['text'] = intent.name
            _LOGGER.debug(f'Получена команда: {event_data!r}')
            self._hass.bus.async_fire(EVENT_NAME, event_data)

    def _intent_from_phrase(self, phrase: str) -> Intent | None:
        if INTENT_ID_MARKER not in phrase:
            return None

        intent_id = BaseConverter.decode(phrase.split(INTENT_ID_MARKER, 1)[1])
        if intent_id < len(self.intents):
            return self.intents[intent_id]
