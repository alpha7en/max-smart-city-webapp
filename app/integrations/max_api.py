"""Тонкий async-клиент MAX Bot API (https://platform-api2.max.ru).

TLS проверяется всегда: системные корни + certifi + сертификаты Минцифры (certs/).
"""
from __future__ import annotations

import asyncio
import logging
import ssl
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import certifi
import httpx

log = logging.getLogger(__name__)
RUSSIAN_CA = Path(__file__).with_name("certs") / "russian_trusted_ca.pem"
UPDATE_TYPES = ("message_created", "message_callback", "bot_started")
RETRY_DELAYS = (1, 2, 4)
KEEP = object()  # edit(): не трогать вложения


class MaxApiError(Exception):
    def __init__(self, status: int, code: str = "", message: str = ""):
        super().__init__(f"MAX API {status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


def ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cafile=certifi.where())
    if RUSSIAN_CA.exists():
        ctx.load_verify_locations(cafile=str(RUSSIAN_CA))
    return ctx


def message_body(text: str | None, keyboard: dict | None = None, *, clear_keyboard: bool = False,
                 fmt: str | None = "markdown", notify: bool = True) -> dict:
    """NewMessageBody. clear_keyboard — отправить attachments: [] (убрать кнопки при замене)."""
    body: dict[str, Any] = {"text": text}
    if fmt:
        body["format"] = fmt
    if keyboard:
        body["attachments"] = [keyboard]
    elif clear_keyboard:
        body["attachments"] = []
    if not notify:
        body["notify"] = False
    return body


class MaxApi:
    def __init__(self, token: str, base: str = "https://platform-api2.max.ru", *,
                 client: httpx.AsyncClient | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.token = token
        self.base = base.rstrip("/")
        self._client = client or httpx.AsyncClient(
            verify=ssl_context(), timeout=httpx.Timeout(15.0, read=35.0)
        )
        self._sleep = sleep

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, *, params: dict | None = None,
                       json: Any = None, retries: int = len(RETRY_DELAYS)) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {"Authorization": self.token}
        for attempt in range(retries + 1):
            delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
            try:
                resp = await self._client.request(method, self.base + path, params=params,
                                                  json=json, headers=headers)
            except httpx.TransportError as e:
                if attempt >= retries:
                    raise MaxApiError(0, "network", str(e)) from e
                log.warning("MAX %s %s: %s, retry in %ss", method, path, e, delay)
                await self._sleep(delay)
                continue
            if resp.status_code < 400:
                return resp.json() if resp.content else {}
            try:
                err = resp.json()
            except ValueError:
                err = {}
            code = str(err.get("code") or err.get("error") or "")
            message = str(err.get("message") or resp.text[:200])
            retryable = (resp.status_code == 429 or resp.status_code >= 500
                         or "attachment.not.ready" in code or "attachment.not.ready" in message)
            if retryable and attempt < retries:
                log.warning("MAX %s %s → %s %s, retry in %ss", method, path, resp.status_code, code, delay)
                await self._sleep(delay)
                continue
            raise MaxApiError(resp.status_code, code, message)
        raise AssertionError("unreachable")

    # --- Методы API ---

    async def get_me(self) -> dict:
        return await self._request("GET", "/me")

    async def get_updates(self, marker: int | None = None, timeout: int = 25, limit: int = 100,
                          types: tuple[str, ...] = UPDATE_TYPES) -> dict:
        """Long polling. Повторы — на стороне poller'а (там же backoff)."""
        params = {"marker": marker, "timeout": timeout, "limit": limit, "types": ",".join(types)}
        return await self._request("GET", "/updates", params=params, retries=0)

    async def send(self, text: str, *, user_id: int | None = None, chat_id: int | None = None,
                   keyboard: dict | None = None, fmt: str | None = "markdown", notify: bool = True) -> dict:
        """POST /messages → message (mid в message.body.mid)."""
        if user_id is None and chat_id is None:
            raise ValueError("user_id or chat_id required")
        params = {"user_id": user_id} if user_id is not None else {"chat_id": chat_id}
        res = await self._request("POST", "/messages", params=params,
                                  json=message_body(text, keyboard, fmt=fmt, notify=notify))
        return res.get("message") or {}

    async def edit(self, mid: str, text: str | None = None, keyboard: dict | None | object = KEEP,
                   fmt: str | None = "markdown") -> None:
        """PUT /messages. text=None — текст не передаём; keyboard=KEEP — не менять вложения, None — убрать клавиатуру."""
        body: dict[str, Any] = {}
        if text is not None:
            body["text"] = text
            if fmt:
                body["format"] = fmt
        if keyboard is not KEEP:
            body["attachments"] = [keyboard] if keyboard else []
        await self._request("PUT", "/messages", params={"message_id": mid}, json=body)

    async def delete(self, mid: str) -> None:
        if not mid:
            return
        await self._request("DELETE", "/messages", params={"message_id": mid})

    delete_message = delete
    edit_message = edit

    async def delete_messages(self, mids: list[str]) -> list[str]:
        """Удалить несколько сообщений. Возвращает список успешно удалённых mid."""
        deleted: list[str] = []
        for mid in mids:
            if not mid:
                continue
            try:
                await self.delete(mid)
                deleted.append(mid)
            except MaxApiError as e:
                log.warning("delete %s failed: %s", mid, e)
        return deleted

    async def safe_delete(self, mid: str) -> bool:
        """Безопасное удаление одного сообщения без выброса исключения."""
        if not mid:
            return False
        try:
            await self.delete(mid)
            return True
        except MaxApiError as e:
            log.warning("delete %s failed: %s", mid, e)
            return False

    async def safe_edit(self, mid: str, text: str | None = None, keyboard: dict | None | object = KEEP,
                        fmt: str | None = "markdown") -> bool:
        """Безопасное редактирование одного сообщения без выброса исключения."""
        if not mid:
            return False
        try:
            await self.edit(mid, text=text, keyboard=keyboard, fmt=fmt)
            return True
        except MaxApiError as e:
            log.warning("edit %s failed: %s", mid, e)
            return False

    async def answer(self, callback_id: str, *, message: dict | None = None,
                     notification: str | None = None) -> None:
        """POST /answers: message (NewMessageBody) заменяет сообщение с кнопкой, notification — тост."""
        body: dict[str, Any] = {}
        if message is not None:
            body["message"] = message
        if notification:
            body["notification"] = notification
        await self._request("POST", "/answers", params={"callback_id": callback_id}, json=body)

    async def typing(self, chat_id: int) -> None:
        await self._request("POST", f"/chats/{chat_id}/actions", json={"action": "typing_on"}, retries=0)

    async def set_commands(self, commands: list[tuple[str, str]]) -> None:
        await self._request("PATCH", "/me/commands",
                            json={"commands": [{"name": n, "description": d} for n, d in commands]})

    async def download(self, url: str, max_bytes: int = 10 * 1024 * 1024, timeout: float = 15.0) -> tuple[bytes, str]:
        """Скачивает вложение по URL (без токена — это CDN). → (данные, content-type).
        Больше max_bytes или не картинка → MaxApiError."""
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                async with self._client.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
                    if resp.status_code >= 400:
                        if (resp.status_code == 429 or resp.status_code >= 500) and attempt < len(RETRY_DELAYS):
                            await self._sleep(RETRY_DELAYS[attempt])
                            continue
                        raise MaxApiError(resp.status_code, "download", f"HTTP {resp.status_code}")
                    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    if ctype and not (ctype.startswith("image/") or ctype == "application/octet-stream"):
                        raise MaxApiError(415, "not_image", ctype)
                    declared = int(resp.headers.get("content-length") or 0)
                    if declared > max_bytes:
                        raise MaxApiError(413, "too_large", str(declared))
                    buf = bytearray()
                    async for chunk in resp.aiter_bytes():
                        buf.extend(chunk)
                        if len(buf) > max_bytes:
                            raise MaxApiError(413, "too_large", f">{max_bytes}")
                    return bytes(buf), ctype
            except httpx.TransportError as e:
                if attempt >= len(RETRY_DELAYS):
                    raise MaxApiError(0, "network", str(e)) from e
                await self._sleep(RETRY_DELAYS[attempt])
        raise MaxApiError(0, "download", "retries exhausted")
