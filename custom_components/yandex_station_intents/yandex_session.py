import base64
from dataclasses import dataclass, field
from http import HTTPStatus
import logging
import pickle
import re
from typing import Any, Self, cast

from aiohttp import ClientResponse, ClientWebSocketResponse, CookieJar, hdrs
import dacite
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import CONF_COOKIE, CONF_UID, CONF_X_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)

PASSPORT_URL = "https://mobileproxy.passport.yandex.net/1/bundle"
RE_CSRF = re.compile('"csrfToken2":"(.+?)"')

ISSUE_ID_REAUTH_REQUIRED = "reauth_required"
ISSUE_ID_CAPTCHA = "captcha"


class AuthException(Exception):
    pass


class AuthErrorException(AuthException):
    def __init__(self, error_codes: list[str]) -> None:
        self._error_codes = error_codes

    def __str__(self) -> str:
        return "Ошибка аутентификации: " + ", ".join(self._error_codes)


class CaptchaException(AuthException):
    def __str__(self) -> str:
        return "Обнаружена CAPTCHA"


@dataclass(kw_only=True)
class PassportResponse:
    status: str
    errors: list[str] = field(default_factory=list)

    def raise_for_error(self) -> None:
        if self.status != "ok":
            raise AuthErrorException(self.errors)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        base = dacite.from_dict(PassportResponse, data)
        base.raise_for_error()

        return dacite.from_dict(cls, data)


@dataclass
class XTokenResponse(PassportResponse):
    token_type: str
    access_token: str


@dataclass
class AuthTrackResponse(PassportResponse):
    track_id: str
    passport_host: str


@dataclass
class AccountInfo(PassportResponse):
    uid: int
    display_name: str
    display_login: str


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
                cookie_jar = cast(CookieJar, self._session.cookie_jar)
                empty_cookies = cookie_jar._cookies
                try:
                    cookie_jar._cookies = pickle.loads(base64.b64decode(cookie))
                    cookie_jar.clear(lambda _: False)
                except Exception as e:
                    _LOGGER.warning(f"Ошибка загрузки cookies: {e}")
                    cookie_jar._cookies = empty_cookies

    async def _async_auth(self, x_token: str) -> None:
        _LOGGER.debug("Аутентификация с помощью токена...")
        payload = {"type": "x-token", "retpath": "https://www.yandex.ru"}
        headers = {"Ya-Consumer-Authorization": f"OAuth {x_token}"}
        r = await self._session.post(f"{PASSPORT_URL}/auth/x_token/", data=payload, headers=headers)
        response = AuthTrackResponse.from_json(await r.json())

        payload = {"track_id": response.track_id}
        r = await self._session.get(f"{response.passport_host}/auth/session/", params=payload, allow_redirects=False)
        if r.status != HTTPStatus.FOUND:
            raise AuthErrorException(error_codes=["session.invalid_status_code"])

        location = r.headers.get(hdrs.LOCATION, "")
        if "auth/finish" in location:
            _LOGGER.debug("Аутентификация пройдена")
            return

        if "showcaptcha" in location:
            raise CaptchaException

        raise AuthErrorException(error_codes=["session.missing"])

    @property
    def _session_cookie(self) -> str:
        # noinspection PyProtectedMember
        raw = pickle.dumps(cast(CookieJar, self._session.cookie_jar)._cookies, pickle.HIGHEST_PROTOCOL)
        return base64.b64encode(raw).decode()

    async def async_get_x_token(self, host: str, cookies: dict[str, str]) -> str:
        client_creds = {
            "client_id": "c0ebe342af7d48fbbbfcf2d2eedb8f9e",
            "client_secret": "ad0a908f0aa341a182a37ecd75bc319e",
        }
        headers = {
            "Ya-Client-Host": host,  # passport.yandex.ru/com
            "Ya-Client-Cookie": "; ".join([f"{k}={v}" for k, v in cookies.items()]),  # достаточно Session_id
        }
        r = await self._session.post(f"{PASSPORT_URL}/oauth/token_by_sessionid", data=client_creds, headers=headers)
        response = XTokenResponse.from_json(await r.json())
        return response.access_token

    async def async_get_account_info(self, x_token: str) -> AccountInfo:
        headers = {"Authorization": f"OAuth {x_token}"}
        r = await self._session.get(f"{PASSPORT_URL}/account/short_info/?avatar_size=islands-300", headers=headers)
        return AccountInfo.from_json(await r.json())

    async def async_refresh(self) -> None:
        assert self._entry

        if not self._x_token:
            raise AuthException("missing x_token")

        try:
            await self._async_auth(self._x_token)
        except CaptchaException:
            ir.async_create_issue(
                self._hass,
                DOMAIN,
                f"{ISSUE_ID_CAPTCHA}_{self._entry.entry_id}",
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_ID_CAPTCHA,
                translation_placeholders={"entity": self._entry.title},
            )
            raise
        except AuthException:
            ir.async_create_issue(
                self._hass,
                DOMAIN,
                f"{ISSUE_ID_REAUTH_REQUIRED}_{self._entry.entry_id}",
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_ID_REAUTH_REQUIRED,
                translation_placeholders={"entity": self._entry.title},
            )
            raise

        data = self._entry.data.copy()
        data[CONF_COOKIE] = self._session_cookie
        if CONF_UID not in data:
            data[CONF_UID] = (await self.async_get_account_info(self._x_token)).uid
        self._hass.config_entries.async_update_entry(self._entry, data=data)

    async def async_validate(self) -> bool:
        r = await self._session.get("https://quasar.yandex.ru/get_account_config")
        return r.status == HTTPStatus.OK and (await r.json()).get("status") == "ok"

    async def get(self, url: str, **kwargs: Any) -> ClientResponse:
        return await self._request(hdrs.METH_GET, url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> ClientResponse:
        return await self._request(hdrs.METH_POST, url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> ClientResponse:
        return await self._request(hdrs.METH_PUT, url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> ClientResponse:
        return await self._request(hdrs.METH_DELETE, url, **kwargs)

    async def ws_connect(self, *args: Any, **kwargs: Any) -> ClientWebSocketResponse:
        return await self._session.ws_connect(*args, **kwargs)

    async def _request(self, method: str, url: str, retry: int = 2, **kwargs: Any) -> ClientResponse:
        if method != hdrs.METH_GET:
            if self._csrf_token is None:
                _LOGGER.debug("Обновление CSRF-токена")
                r = await self._session.get("https://yandex.ru/quasar/iot")
                raw = await r.text()
                m = RE_CSRF.search(raw)
                assert m, raw

                self._csrf_token = m[1]

            kwargs["headers"] = {"x-csrf-token": self._csrf_token}

        r = await getattr(self._session, method.lower())(url, **kwargs)
        response_text = (await r.text())[:1024]
        if r.status == HTTPStatus.OK:
            if self._entry:
                ir.async_delete_issue(self._hass, DOMAIN, f"{ISSUE_ID_CAPTCHA}_{self._entry.entry_id}")
                ir.async_delete_issue(self._hass, DOMAIN, f"{ISSUE_ID_REAUTH_REQUIRED}_{self._entry.entry_id}")

            return r
        elif r.status == HTTPStatus.BAD_REQUEST:
            retry = 0
        elif r.status == HTTPStatus.UNAUTHORIZED:
            try:
                await self.async_refresh()
            except AuthException as e:
                if retry == 0:
                    raise
                _LOGGER.debug(e)
        elif r.status == HTTPStatus.FORBIDDEN:
            self._csrf_token = None
        else:
            _LOGGER.warning(f"Неожиданный ответ от {url}: [{r.status}] {response_text}")

        if retry:
            _LOGGER.debug(f"Повторный запрос {method} {url}")
            return await self._request(method, url, retry - 1, **kwargs)

        raise Exception(f"Неожиданный ответ от {url}: [{r.status}] {response_text}")
