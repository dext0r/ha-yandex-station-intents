from dataclasses import dataclass
from typing import Literal, Self


@dataclass(kw_only=True)
class TextAction:
    instance: Literal["text_action"] = "text_action"
    value: str


@dataclass(kw_only=True)
class PhraseAction:
    instance: Literal["phrase_action"] = "phrase_action"
    value: str


@dataclass(kw_only=True)
class QuasarServerActionCapability:
    type: Literal["devices.capabilities.quasar.server_action"] = "devices.capabilities.quasar.server_action"
    state: TextAction | PhraseAction


@dataclass
class TTSActionParameters:
    text: str


@dataclass(kw_only=True)
class TTSAction:
    instance: Literal["tts"] = "tts"
    value: TTSActionParameters


@dataclass(kw_only=True)
class QuasarCapability:
    type: Literal["devices.capabilities.quasar"] = "devices.capabilities.quasar"
    state: TTSAction


@dataclass(kw_only=True)
class ScenarioStepActionRequestedDeviceWithAssistant:
    """
    Действия из раздела "Любое умное устройство, которое активирует сценарий"
    """

    id: Literal["requested-device"] = "requested-device"
    type: Literal["step.action.item.requested_device_with_assistant"] = (
        "step.action.item.requested_device_with_assistant"
    )
    value: QuasarCapability | QuasarServerActionCapability

    @classmethod
    def text_action(cls, command: str) -> Self:
        """
        Выполняет команду на колонке.
        В интерфейсе: "Ответить на вопрос или выполнить команду"
        """
        return cls(value=QuasarServerActionCapability(state=TextAction(value=command)))

    @classmethod
    def tts(cls, text: str) -> Self:
        """
        Проговаривает текст на колонке, действие не попадает в список событий.
        В интерфейсе: "Прочитать текст вслух"
        """
        return cls(value=QuasarCapability(state=TTSAction(value=TTSActionParameters(text))))

    @classmethod
    def tts_pa(cls, text: str) -> Self:
        """
        Проговаривает текст на колонке, действие попадает в список событий.
        В интерфейсе: отсутствует
        """
        return cls(value=QuasarServerActionCapability(state=PhraseAction(value=text)))


@dataclass(kw_only=True)
class DeviceRangeCapabilityChannelInstance:
    instance: Literal["channel"] = "channel"
    value: int


@dataclass(kw_only=True)
class DeviceRangeCapability:
    type: Literal["devices.capabilities.range"] = "devices.capabilities.range"
    state: DeviceRangeCapabilityChannelInstance


@dataclass(kw_only=True)
class Device:
    id: str
    item_type: Literal["device"] = "device"
    capabilities: list[DeviceRangeCapability]


@dataclass(kw_only=True)
class ScenarioStepActionDevice:
    """Действие над устройством."""

    id: str
    type: Literal["step.action.item.device"] = "step.action.item.device"
    value: Device

    @classmethod
    def set_channel(cls, device_id: str, channel: int) -> Self:
        return cls(
            id=device_id,
            value=Device(
                id=device_id,
                capabilities=[
                    DeviceRangeCapability(state=DeviceRangeCapabilityChannelInstance(value=channel)),
                ],
            ),
        )
