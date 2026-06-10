import asyncio
import base64
from collections import defaultdict
from dataclasses import dataclass, field
from http import HTTPStatus
from http.cookies import SimpleCookie
import logging
import pickle
from typing import Any, Self, cast

from aiohttp import ClientResponse, ClientWebSocketResponse, CookieJar, hdrs
import dacite
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from yarl import URL

from .const import CONF_COOKIE, CONF_UID, CONF_X_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)

ISSUE_ID_REAUTH_REQUIRED = "reauth_required"
ISSUE_ID_CAPTCHA = "captcha"


class AuthError(Exception):
    def __init__(self, error_codes: list[str]) -> None:
        self._error_codes = error_codes

    def __str__(self) -> str:
        return "Ошибка аутентификации: " + ", ".join(self._error_codes)


class CaptchaDetectedError(AuthError):
    def __str__(self) -> str:
        return "Обнаружена CAPTCHA"


@dataclass(kw_only=True)
class PassportResponse:
    """Базовый ответ от сервиса passport (с автоматической проверкой на ошибки).

    Служит родительским классом для конкретных ответов."""

    status: str
    errors: list[str] = field(default_factory=list)

    def raise_for_error(self) -> None:
        if self.status != "ok":
            raise AuthError(self.errors)

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

    @property
    def auth_session_url(self) -> str:
        return str(URL(self.passport_host) / "auth/session/")


@dataclass
class AccountInfo(PassportResponse):
    uid: int
    display_name: str
    display_login: str


class YandexCookieJar(CookieJar):
    def __init__(self, loop: asyncio.AbstractEventLoop):
        # Яндекс не воспринимает значения некоторых кук, если они обёрнуты в кавычки.
        # Это особенно важно для куки "i", которая, похоже, используется для детекции ботов.
        super().__init__(loop=loop, quote_cookie=False)

    def load_from_base64(self, data: str) -> None:
        try:
            self._cookies = pickle.loads(base64.b64decode(data))
            self.clear(lambda _: False)  # вызывает ошибку, если куки в неправильном формате (#46)
        except Exception as e:
            _LOGGER.warning(f"Ошибка загрузки cookies: {e}")
            self._cookies = defaultdict(SimpleCookie)

    def as_base64(self) -> str:
        return base64.b64encode(pickle.dumps(self._cookies, pickle.HIGHEST_PROTOCOL)).decode()


class PassportClient:
    BASE_URL = URL("https://mobileproxy.passport.yandex.net/1/bundle")

    def __init__(self, hass: HomeAssistant):
        self._session = async_create_clientsession(hass, cookie_jar=YandexCookieJar(hass.loop))

    async def async_get_x_token(self, host: str, cookies: dict[str, str]) -> str:
        """Возвращает x_token по сессионной куке от пользователя."""
        client_creds = {
            "client_id": "c0ebe342af7d48fbbbfcf2d2eedb8f9e",
            "client_secret": "ad0a908f0aa341a182a37ecd75bc319e",
        }
        headers = {
            "Ya-Client-Host": host,  # passport.yandex.ru/com
            "Ya-Client-Cookie": "; ".join([f"{k}={v}" for k, v in cookies.items()]),  # но достаточно Session_id
        }
        r = await self._request(
            hdrs.METH_POST,
            "oauth/token_by_sessionid",
            data=client_creds,
            headers=headers,
        )
        response = XTokenResponse.from_json(await r.json())
        return response.access_token

    async def async_get_account_info(self, x_token: str) -> AccountInfo:
        """Возвращает информацию об аккаунте по x_token."""
        headers = {"Authorization": f"OAuth {x_token}"}
        r = await self._request(
            hdrs.METH_GET,
            "account/short_info/?avatar_size=islands-300",
            headers=headers,
        )
        return AccountInfo.from_json(await r.json())

    async def async_start_auth(self, x_token: str) -> AuthTrackResponse:
        """Инициирует аутентификацию по x_token."""
        _LOGGER.debug("Аутентификация по x_token...")
        payload = {"type": "x-token", "retpath": "https://www.yandex.ru"}
        headers = {"Ya-Consumer-Authorization": f"OAuth {x_token}"}
        r = await self._request(
            hdrs.METH_POST,
            "auth/x_token/",
            data=payload,
            headers=headers,
        )
        return AuthTrackResponse.from_json(await r.json())

    async def _request(self, method: str, path: str, **kwargs: Any) -> ClientResponse:
        """Выполняет HTTP-запрос к Passport API."""
        url = self.BASE_URL.joinpath(path, encoded=True)
        _LOGGER.debug(f"{method} {url}")
        return await self._session.request(method, url, **kwargs)


class YandexSession:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._cookie_jar = YandexCookieJar(hass.loop)
        self._session = async_create_clientsession(hass, cookie_jar=self._cookie_jar)
        self._passport = PassportClient(hass)
        self._x_token: str = self._entry.data.get(CONF_X_TOKEN, "")
        self._csrf_token: str | None = None

        if cookie_b64 := self._entry.data.get(CONF_COOKIE):
            self._cookie_jar.load_from_base64(cookie_b64)

    async def async_authenticate(self) -> None:
        """Выполняет аутентификацию и заводит repair issue при ошибке."""
        try:
            await self._async_run_auth_flow()
        except CaptchaDetectedError:
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
        except AuthError:
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

    async def async_save_to_entry(self) -> None:
        """Сохраняет сессию в ConfigEntry."""
        data = self._entry.data.copy()
        data[CONF_COOKIE] = self._cookie_jar.as_base64()

        if CONF_UID not in data:  # миграция со старой версии, сейчас заполняется из config_flow
            account = await self._passport.async_get_account_info(self._x_token)
            data[CONF_UID] = account.uid

        self._hass.config_entries.async_update_entry(self._entry, data=data)

    async def async_verify(self) -> bool:
        """Проверяет действительность сессии."""
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

    async def _async_run_auth_flow(self) -> None:
        """Проходит шаги аутентификации и устанавливает сессионные куки."""
        flow = await self._passport.async_start_auth(self._x_token)
        payload = {"track_id": flow.track_id}
        url = flow.auth_session_url
        _LOGGER.debug(f"{hdrs.METH_GET} {url}")
        r = await self._session.get(url, params=payload, allow_redirects=False)
        if r.status != HTTPStatus.FOUND:
            raise AuthError(error_codes=["session.invalid_status_code"])

        location = r.headers.get(hdrs.LOCATION, "")
        if "auth/finish" in location:
            _LOGGER.debug("Аутентификация пройдена")
            return

        if "showcaptcha" in location:
            raise CaptchaDetectedError(error_codes=[])

        raise AuthError(error_codes=["session.missing"])

    async def _update_csrf_token(self) -> None:
        _LOGGER.debug("Обновление CSRF-токена")
        url = "https://quasar.yandex.ru/csrf_token"
        _LOGGER.debug(f"{hdrs.METH_GET} {url}")
        r = await self._session.get("https://quasar.yandex.ru/csrf_token")
        resp = await r.json()
        assert resp.get("status") == "ok", resp

        self._csrf_token = resp.get("token")
        assert self._csrf_token, resp

    async def _request(self, method: str, url: str, retries_left: int = 2, **kwargs: Any) -> ClientResponse:
        if method in (hdrs.METH_POST, hdrs.METH_PUT, hdrs.METH_DELETE):
            if self._csrf_token is None:
                await self._update_csrf_token()

            kwargs["headers"] = {"x-csrf-token": self._csrf_token}

        _LOGGER.debug(f"{method} {url}")
        r = cast(ClientResponse, await getattr(self._session, method.lower())(url, **kwargs))
        response_text = (await r.text())[:1024]

        if r.status == HTTPStatus.OK:
            if self._entry:
                ir.async_delete_issue(self._hass, DOMAIN, f"{ISSUE_ID_CAPTCHA}_{self._entry.entry_id}")
                ir.async_delete_issue(self._hass, DOMAIN, f"{ISSUE_ID_REAUTH_REQUIRED}_{self._entry.entry_id}")

            return r

        if r.status == HTTPStatus.BAD_REQUEST:
            retries_left = 0
        elif r.status == HTTPStatus.UNAUTHORIZED:
            try:
                await self.async_authenticate()
                await self.async_save_to_entry()
            except AuthError as e:
                if retries_left == 0:
                    raise
                _LOGGER.debug(e)
        elif r.status == HTTPStatus.FORBIDDEN:
            # возможен в двух случаях: кончился csrf_token или нас поймал антибот
            _LOGGER.debug(f"Неожиданный ответ от {url}: [{r.status}] {response_text}")
            await self._update_csrf_token()
        else:
            _LOGGER.warning(f"Неожиданный ответ от {url}: [{r.status}] {response_text}")

        if retries_left:
            _LOGGER.debug(f"Повторный запрос {method} {url}")
            return await self._request(method, url, retries_left - 1, **kwargs)

        raise Exception(f"Неожиданный ответ от {url}: [{r.status}] {response_text}")
