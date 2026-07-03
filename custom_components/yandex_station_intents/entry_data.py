from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components import media_player
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.typing import ConfigType

from . import CONF_AUTOSYNC
from .const import CONF_MODE, CONF_UID, DOMAIN, INTENT_PLAYER_NAME_PREFIX, ConnectionMode

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from . import EventStream, IntentManager, YandexQuasar


@dataclass
class ConfigEntryData:
    entry: ConfigEntry
    yaml_config: ConfigType
    quasar: YandexQuasar
    intent_manager: IntentManager
    event_stream: EventStream | None = None

    _task: asyncio.Task[Any] | None = None

    @property
    def autosync(self) -> bool:
        return bool(self.yaml_config.get(CONF_AUTOSYNC, True))

    @property
    def connection_mode(self) -> ConnectionMode:
        return ConnectionMode(self.yaml_config.get(CONF_MODE, ConnectionMode.WEBSOCKET))

    @property
    def media_player_entity_id(self) -> str:
        return f"{media_player.DOMAIN}.{DOMAIN}_{self.entry.data[CONF_UID]}"

    @property
    def media_player_name(self) -> str:
        return f"{INTENT_PLAYER_NAME_PREFIX} {self.entry.data[CONF_UID]}"

    async def async_run_or_replace_task(self, target: Coroutine[Any, Any, None]) -> None:
        """Запускает фоновую задачу, отменяя предыдущую, если она ещё выполняется."""
        await self.async_stop_task()

        async def _run_task() -> None:
            try:
                await target
            finally:
                self._task = None

        self._task = asyncio.create_task(_run_task())

    async def async_stop_task(self) -> None:
        """Отменяет текущую задачу и дожидается её завершения."""
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
