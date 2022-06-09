from __future__ import annotations

from functools import lru_cache
import json
import logging

from homeassistant import data_entry_flow
from homeassistant.config_entries import ConfigFlow
from homeassistant.helpers.reload import async_integration_yaml_config
import voluptuous as vol

from . import DOMAIN, get_config_entry_data_from_yaml_config
from .const import CONF_X_TOKEN
from .yandex_session import AuthException, LoginResponse, YandexSession

_LOGGER = logging.getLogger(__name__)

METHOD_COOKIES = 'cookies'
METHOD_TOKEN = 'token'
METHOD_YANDEX_STATION = 'yandex_station'


class YandexSmartHomeIntentsFlowHandler(ConfigFlow, domain=DOMAIN):
    @property
    @lru_cache()
    def _session(self):
        return YandexSession(self.hass)

    async def async_step_user(self, user_input=None) -> data_entry_flow.FlowResult:
        if self._async_current_entries():
            return self.async_abort(reason='single_instance_allowed')

        if user_input is None:
            return self.async_show_form(
                step_id='user',
                data_schema=vol.Schema({
                    vol.Required('method', default=METHOD_YANDEX_STATION): vol.In({
                        METHOD_YANDEX_STATION: 'Через компонент Yandex.Station',
                        METHOD_COOKIES: 'Cookies',
                        METHOD_TOKEN: 'Токен'
                    })
                })
            )

        if user_input['method'] == METHOD_YANDEX_STATION:
            return await self.async_step_yandex_station()

        return await self._show_form(user_input['method'])

    async def async_step_yandex_station(self, user_input=None) -> data_entry_flow.FlowResult:
        entries = self.hass.config_entries.async_entries('yandex_station')
        if not entries:
            return self.async_abort(reason='install_yandex_station')

        if user_input:
            for entry in entries:
                if entry.entry_id == user_input['account']:
                    return await self.async_step_token({METHOD_TOKEN: entry.data[CONF_X_TOKEN]})

        entries = {
            entry.entry_id: entry.title
            for entry in entries
        }

        return self.async_show_form(
            step_id='yandex_station',
            data_schema=vol.Schema({
                vol.Required('account'): vol.In(entries),
            }),
        )

    async def async_step_cookies(self, user_input) -> data_entry_flow.FlowResult:
        try:
            cookies = {p['name']: p['value'] for p in json.loads(user_input[METHOD_COOKIES])}
        except (TypeError, KeyError, json.decoder.JSONDecodeError):
            return await self._show_form(METHOD_COOKIES, errors={'base': 'cookies.invalid_format'})

        try:
            response = await self._session.login_cookies(cookies)
        except AuthException as e:
            _LOGGER.error(f'Ошибка авторизации: {e}')
            return await self._show_form(METHOD_COOKIES, errors={'base': 'auth.error'})

        return await self._check_yandex_response(response, METHOD_COOKIES)

    async def async_step_token(self, user_input) -> data_entry_flow.FlowResult:
        response = await self._session.validate_token(user_input[METHOD_TOKEN])
        return await self._check_yandex_response(response, METHOD_TOKEN)

    async def _show_form(self, method: str, errors: dict[str, str] | None = None) -> data_entry_flow.FlowResult:
        return self.async_show_form(
            step_id=method,
            errors=errors,
            data_schema=vol.Schema({
                vol.Required(method): str,
            })
        )

    async def _check_yandex_response(self, response: LoginResponse, method: str) -> data_entry_flow.FlowResult:
        if response.ok:
            entry = await self.async_set_unique_id(response.display_login)
            if entry:
                self.hass.config_entries.async_update_entry(entry, data={
                    CONF_X_TOKEN: response.x_token
                })

                return self.async_abort(reason='account_updated')
            else:
                config = await async_integration_yaml_config(self.hass, DOMAIN)
                data = get_config_entry_data_from_yaml_config({
                    CONF_X_TOKEN: response.x_token
                }, config)

                return self.async_create_entry(title=response.display_login, data=data)

        elif response.error:
            _LOGGER.error(f'Ошибка авторизации: {response.error}')
            return await self._show_form(method, errors={'base': 'auth.error'})

        raise NotImplementedError
