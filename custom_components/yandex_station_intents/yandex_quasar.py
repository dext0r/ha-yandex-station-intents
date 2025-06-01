from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
import logging
from typing import Any, AsyncIterable, cast

from aiohttp import ClientConnectorError, ClientResponseError, ClientWebSocketResponse, WSMessage, WSMsgType
from homeassistant.components import media_player
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HassJob, HomeAssistant
from homeassistant.helpers import entity_registry
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, INTENT_ID_MARKER, YANDEX_STATION_DOMAIN
from .yandex_intent import Intent, IntentManager
from .yandex_session import YandexSession

_LOGGER = logging.getLogger(__name__)

URL_USER = "https://iot.quasar.yandex.ru/m/user"
URL_V3_USER = "https://iot.quasar.yandex.ru/m/v3/user"
URL_V4_USER = "https://iot.quasar.yandex.ru/m/v4/user"
DEFAULT_RECONNECTION_DELAY = 2
MAX_RECONNECTION_DELAY = 180


@dataclass
class Device:
    id: str
    name: str
    room: str | None = None
    entity_id: str | None = None
    yandex_station_id: str | None = None

    @classmethod
    def from_dict(cls, data: ConfigType) -> Device:
        return Device(
            id=data["id"],
            name=data["name"],
            room=data.get("room_name"),
            entity_id=data.get("parameters", {}).get("device_info", {}).get("model"),
            yandex_station_id=data.get("quasar_info", {}).get("device_id"),
        )


class ScenarioStepItem(ABC):
    """
    Действие в сценариях.
    """

    @property
    @abstractmethod
    def as_dict(self) -> ConfigType:
        pass


class ScenarioStepItemDeviceChannel(ScenarioStepItem):
    """
    Действие "Канал X" для служебного плеера.
    """

    def __init__(self, device: Device, channel: int):
        self._device = device
        self._channel = channel

    @property
    def as_dict(self) -> ConfigType:
        return {
            "id": self._device.id,
            "type": "step.action.item.device",
            "value": {
                "id": self._device.id,
                "capabilities": [
                    {
                        "type": "devices.capabilities.range",
                        "state": {
                            "instance": "channel",
                            "value": self._channel,
                        },
                    }
                ],
            },
        }


class ScenarioStepItemRequestedDevice(ScenarioStepItem, ABC):
    """
    Действия из раздела "Любое умное устройство, которое активирует сценарий"
    """

    @property
    def as_dict(self) -> ConfigType:
        return {
            "id": "requested-device",
            "type": "step.action.item.requested_device_with_assistant",
            "value": self._value,
        }

    @property
    @abstractmethod
    def _value(self) -> ConfigType:
        pass


class ScenarioStepItemRequestedDeviceTTS(ScenarioStepItemRequestedDevice):
    """
    Проговаривает текст на колонке, действие не попадает в список событий.
    В интерфейсе: "Прочитать текст вслух"
    """

    def __init__(self, text: str):
        self._text = text

    @property
    def _value(self) -> ConfigType:
        return {
            "type": "devices.capabilities.quasar",
            "state": {
                "instance": "tts",
                "value": {"text": self._text},
            },
        }


class ScenarioStepItemRequestedDeviceTTSPA(ScenarioStepItemRequestedDevice):
    """
    Проговаривает текст на колонке, действие попадает в список событий.
    В интерфейсе: отсутствует
    """

    def __init__(self, text: str):
        self._text = text

    @property
    def _value(self) -> ConfigType:
        return {
            "type": "devices.capabilities.quasar.server_action",
            "state": {
                "instance": "phrase_action",
                "value": self._text,
            },
        }


class ScenarioStepItemRequestedDeviceTextAction(ScenarioStepItemRequestedDevice):
    """
    Выполняет команду на колонке.
    В интерфейсе: "Ответить на вопрос или выполнить команду"
    """

    def __init__(self, command: str):
        self._command = command

    @property
    def _value(self) -> ConfigType:
        return {
            "type": "devices.capabilities.quasar.server_action",
            "state": {
                "instance": "text_action",
                "value": self._command,
            },
        }


class YandexQuasar:
    def __init__(self, session: YandexSession) -> None:
        self._session = session

        self.devices: list[Device] = []
        self.running = True

    async def async_init(self) -> None:
        _LOGGER.debug("Получение списка устройств")

        r = await self._session.get(f"{URL_V3_USER}/devices")
        resp = await r.json()
        assert resp["status"] == "ok", resp

        for house in resp["households"]:
            if "sharing_info" in house:
                continue

            for device_config in house["all"]:
                if self._is_supported_device(device_config):
                    self.devices.append(Device.from_dict(device_config))

    async def async_get_scenarios(self) -> list[dict[str, Any]]:
        r = await self._session.get(f"{URL_USER}/scenarios")
        resp = await r.json()
        assert resp["status"] == "ok", resp
        assert isinstance(resp["scenarios"], list)

        return resp["scenarios"]

    async def async_get_intents(self) -> dict[str, str]:
        """Получает список интентов, которые управляются компонентом."""
        _LOGGER.debug("Получение списка интентов")

        rv = {}
        for scenario in await self.async_get_scenarios():
            if INTENT_ID_MARKER not in scenario["name"]:
                continue

            rv[scenario["name"].replace(f"{INTENT_ID_MARKER}", "").strip()] = scenario["id"]

        return rv

    async def async_add_or_update_intent(
        self, intent: Intent, intent_quasar_id: str | None, intent_player_device: Device | None
    ) -> None:
        step_items: list[ScenarioStepItem] = []

        if intent_player_device:
            if intent.say_phrase:
                step_items.append(ScenarioStepItemRequestedDeviceTTS(intent.say_phrase))
            step_items.append(ScenarioStepItemDeviceChannel(intent_player_device, intent.id))
        else:
            if intent.say_phrase and intent.execute_command:
                step_items.append(ScenarioStepItemRequestedDeviceTTS(intent.say_phrase))
                step_items.append(ScenarioStepItemRequestedDeviceTextAction(intent.scenario_text_command))
            elif intent.say_phrase:
                step_items.append(ScenarioStepItemRequestedDeviceTTSPA(intent.scenario_text_command))
            else:
                step_items.append(ScenarioStepItemRequestedDeviceTextAction(intent.scenario_text_command))

        payload = {
            "name": intent.scenario_name,
            "icon": "home",
            "triggers": [{"type": "scenario.trigger.voice", "value": v} for v in intent.trigger_phrases],
            "steps": [
                {
                    "type": "scenarios.steps.actions.v2",
                    "parameters": {"items": [si.as_dict for si in step_items]},
                }
            ],
        }

        if intent_quasar_id:
            _LOGGER.debug(f"Обновление сценария {intent.scenario_name!r}: {payload}")
            r = await self._session.put(f"{URL_V4_USER}/scenarios/{intent_quasar_id}", json=payload)
        else:
            _LOGGER.debug(f"Создание сценария {intent.scenario_name!r}: {payload}")
            r = await self._session.post(f"{URL_V4_USER}/scenarios", json=payload)

        resp = await r.json()
        assert resp["status"] == "ok", resp

    def get_intent_player_device(self, entity_id: str) -> Device | None:
        for device in self.devices:
            if device.entity_id == entity_id:
                return device

        return None

    async def delete_stale_intents(self, active_intents: list[Intent]) -> None:
        quasar_intents = await self.async_get_intents()
        for intent_name, intent_id in quasar_intents.items():
            if intent_name not in [i.name for i in active_intents]:
                try:
                    _LOGGER.debug(f"Удаление сценария {intent_name!r}")
                    r = await self._session.delete(f"{URL_USER}/scenarios/{intent_id}")
                    resp = await r.json()
                    assert resp["status"] == "ok", resp
                except Exception:
                    _LOGGER.exception(f"Ошибка удаления сценария {intent_name!r}")

    async def clear_scenarios(self) -> None:
        r = await self._session.get(f"{URL_USER}/scenarios")
        resp = await r.json()
        assert resp["status"] == "ok", resp

        for scenario in resp["scenarios"]:
            if not self.running:
                break
            scenario_id = scenario["id"]
            scenario_name = scenario["name"]
            try:
                _LOGGER.debug(f"Удаление сценария {scenario_name!r}")
                r = await self._session.delete(f"{URL_USER}/scenarios/{scenario_id}")
                resp = await r.json()
                assert resp["status"] == "ok", resp
            except Exception:
                _LOGGER.exception(f"Ошибка удаления сценария {scenario_name!r}")

    def stop(self) -> None:
        self.running = False

    @staticmethod
    def _is_supported_device(device: ConfigType) -> bool:
        device_type = device.get("type", "")

        # devices.types.smart_speaker.yandex.station.mini_2
        # devices.types.smart_speaker.yandex.station_2
        if device_type.startswith("devices.types.smart_speaker"):
            return True

        # devices.types.media_device.tv.yandex.magritte
        if device_type.startswith("devices.types.media_device.tv.yandex"):
            return True

        # devices.types.media_device.dongle.yandex.module_2
        if "dongle.yandex.module" in device_type:
            return True

        # Служебный плеер для mode=device
        if DOMAIN in device.get("parameters", {}).get("device_info", {}).get("model", ""):
            return True

        return False


class EventStream:
    def __init__(
        self, hass: HomeAssistant, session: YandexSession, quasar: YandexQuasar, intent_manager: IntentManager
    ) -> None:
        self._hass = hass
        self._session = session
        self._quasar = quasar
        self._manager = intent_manager
        self._entity_registry = entity_registry.async_get(self._hass)

        self._ws: ClientWebSocketResponse | None = None
        self._ws_reconnect_delay = DEFAULT_RECONNECTION_DELAY
        self._ws_active = True

    async def connect(self, *_: Any) -> None:
        if not self._ws_active:
            return

        try:
            r = await self._session.get(f"{URL_V3_USER}/devices")
            resp = await r.json()
            assert resp["status"] == "ok", resp

            url = resp["updates_url"]

            _LOGGER.debug("Подключение к %s" % url.split("?")[0])
            self._ws = await self._session.ws_connect(url, heartbeat=45)

            _LOGGER.debug("Подключение к УДЯ установлено")
            self._ws_reconnect_delay = DEFAULT_RECONNECTION_DELAY

            async for msg in cast(AsyncIterable[WSMessage], self._ws):
                if msg.type == WSMsgType.TEXT:
                    try:
                        await self._on_message(msg.json())
                    except Exception as e:
                        _LOGGER.exception(f"Неожиданное событие: {msg!r} ({e!r})")

            _LOGGER.debug(f"Отключено: {self._ws.close_code}")
            if self._ws.close_code is not None:
                self._try_reconnect()
        except (ClientConnectorError, ClientResponseError, TimeoutError):
            _LOGGER.exception("Ошибка подключения к УДЯ")
            self._try_reconnect()
        except Exception:
            _LOGGER.exception("Неожиданное исключение")
            self._try_reconnect()

    async def disconnect(self, *_: Any) -> None:
        self._ws_active = False

        if self._ws:
            await self._ws.close()

    def _try_reconnect(self) -> None:
        self._ws_reconnect_delay = min(2 * self._ws_reconnect_delay, MAX_RECONNECTION_DELAY)
        _LOGGER.debug(f"Переподключение через {self._ws_reconnect_delay} сек.")
        async_call_later(self._hass, self._ws_reconnect_delay, HassJob(self.connect))

    async def _on_message(self, payload: dict[Any, Any]) -> None:
        if payload.get("operation") != "update_states":
            return

        message = json.loads(payload["message"])
        for dev in message.get("updated_devices", []):
            if not dev.get("capabilities"):
                continue

            for cap in dev["capabilities"]:
                if cap["type"] != "devices.capabilities.quasar.server_action":
                    continue

                cap_state = cap.get("state")
                if not cap_state:
                    continue

                if cap_state["instance"] in ["text_action", "phrase_action"] and INTENT_ID_MARKER in cap_state["value"]:
                    _LOGGER.debug(f"Интент обнаружен в событии: {dev!r}")

                    yandex_station_entity_id: str | None = None
                    event_data: ConfigType = {}

                    for device in self._quasar.devices:
                        if device.id != dev["id"] or not device.yandex_station_id:
                            continue

                        if device.room:
                            event_data["room"] = device.room

                        yandex_station_entity_id = self._entity_registry.async_get_entity_id(
                            media_player.DOMAIN, YANDEX_STATION_DOMAIN, device.yandex_station_id
                        )
                        if yandex_station_entity_id:
                            event_data[ATTR_ENTITY_ID] = yandex_station_entity_id

                    await self._manager.async_handle_phrase(cap_state["value"], event_data, yandex_station_entity_id)
