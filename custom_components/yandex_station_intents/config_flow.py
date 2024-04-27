from enum import StrEnum
from functools import lru_cache
import json
import logging

from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from . import DOMAIN, YandexSession
from .const import CONF_UID, CONF_X_TOKEN, YANDEX_STATION_DOMAIN

_LOGGER = logging.getLogger(__name__)


class AuthMethod(StrEnum):
    COOKIES = "cookies"
    TOKEN = "token"
    YANDEX_STATION = "yandex_station"


class YandexSmartHomeIntentsFlowHandler(ConfigFlow, domain=DOMAIN):
    @property
    @lru_cache()
    def _session(self) -> YandexSession:
        return YandexSession(self.hass)

    async def async_step_user(self, user_input: ConfigType | None = None) -> FlowResult:  # type: ignore
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required("method", default=AuthMethod.YANDEX_STATION): vol.In(
                            {
                                AuthMethod.YANDEX_STATION: "Через компонент Yandex.Station",
                                AuthMethod.COOKIES: "Cookies",
                                AuthMethod.TOKEN: "Токен",
                            }
                        )
                    }
                ),
            )

        if user_input["method"] == AuthMethod.YANDEX_STATION:
            return await self.async_step_yandex_station()

        return await self._show_form(user_input["method"])

    async def async_step_yandex_station(self, user_input: ConfigType | None = None) -> FlowResult:
        entries = self.hass.config_entries.async_entries(YANDEX_STATION_DOMAIN)
        if not entries:
            return self.async_abort(reason="install_yandex_station")

        if user_input:
            for entry in entries:
                if entry.entry_id == user_input["account"]:
                    return await self.async_step_token({AuthMethod.TOKEN: entry.data[CONF_X_TOKEN]})

        accounts = {entry.entry_id: entry.title for entry in entries}

        return self.async_show_form(
            step_id="yandex_station",
            data_schema=vol.Schema(
                {
                    vol.Required("account"): vol.In(accounts),
                }
            ),
        )

    async def async_step_cookies(self, user_input: ConfigType) -> FlowResult:
        try:
            raw = json.loads(user_input[AuthMethod.COOKIES])
            host = next(p["domain"] for p in raw if p["domain"].startswith(".yandex."))
            cookies = {p["name"]: p["value"] for p in raw}
        except (StopIteration, TypeError, KeyError, json.decoder.JSONDecodeError):
            return await self._show_form(str(AuthMethod.COOKIES), error_code="cookies.invalid_format")

        try:
            x_token = await self._session.async_get_x_token(host, cookies)
        except Exception as e:
            return await self._show_form(str(AuthMethod.COOKIES), error_code="auth.error", error_description=str(e))

        return await self.async_step_token({AuthMethod.TOKEN: x_token})

    async def async_step_token(self, user_input: ConfigType) -> FlowResult:
        x_token = user_input[AuthMethod.TOKEN]

        try:
            account = await self._session.async_get_account_info(x_token)
        except Exception as e:
            return await self._show_form(str(AuthMethod.TOKEN), error_code="auth.error", error_description=str(e))

        await self.async_set_unique_id(account.display_login)
        return self.async_create_entry(title=account.display_login, data={CONF_X_TOKEN: x_token, CONF_UID: account.uid})

    async def _show_form(
        self, step_id: str, error_code: str | None = None, error_description: str | None = None
    ) -> FlowResult:
        errors = {}
        if error_code:
            errors["base"] = error_code

        return self.async_show_form(
            step_id=step_id,
            errors=errors,
            description_placeholders={"error_description": error_description},
            data_schema=vol.Schema({vol.Required(step_id): str}),
        )
