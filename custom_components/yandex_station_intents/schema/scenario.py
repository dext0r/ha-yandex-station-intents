from collections.abc import Iterable
import dataclasses
from dataclasses import dataclass, field
from typing import Any, Literal, Self

from .scenario_step_actions import ScenarioStepActionDevice, ScenarioStepActionRequestedDeviceWithAssistant


@dataclass(kw_only=True)
class ScenarioVoiceTrigger:
    type: Literal["scenario.trigger.voice"] = "scenario.trigger.voice"
    value: str


@dataclass
class ScenarioStepParameters:
    items: list[ScenarioStepActionRequestedDeviceWithAssistant | ScenarioStepActionDevice | Any]


type ScenarioStepItem = ScenarioStepActionRequestedDeviceWithAssistant | ScenarioStepActionDevice


@dataclass(kw_only=True)
class ScenarioStep:
    type: Literal["scenarios.steps.actions.v2"] = "scenarios.steps.actions.v2"
    parameters: ScenarioStepParameters

    @classmethod
    def with_items(cls, items: list[ScenarioStepItem]) -> Self:
        return cls(parameters=ScenarioStepParameters(items=items))


@dataclass(kw_only=True)
class Scenario:
    id: str | None = field(default=None, compare=False)
    name: str
    icon: str = "home"
    triggers: list[ScenarioVoiceTrigger | dict[Any, Any]]
    steps: list[ScenarioStep | dict[Any, Any]]

    def as_dict(self) -> dict[str, Any]:
        def skip_none(obj: Iterable[tuple[str, Any]]) -> dict[str, str]:
            return {k: v for k, v in obj if v is not None}

        return dataclasses.asdict(self, dict_factory=skip_none)
