"""
High-level HTTP Client for MAX Platform API (platform-api2.max.ru).
Features:
- Safe header authentication
- Automatic URL parameter encoding and sanitization
- Robust query-string callback answering
- Full typed models integration
"""

import ssl
import json
import urllib.request
import urllib.parse
from typing import Optional, Dict, Any, List
import logging

from max_bot_sdk.models import User, Update

logger = logging.getLogger("max_bot_sdk.client")

class MaxBotError(Exception):
    """Базовое исключение для всех ошибок MAX Bot SDK."""
    pass

class MaxAPIError(MaxBotError, urllib.error.URLError):
    """Ошибка API платформы MAX (HTTP 4xx / 5xx)."""
    def __init__(self, code: int, message: str, body: str = ""):
        super().__init__(f"MAX API HTTP Error {code}: {message}")
        self.code = code
        self.message = message
        self.body = body

class MaxNetworkError(MaxBotError, urllib.error.URLError):
    """Сетевая ошибка при взаимодействии с платформой MAX (таймаут, DNS, разрыв соединения)."""
    def __init__(self, message: str, original_error: Optional[Exception] = None):
        super().__init__(f"MAX Network Error: {message}")
        self.original_error = original_error

class MaxValidationError(MaxBotError, ValueError):
    """Ошибка валидации данных (некорректный токен, параметры запроса)."""
    pass

class MaxBotClient:
    def __init__(self, token: str, base_url: str = "https://platform-api2.max.ru"):
        if not token:
            raise MaxValidationError("Token is required to initialize MaxBotClient")
        self.token = token.strip().strip("\"'")
        self.base_url = base_url.rstrip("/")
        self.ctx = ssl._create_unverified_context()

    def _request(
        self,
        endpoint: str,
        method: str = "GET",
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        timeout: int = 15
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        if params:
            url += f"?{urllib.parse.urlencode(params)}"

        data = None
        headers = {
            "Authorization": self.token,
            "User-Agent": "MAX-Python-SDK/1.0"
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(json_body).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=timeout) as resp:
                raw_bytes = resp.read()
                if not raw_bytes:
                    return {}
                return json.loads(raw_bytes.decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                pass
            logger.error("MAX API HTTP Error %s on %s %s: %s", e.code, method, endpoint, err_body)
            raise MaxAPIError(e.code, str(e), err_body) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            logger.error("MAX API Network Error on %s %s: %s", method, endpoint, e)
            raise MaxNetworkError(str(e), original_error=e) from e

    def get_me(self) -> User:
        """Получение профиля текущего бота"""
        data = self._request("/me")
        return User.from_dict(data)

    def send_message(
        self,
        chat_id: Optional[Any] = None,
        user_id: Optional[int] = None,
        text: str = "",
        keyboard: Optional[Dict[str, Any]] = None,
        buttons: Optional[List[List[Dict[str, Any]]]] = None,
        format_type: str = "markdown"
    ) -> Dict[str, Any]:
        """
        Отправка сообщения в диалог или групповой чат.
        Поддерживает как typed keyboard словарь, так и raw список buttons.
        """
        from max_bot_sdk.keyboards import KeyboardBuilder

        params = {}
        if chat_id:
            params["chat_id"] = chat_id
        elif user_id:
            params["user_id"] = user_id
        else:
            raise ValueError("Для отправки сообщения требуется указать chat_id или user_id")

        payload: Dict[str, Any] = {
            "text": text,
            "format": format_type
        }

        if keyboard:
            payload["attachments"] = [keyboard]
        elif buttons:
            payload["attachments"] = [KeyboardBuilder.inline(buttons)]

        return self._request("/messages", method="POST", params=params, json_body=payload)

    def answer_callback(self, callback_id: str, notification: str = "Принято") -> Dict[str, Any]:
        """
        Подтверждение нажатия на inline-кнопку.
        В API MAX callback_id передается в query params, а notification в теле запроса.
        """
        endpoint = f"/answers?callback_id={urllib.parse.quote(str(callback_id))}"
        payload = {"notification": notification or "Принято"}
        try:
            return self._request(endpoint, method="POST", json_body=payload)
        except Exception as e:
            logger.warning("Could not answer callback %s: %s", callback_id, e)
            return {}

    def get_updates(self, marker: Optional[int] = None, timeout: int = 25) -> Dict[str, Any]:
        """
        Long Polling метод получения событий.
        """
        params = {}
        if marker:
            params["marker"] = marker
        return self._request("/updates", method="GET", params=params, timeout=timeout + 5)
