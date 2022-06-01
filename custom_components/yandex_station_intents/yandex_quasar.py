from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any

from aiohttp import ClientConnectorError, ClientResponseError, ClientWebSocketResponse, WSMessage, WSMsgType
from homeassistant.components import media_player
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity_registry import EntityRegistry
from homeassistant.helpers.event import async_call_later

from .const import INTENT_ID_MARKER, INTENT_PLAYER_NAME, STATION_STUB_COMMAND
from .intent import Intent, IntentManager
from .yandex_session import YandexSession

_LOGGER = logging.getLogger(__name__)

URL_USER = 'https://iot.quasar.yandex.ru/m/user'
DEFAULT_RECONNECTION_DELAY = 2
MAX_RECONNECTION_DELAY = 180


@dataclass
class Device:
    id: str
    name: str
    room: str | None = None
    yandex_station_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> Device:
        kw = {'id': data['id'], 'name': data['name'], 'room': data.get('room')}

        if 'quasar_info' in data:
            kw['yandex_station_id'] = data['quasar_info'].get('device_id')

        return Device(**kw)


class YandexQuasar:
    def __init__(self, session: YandexSession):
        self._session = session

        self.devices: list[Device] = []

    async def async_init(self):
        _LOGGER.debug('Получение списка устройств')

        r = await self._session.get(f'{URL_USER}/devices')
        resp = await r.json()
        assert resp['status'] == 'ok', resp

        for dev in resp['unconfigured_devices']:
            self.devices.append(Device.from_dict(dev))

        for room in resp['rooms']:
            room_name = room['name']

            for device in room['devices']:
                if device['type'].startswith('devices.types.smart_speaker') or \
                        device['type'].endswith('yandex.module'):
                    device['room'] = room_name
                    self.devices.append(Device.from_dict(device))

    async def async_get_intent_player_device_id(self) -> None | str:
        for device in self.devices:
            if device.name == INTENT_PLAYER_NAME:
                return device.id

        return None

    async def async_get_intents(self) -> dict[str, str]:
        """Получает список интенетов, которые управляются компонентом."""
        _LOGGER.debug('Получение списка интентов')

        r = await self._session.get(f'{URL_USER}/scenarios')
        resp = await r.json()
        assert resp['status'] == 'ok', resp

        rv = {}
        for scenario in resp['scenarios']:
            if INTENT_ID_MARKER not in scenario['name']:
                continue

            rv[scenario['name'].replace(f'{INTENT_ID_MARKER}', '').strip()] = scenario['id']

        return rv

    async def async_add_or_update_intent(self,
                                         intent: Intent,
                                         intent_quasar_id: str | None,
                                         target_device_id: str):
        devices = []

        if intent.say_phrase:
            speaker_caps = [{
                'type': 'devices.capabilities.quasar.server_action',
                'state': {
                    'instance': 'phrase_action',
                    'value': intent.as_phrase
                }
            }]
        else:
            speaker_caps = [{
                'type': 'devices.capabilities.quasar.server_action',
                'state': {
                    'instance': 'text_action',
                    'value': intent.as_phrase
                },
                'parameters': {
                    'instance': 'text_action'
                }
            }]

        if target_device_id:
            devices = [{
                'id': target_device_id,
                'capabilities': [{
                    'type': 'devices.capabilities.range',
                    'state': {
                        'instance': 'channel',
                        'relative': False,
                        'value': intent.id
                    }
                }]
            }]

            if intent.say_phrase:
                speaker_caps = [{
                    'type': 'devices.capabilities.quasar.server_action',
                    'state': {
                        'instance': 'phrase_action',
                        'value': intent.say_phrase
                    }
                }]
            else:
                speaker_caps = [{
                    'type': 'devices.capabilities.quasar.server_action',
                    'state': {
                        'instance': 'text_action',
                        'value': STATION_STUB_COMMAND
                    }
                }]

        payload = {
            'name': intent.scenario_name,
            'icon': 'home',
            'triggers': [{
                'type': 'scenario.trigger.voice',
                'value': v
            } for v in intent.trigger_phrases],
            'steps': [{
                'type': 'scenarios.steps.actions',
                'parameters': {
                    'requested_speaker_capabilities': speaker_caps,
                    'launch_devices': devices
                }
            }]
        }

        if intent_quasar_id:
            _LOGGER.debug(f'Обновление сценария {intent.scenario_name!r}')
            r = await self._session.put(f'{URL_USER}/scenarios/{intent_quasar_id}', json=payload)
        else:
            _LOGGER.debug(f'Создание сценария {intent.scenario_name!r}')
            r = await self._session.post(f'{URL_USER}/scenarios', json=payload)

        resp = await r.json()
        assert resp['status'] == 'ok', resp

    async def delete_stale_intents(self, active_intents: list[Intent]):
        quasar_intents = await self.async_get_intents()
        for intent_name, intent_id in quasar_intents.items():
            if intent_name not in [i.name for i in active_intents]:
                # noinspection PyBroadException
                try:
                    _LOGGER.debug(f'Удаление сценария {intent_name!r}')
                    r = await self._session.delete(f'{URL_USER}/scenarios/{intent_id}')
                    resp = await r.json()
                    assert resp['status'] == 'ok', resp
                except Exception:
                    _LOGGER.exception(f'Ошибка удаления сценария {intent_name!r}')

    async def clear_scenarios(self):
        r = await self._session.get(f'{URL_USER}/scenarios')
        resp = await r.json()
        assert resp['status'] == 'ok', resp

        for scenario in resp['scenarios']:
            scenario_id = scenario['id']
            scenario_name = scenario['name']
            # noinspection PyBroadException
            try:
                _LOGGER.debug(f'Удаление сценария {scenario_name!r}')
                r = await self._session.delete(f'{URL_USER}/scenarios/{scenario_id}')
                resp = await r.json()
                assert resp['status'] == 'ok', resp
            except Exception:
                _LOGGER.exception(f'Ошибка удаления сценария {scenario_name!r}')


class EventStream:
    def __init__(self,
                 hass: HomeAssistant,
                 session: YandexSession,
                 quasar: YandexQuasar,
                 intent_manager: IntentManager):
        self._hass = hass
        self._session = session
        self._quasar = quasar
        self._manager = intent_manager
        self._entity_registry: EntityRegistry | None = None

        self._ws: ClientWebSocketResponse | None = None
        self._ws_reconnect_delay = DEFAULT_RECONNECTION_DELAY
        self._ws_active = True

    async def async_init(self):
        self._entity_registry = entity_registry.async_get(self._hass)

    async def connect(self, *_):
        if not self._ws_active:
            return

        # noinspection PyBroadException
        try:
            r = await self._session.get('https://iot.quasar.yandex.ru/m/v3/user/devices')
            resp = await r.json()
            assert resp['status'] == 'ok', resp

            url = resp['updates_url']

            _LOGGER.debug('Подключение к %s' % url.split('?')[0])
            self._ws = await self._session.ws_connect(url, heartbeat=45)

            _LOGGER.debug('Подключение к УДЯ установлено')
            self._ws_reconnect_delay = DEFAULT_RECONNECTION_DELAY

            async for msg in self._ws:  # type: WSMessage
                if msg.type == WSMsgType.TEXT:
                    # noinspection PyBroadException
                    try:
                        await self._on_message(msg.json())
                    except Exception as e:
                        _LOGGER.exception(f'Неожиданное событие: {msg!r} ({e!r})')

            _LOGGER.debug(f'Отключено: {self._ws.close_code}')
            if self._ws.close_code is not None:
                self._try_reconnect()
        except (ClientConnectorError, ClientResponseError, TimeoutError):
            _LOGGER.exception('Ошибка подключения к УДЯ')
            self._try_reconnect()
        except Exception:
            _LOGGER.exception('Неожиданное исключение')
            self._try_reconnect()

    async def disconnect(self, *_):
        self._ws_active = False

        if self._ws:
            await self._ws.close()

    def _try_reconnect(self):
        self._ws_reconnect_delay = min(2 * self._ws_reconnect_delay, MAX_RECONNECTION_DELAY)
        _LOGGER.debug(f'Переподключение через {self._ws_reconnect_delay} сек.')
        async_call_later(self._hass, self._ws_reconnect_delay, self.connect)

    async def _on_message(self, payload: dict[Any, Any]):
        if payload.get('operation') != 'update_states':
            return

        message = json.loads(payload['message'])
        for dev in message.get('updated_devices', []):
            if not dev.get('capabilities'):
                continue

            for cap in dev['capabilities']:
                if cap['type'] != 'devices.capabilities.quasar.server_action':
                    continue

                cap_state = cap.get('state')
                if not cap_state:
                    continue

                if cap_state['instance'] in ['text_action', 'phrase_action'] and \
                        INTENT_ID_MARKER in cap_state['value']:
                    for device in self._quasar.devices:
                        if device.id != dev['id'] or not device.yandex_station_id:
                            continue

                        phrase = cap_state['value']
                        entity_id = self._entity_registry.async_get_entity_id(
                            media_player.DOMAIN,
                            'yandex_station',
                            device.yandex_station_id
                        )
                        if not entity_id:
                            _LOGGER.debug(f'Ignoring intent {phrase!r}, speaker {device.yandex_station_id} not found')
                            continue

                        event_data = {
                            ATTR_ENTITY_ID: entity_id
                        }
                        if device.room:
                            event_data['room'] = device.room

                        self._manager.event_from_phrase(phrase, event_data)
