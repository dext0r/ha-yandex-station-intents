from enum import StrEnum

DOMAIN = "yandex_station_intents"
YANDEX_STATION_DOMAIN = "yandex_station"

CONF_ACCOUNTS = "accounts"
CONF_INTENTS = "intents"
CONF_INTENT_EXTRA_PHRASES = "extra_phrases"
CONF_INTENT_SAY_PHRASE = "say_phrase"
CONF_INTENT_EXECUTE_COMMAND = "execute_command"
CONF_MODE = "mode"
CONF_AUTOSYNC = "autosync"
CONF_X_TOKEN = "x_token"
CONF_COOKIE = "cookie"
CONF_UID = "uid"

INTENT_PLAYER_NAME_PREFIX = "Интенты"
INTENT_ID_MARKER = "---"
STATION_STUB_COMMAND = "Сделай громкость"

CLEAR_CONFIRM_KEY = "confirm"
CLEAR_CONFIRM_TEXT = "я действительно хочу удалить все сценарии из удя"

EVENT_NAME = "yandex_intent"


class ConnectionMode(StrEnum):
    WEBSOCKET = "websocket"
    DEVICE = "device"
