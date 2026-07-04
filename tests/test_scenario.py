from typing import Any

from custom_components.yandex_station_intents.schema.scenario import (
    Scenario,
    ScenarioStep,
    ScenarioVoiceTrigger,
)
from custom_components.yandex_station_intents.schema.scenario_step_actions import (
    ScenarioStepActionRequestedDeviceWithAssistant,
)


def _scenario(
    *,
    triggers: list[ScenarioVoiceTrigger | dict[str, Any]] | None = None,
    steps: list[ScenarioStep | dict[str, Any]] | None = None,
) -> Scenario:
    if triggers is None:
        triggers = [ScenarioVoiceTrigger(value="Фраза")]
    if steps is None:
        steps = [ScenarioStep.with_items([ScenarioStepActionRequestedDeviceWithAssistant.text_action("команда")])]

    return Scenario(name="Интент", triggers=triggers, steps=steps)


def test_equal_scenarios() -> None:
    assert _scenario() == _scenario()


def test_different_triggers_count() -> None:
    left = _scenario(triggers=[ScenarioVoiceTrigger(value="Фраза 1")])
    right = _scenario(
        triggers=[
            ScenarioVoiceTrigger(value="Фраза 1"),
            ScenarioVoiceTrigger(value="Фраза 2"),
        ]
    )
    assert left != right


def test_different_trigger_value() -> None:
    left = _scenario(triggers=[ScenarioVoiceTrigger(value="Фраза 1")])
    right = _scenario(triggers=[ScenarioVoiceTrigger(value="Фраза 2")])
    assert left != right


def test_equal_multiple_triggers() -> None:
    triggers = [
        ScenarioVoiceTrigger(value="Фраза 1"),
        ScenarioVoiceTrigger(value="Фраза 2"),
    ]
    assert _scenario(triggers=list(triggers)) == _scenario(triggers=list(triggers))


def test_triggers_order_matters() -> None:
    left = _scenario(
        triggers=[
            ScenarioVoiceTrigger(value="Фраза 1"),
            ScenarioVoiceTrigger(value="Фраза 2"),
        ]
    )
    right = _scenario(
        triggers=[
            ScenarioVoiceTrigger(value="Фраза 2"),
            ScenarioVoiceTrigger(value="Фраза 1"),
        ]
    )
    assert left != right


def test_different_steps_count() -> None:
    step = ScenarioStep.with_items([ScenarioStepActionRequestedDeviceWithAssistant.text_action("команда")])
    left = _scenario(steps=[step])
    right = _scenario(steps=[step, step])
    assert left != right


def test_different_step_value() -> None:
    left = _scenario(steps=[ScenarioStep.with_items([ScenarioStepActionRequestedDeviceWithAssistant.tts("foo")])])
    right = _scenario(steps=[ScenarioStep.with_items([ScenarioStepActionRequestedDeviceWithAssistant.tts("bar")])])
    assert left != right


def test_same_value_different_action_type() -> None:
    left = _scenario(
        steps=[ScenarioStep.with_items([ScenarioStepActionRequestedDeviceWithAssistant.text_action("текст")])]
    )
    right = _scenario(steps=[ScenarioStep.with_items([ScenarioStepActionRequestedDeviceWithAssistant.tts("текст")])])
    assert left != right


def test_equal_multiple_steps() -> None:
    def steps() -> list[ScenarioStep]:
        return [
            ScenarioStep.with_items([ScenarioStepActionRequestedDeviceWithAssistant.text_action("команда 1")]),
            ScenarioStep.with_items([ScenarioStepActionRequestedDeviceWithAssistant.tts("текст")]),
        ]

    assert _scenario(steps=list(steps())) == _scenario(steps=list(steps()))


def test_steps_order_matters() -> None:
    text_step = ScenarioStep.with_items([ScenarioStepActionRequestedDeviceWithAssistant.text_action("команда")])
    tts_step = ScenarioStep.with_items([ScenarioStepActionRequestedDeviceWithAssistant.tts("текст")])
    left = _scenario(steps=[text_step, tts_step])
    right = _scenario(steps=[tts_step, text_step])
    assert left != right


def test_voice_trigger_not_equal_to_arbitrary_dict() -> None:
    left = _scenario(triggers=[ScenarioVoiceTrigger(value="Фраза")])
    right = _scenario(triggers=[{"foo": "bar"}])
    assert left != right


def test_step_not_equal_to_arbitrary_dict() -> None:
    left = _scenario(steps=[ScenarioStep.with_items([ScenarioStepActionRequestedDeviceWithAssistant.tts("текст")])])
    right = _scenario(steps=[{"foo": "bar"}])
    assert left != right
