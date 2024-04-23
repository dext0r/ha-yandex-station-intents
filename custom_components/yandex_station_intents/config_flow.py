from enum import StrEnum
from functools import lru_cache
import json
import logging

from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from . import DOMAIN
from .const import CONF_X_TOKEN, YANDEX_STATION_DOMAIN
from .yandex_session import AuthException, LoginResponse, YandexSession

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
        except (TypeError, KeyError, json.decoder.JSONDecodeError):
            return await self._show_form(AuthMethod.COOKIES, errors={"base": "cookies.invalid_format"})

        try:
            response = await self._session.login_cookies(host, cookies)
        except AuthException as e:
            _LOGGER.error(f"Ошибка авторизации: {e}")
            return await self._show_form(AuthMethod.COOKIES, errors={"base": "auth.error"})

        return await self._check_yandex_response(response, AuthMethod.COOKIES)

    async def async_step_token(self, user_input: ConfigType) -> FlowResult:
        response = await self._session.validate_token(user_input[AuthMethod.TOKEN])
        return await self._check_yandex_response(response, AuthMethod.TOKEN)

    async def _show_form(self, method: AuthMethod, errors: dict[str, str] | None = None) -> FlowResult:
        return self.async_show_form(
            step_id=str(method),
            errors=errors,
            data_schema=vol.Schema({vol.Required(str(method)): str}),
        )

    async def _check_yandex_response(self, response: LoginResponse, method: AuthMethod) -> FlowResult:
        if response.ok:
            await self.async_set_unique_id(response.display_login)
            return self.async_create_entry(title=response.display_login, data={CONF_X_TOKEN: response.x_token})

        elif response.error:
            _LOGGER.error(f"Ошибка авторизации: {response.error}")
            return await self._show_form(method, errors={"base": "auth.error"})

        raise NotImplementedError
