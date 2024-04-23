import base64
import logging
import pickle
import re
from typing import Any, cast

from aiohttp import ClientResponse, ClientWebSocketResponse, CookieJar
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import CONF_COOKIE, CONF_X_TOKEN

_LOGGER = logging.getLogger(__name__)

RE_CSRF = re.compile('"csrfToken2":"(.+?)"')


class AuthException(Exception):
    pass


class LoginResponse:
    """
    status: ok
       uid: 1234567890
       display_name: John
       public_name: John
       firstname: John
       lastname: McClane
       gender: m
       display_login: j0hn.mcclane
       normalized_display_login: j0hn-mcclane
       native_default_email: j0hn.mcclane@yandex.ru
       avatar_url: XXX
       is_avatar_empty: True
       public_id: XXX
       access_token: XXX
       cloud_token: XXX
       x_token: XXX
       x_token_issued_at: 1607490000
       access_token_expires_in: 24650000
       x_token_expires_in: 24650000
    status: error
       errors: [captcha.required]
       captcha_image_url: XXX
    status: error
       errors: [account.not_found]
       errors: [password.not_matched]
    """

    def __init__(self, resp: dict[str, Any]) -> None:
        self.raw = resp

    @property
    def ok(self) -> bool:
        return bool(self.raw["status"] == "ok")

    @property
    def error(self) -> str:
        return str(self.raw["errors"][0])

    @property
    def display_login(self) -> str:
        return str(self.raw["display_login"])

    @property
    def x_token(self) -> str:
        return str(self.raw["x_token"])


class YandexSession:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry | None = None) -> None:
        self._hass = hass
        self._session = async_create_clientsession(hass)
        self._entry = entry
        self._x_token: str | None = None
        self._csrf_token: str | None = None

        if self._entry:
            self._x_token = self._entry.data.get(CONF_X_TOKEN)

            cookie = self._entry.data.get(CONF_COOKIE)
            if cookie:
                raw = base64.b64decode(cookie)
                cast(CookieJar, self._session.cookie_jar)._cookies = pickle.loads(raw)

    async def login_cookies(self, host: str, cookies: dict[str, str]) -> LoginResponse:
        r = await self._session.post(
            "https://mobileproxy.passport.yandex.net/1/bundle/oauth/token_by_sessionid",
            data={
                "client_id": "c0ebe342af7d48fbbbfcf2d2eedb8f9e",
                "client_secret": "ad0a908f0aa341a182a37ecd75bc319e",
            },
            headers={
                "Ya-Client-Host": host,
                "Ya-Client-Cookie": "; ".join([f"{k}={v}" for k, v in cookies.items()]),
            },
        )
        resp = await r.json()
        if "error" in resp:
            raise AuthException(resp.get("error_description"))
        if "access_token" not in resp:
            raise AuthException("Отсутствует access_token")

        x_token = resp["access_token"]

        return await self.validate_token(x_token)

    async def validate_token(self, x_token: str) -> LoginResponse:
        headers = {"Authorization": f"OAuth {x_token}"}
        r = await self._session.get(
            "https://mobileproxy.passport.yandex.net/1/bundle/account/short_info/?avatar_size=islands-300",
            headers=headers,
        )
        resp = await r.json()
        resp["x_token"] = x_token

        return LoginResponse(resp)

    async def login_token(self, x_token: str) -> bool:
        _LOGGER.debug("Авторизация в Яндекс с помощью токена")

        payload = {"type": "x-token", "retpath": "https://www.yandex.ru"}
        headers = {"Ya-Consumer-Authorization": f"OAuth {x_token}"}
        r = await self._session.post(
            "https://mobileproxy.passport.yandex.net/1/bundle/auth/x_token/", data=payload, headers=headers
        )
        resp = await r.json()
        if resp["status"] != "ok":
            _LOGGER.error(f"Ошибка авторизации: {resp}")
            return False

        host = resp["passport_host"]
        payload = {"track_id": resp["track_id"]}
        r = await self._session.get(f"{host}/auth/session/", params=payload, allow_redirects=False)
        assert r.status == 302, await r.read()

        return True

    async def refresh_cookies(self) -> bool:
        r = await self._session.get("https://quasar.yandex.ru/get_account_config")
        resp = await r.json()
        if resp["status"] == "ok":
            return True

        if not self._x_token:
            return False

        ok = await self.login_token(self._x_token)
        if ok and self._entry:
            data = self._entry.data.copy()
            data[CONF_COOKIE] = self._session_cookie
            self._hass.config_entries.async_update_entry(self._entry, data=data)

        return ok

    async def get(self, url: str, **kwargs: Any) -> ClientResponse:
        return await self._request("get", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> ClientResponse:
        return await self._request("post", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> ClientResponse:
        return await self._request("put", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> ClientResponse:
        return await self._request("delete", url, **kwargs)

    async def ws_connect(self, *args: Any, **kwargs: Any) -> ClientWebSocketResponse:
        return await self._session.ws_connect(*args, **kwargs)

    async def _request(self, method: str, url: str, retry: int = 2, **kwargs: Any) -> ClientResponse:
        if method != "get":
            if self._csrf_token is None:
                _LOGGER.debug("Обновление CSRF-токена")
                r = await self._session.get("https://yandex.ru/quasar/iot")
                raw = await r.text()
                m = RE_CSRF.search(raw)
                assert m, raw

                self._csrf_token = m[1]

            kwargs["headers"] = {"x-csrf-token": self._csrf_token}

        r = await getattr(self._session, method)(url, **kwargs)
        response_text = (await r.text())[:1024]
        if r.status == 200:
            return r
        elif r.status == 400:
            retry = 0
        elif r.status == 401:
            # 401 - no cookies
            await self.refresh_cookies()
        elif r.status == 403:
            # 403 - no x-csrf-token
            self._csrf_token = None
        else:
            _LOGGER.warning(f"{url} вернул {r.status}: {response_text}")

        if retry:
            _LOGGER.debug(f"Повтор {method} {url}")
            return await self._request(method, url, retry - 1, **kwargs)

        raise Exception(f"{url} вернул {r.status}: {response_text}")

    @property
    def _session_cookie(self) -> str:
        # noinspection PyProtectedMember
        raw = pickle.dumps(cast(CookieJar, self._session.cookie_jar)._cookies, pickle.HIGHEST_PROTOCOL)
        return base64.b64encode(raw).decode()
