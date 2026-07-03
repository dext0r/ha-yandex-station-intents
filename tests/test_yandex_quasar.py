import json

from homeassistant.config import YAML_CONFIG_FILE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.reload import async_integration_yaml_config
from pytest_homeassistant_custom_component.common import MockConfigEntry, load_fixture, patch_yaml_files
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.yandex_station_intents.const import CONF_INTENTS, DOMAIN
from custom_components.yandex_station_intents.schema.scenario import (
    Scenario,
    ScenarioStep,
    ScenarioVoiceTrigger,
)
from custom_components.yandex_station_intents.schema.scenario_step_actions import (
    Device,
    DeviceRangeCapability,
    DeviceRangeCapabilityChannelInstance,
    PhraseAction,
    QuasarCapability,
    QuasarServerActionCapability,
    ScenarioStepActionDevice,
    ScenarioStepActionRequestedDeviceWithAssistant,
    TextAction,
    TTSAction,
    TTSActionParameters,
)
from custom_components.yandex_station_intents.yandex_intent import IntentManager
from custom_components.yandex_station_intents.yandex_quasar import (
    URL_USER,
    Device as QuasarDevice,
    YandexQuasar,
    get_scenario,
)
from custom_components.yandex_station_intents.yandex_session import YandexSession

INTENTS_YAML = """
yandex_station_intents:
  intents:
    Простой интент:
    Интент с ответом: Я вас услышала
    Кукушка:
      say_phrase: На курантах сейчас
      execute_command: Который час
    Альтернативы фразы:
      extra_phrases:
        - Фраза 1
        - Фраза 2
        - Фраза 3
"""


async def test_async_get_scenarios(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    scenarios_json = json.loads(load_fixture("scenarios.json"))
    aioclient_mock.get(f"{URL_USER}/scenarios", json={"status": "ok", "scenarios": scenarios_json})

    entry = MockConfigEntry(domain=DOMAIN, data={})
    quasar = YandexQuasar(YandexSession(hass, entry))

    scenarios = await quasar.async_get_scenarios()

    assert len(scenarios) == 6

    assert scenarios[0] == Scenario(
        name="--- Альтернативы фразы",
        icon="home",
        triggers=[
            ScenarioVoiceTrigger(value="Альтернативы фразы"),
            ScenarioVoiceTrigger(value="Фраза 1"),
            ScenarioVoiceTrigger(value="Фраза 2"),
            ScenarioVoiceTrigger(value="Фраза 3"),
        ],
        steps=[
            ScenarioStep.with_items(
                [
                    ScenarioStepActionRequestedDeviceWithAssistant(
                        value=QuasarServerActionCapability(state=TextAction(value="ничего не делай---,"))
                    )
                ]
            )
        ],
    )

    assert scenarios[1] == Scenario(
        name="--- Интент с ответом",
        icon="home",
        triggers=[ScenarioVoiceTrigger(value="Интент с ответом")],
        steps=[
            ScenarioStep.with_items(
                [
                    ScenarioStepActionRequestedDeviceWithAssistant(
                        value=QuasarServerActionCapability(state=PhraseAction(value="Я вас услышала---."))
                    )
                ]
            )
        ],
    )

    assert scenarios[2] == Scenario(
        name="--- Кукушка",
        icon="home",
        triggers=[ScenarioVoiceTrigger(value="Кукушка")],
        steps=[
            ScenarioStep.with_items(
                [
                    ScenarioStepActionRequestedDeviceWithAssistant(
                        value=QuasarCapability(state=TTSAction(value=TTSActionParameters(text="На курантах сейчас")))
                    )
                ]
            ),
            ScenarioStep.with_items(
                [
                    ScenarioStepActionRequestedDeviceWithAssistant(
                        value=QuasarServerActionCapability(state=TextAction(value="ничего не делай---:"))
                    )
                ]
            ),
        ],
    )

    assert scenarios[3] == Scenario(
        name="--- Простой интент",
        icon="home",
        triggers=[ScenarioVoiceTrigger(value="Простой интент")],
        steps=[
            ScenarioStep.with_items(
                [
                    ScenarioStepActionRequestedDeviceWithAssistant(
                        value=QuasarServerActionCapability(state=TextAction(value="ничего не делай---.,"))
                    )
                ]
            )
        ],
    )

    assert scenarios[4] == Scenario(
        name="Вечеринка",
        icon="home",
        triggers=[ScenarioVoiceTrigger(value="Вечеринка")],
        steps=[
            ScenarioStep.with_items(
                [
                    ScenarioStepActionDevice(
                        id="fec6624c-896c-420b-aa60-3049c313d15a",
                        value=Device(
                            id="fec6624c-896c-420b-aa60-3049c313d15a",
                            capabilities=[DeviceRangeCapability(state=DeviceRangeCapabilityChannelInstance(value=55))],
                        ),
                    )
                ]
            )
        ],
    )

    user_scenario = scenarios[5]
    assert len(user_scenario.steps) == 2

    step = user_scenario.steps[0]
    assert isinstance(step, ScenarioStep)
    assert step.type == "scenarios.steps.actions.v2"
    assert len(step.parameters.items) == 1
    assert isinstance(step.parameters.items[0], dict)

    step = user_scenario.steps[1]
    assert isinstance(step, dict)


async def test_get_scenario(hass: HomeAssistant) -> None:
    with patch_yaml_files({YAML_CONFIG_FILE: INTENTS_YAML}):
        config = await async_integration_yaml_config(hass, DOMAIN)

    assert config is not None
    intents_config = config[DOMAIN][CONF_INTENTS]

    entry = MockConfigEntry(domain=DOMAIN, data={})
    manager = IntentManager(hass, entry, intents_config)

    scenarios = {intent.name: get_scenario(intent, None).as_dict() for intent in manager.intents}

    assert scenarios["Альтернативы фразы"] == {
        "name": "--- Альтернативы фразы",
        "icon": "home",
        "triggers": [
            {"type": "scenario.trigger.voice", "value": "Альтернативы фразы"},
            {"type": "scenario.trigger.voice", "value": "Фраза 1"},
            {"type": "scenario.trigger.voice", "value": "Фраза 2"},
            {"type": "scenario.trigger.voice", "value": "Фраза 3"},
        ],
        "steps": [
            {
                "type": "scenarios.steps.actions.v2",
                "parameters": {
                    "items": [
                        {
                            "id": "requested-device",
                            "type": "step.action.item.requested_device_with_assistant",
                            "value": {
                                "type": "devices.capabilities.quasar.server_action",
                                "state": {"instance": "text_action", "value": "ничего не делай---,"},
                            },
                        }
                    ]
                },
            }
        ],
    }

    assert scenarios["Интент с ответом"] == {
        "name": "--- Интент с ответом",
        "icon": "home",
        "triggers": [{"type": "scenario.trigger.voice", "value": "Интент с ответом"}],
        "steps": [
            {
                "type": "scenarios.steps.actions.v2",
                "parameters": {
                    "items": [
                        {
                            "id": "requested-device",
                            "type": "step.action.item.requested_device_with_assistant",
                            "value": {
                                "type": "devices.capabilities.quasar.server_action",
                                "state": {"instance": "phrase_action", "value": "Я вас услышала---."},
                            },
                        }
                    ]
                },
            }
        ],
    }

    assert scenarios["Кукушка"] == {
        "name": "--- Кукушка",
        "icon": "home",
        "triggers": [{"type": "scenario.trigger.voice", "value": "Кукушка"}],
        "steps": [
            {
                "type": "scenarios.steps.actions.v2",
                "parameters": {
                    "items": [
                        {
                            "id": "requested-device",
                            "type": "step.action.item.requested_device_with_assistant",
                            "value": {
                                "type": "devices.capabilities.quasar",
                                "state": {"instance": "tts", "value": {"text": "На курантах сейчас"}},
                            },
                        }
                    ]
                },
            },
            {
                "type": "scenarios.steps.actions.v2",
                "parameters": {
                    "items": [
                        {
                            "id": "requested-device",
                            "type": "step.action.item.requested_device_with_assistant",
                            "value": {
                                "type": "devices.capabilities.quasar.server_action",
                                "state": {"instance": "text_action", "value": "ничего не делай---:"},
                            },
                        }
                    ]
                },
            },
        ],
    }

    assert scenarios["Простой интент"] == {
        "name": "--- Простой интент",
        "icon": "home",
        "triggers": [{"type": "scenario.trigger.voice", "value": "Простой интент"}],
        "steps": [
            {
                "type": "scenarios.steps.actions.v2",
                "parameters": {
                    "items": [
                        {
                            "id": "requested-device",
                            "type": "step.action.item.requested_device_with_assistant",
                            "value": {
                                "type": "devices.capabilities.quasar.server_action",
                                "state": {"instance": "text_action", "value": "ничего не делай---.,"},
                            },
                        }
                    ]
                },
            }
        ],
    }


async def test_get_scenario_with_player_device(hass: HomeAssistant) -> None:
    with patch_yaml_files({YAML_CONFIG_FILE: INTENTS_YAML}):
        config = await async_integration_yaml_config(hass, DOMAIN)

    assert config is not None
    intents_config = config[DOMAIN][CONF_INTENTS]

    entry = MockConfigEntry(domain=DOMAIN, data={})
    manager = IntentManager(hass, entry, intents_config)

    device = QuasarDevice(id="player-device-id", name="Интенты")
    scenarios = {intent.name: get_scenario(intent, device).as_dict() for intent in manager.intents}

    assert scenarios["Альтернативы фразы"] == {
        "name": "--- Альтернативы фразы",
        "icon": "home",
        "triggers": [
            {"type": "scenario.trigger.voice", "value": "Альтернативы фразы"},
            {"type": "scenario.trigger.voice", "value": "Фраза 1"},
            {"type": "scenario.trigger.voice", "value": "Фраза 2"},
            {"type": "scenario.trigger.voice", "value": "Фраза 3"},
        ],
        "steps": [
            {
                "type": "scenarios.steps.actions.v2",
                "parameters": {
                    "items": [
                        {
                            "id": "player-device-id",
                            "type": "step.action.item.device",
                            "value": {
                                "id": "player-device-id",
                                "item_type": "device",
                                "capabilities": [
                                    {
                                        "type": "devices.capabilities.range",
                                        "state": {"instance": "channel", "value": 0},
                                    }
                                ],
                            },
                        }
                    ]
                },
            }
        ],
    }

    assert scenarios["Интент с ответом"] == {
        "name": "--- Интент с ответом",
        "icon": "home",
        "triggers": [{"type": "scenario.trigger.voice", "value": "Интент с ответом"}],
        "steps": [
            {
                "type": "scenarios.steps.actions.v2",
                "parameters": {
                    "items": [
                        {
                            "id": "requested-device",
                            "type": "step.action.item.requested_device_with_assistant",
                            "value": {
                                "type": "devices.capabilities.quasar",
                                "state": {"instance": "tts", "value": {"text": "Я вас услышала"}},
                            },
                        },
                        {
                            "id": "player-device-id",
                            "type": "step.action.item.device",
                            "value": {
                                "id": "player-device-id",
                                "item_type": "device",
                                "capabilities": [
                                    {
                                        "type": "devices.capabilities.range",
                                        "state": {"instance": "channel", "value": 1},
                                    }
                                ],
                            },
                        },
                    ]
                },
            }
        ],
    }

    assert scenarios["Кукушка"] == {
        "name": "--- Кукушка",
        "icon": "home",
        "triggers": [{"type": "scenario.trigger.voice", "value": "Кукушка"}],
        "steps": [
            {
                "type": "scenarios.steps.actions.v2",
                "parameters": {
                    "items": [
                        {
                            "id": "requested-device",
                            "type": "step.action.item.requested_device_with_assistant",
                            "value": {
                                "type": "devices.capabilities.quasar",
                                "state": {"instance": "tts", "value": {"text": "На курантах сейчас"}},
                            },
                        },
                        {
                            "id": "player-device-id",
                            "type": "step.action.item.device",
                            "value": {
                                "id": "player-device-id",
                                "item_type": "device",
                                "capabilities": [
                                    {
                                        "type": "devices.capabilities.range",
                                        "state": {"instance": "channel", "value": 2},
                                    }
                                ],
                            },
                        },
                    ]
                },
            }
        ],
    }

    assert scenarios["Простой интент"] == {
        "name": "--- Простой интент",
        "icon": "home",
        "triggers": [{"type": "scenario.trigger.voice", "value": "Простой интент"}],
        "steps": [
            {
                "type": "scenarios.steps.actions.v2",
                "parameters": {
                    "items": [
                        {
                            "id": "player-device-id",
                            "type": "step.action.item.device",
                            "value": {
                                "id": "player-device-id",
                                "item_type": "device",
                                "capabilities": [
                                    {
                                        "type": "devices.capabilities.range",
                                        "state": {"instance": "channel", "value": 3},
                                    }
                                ],
                            },
                        }
                    ]
                },
            }
        ],
    }
