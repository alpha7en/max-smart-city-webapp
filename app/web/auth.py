"""Авторизация мини-приложения по initData MAX (алгоритм как в официальном Go-клиенте ValidateInitData).

secret = HMAC_SHA256(key=b"WebAppData", msg=bot_token)
check  = "\\n".join(sorted(f"{k}={v}" для всех пар, кроме hash))
hash   = hex(HMAC_SHA256(key=secret, msg=check))
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, unquote, urlencode

from fastapi import HTTPException, Request

HEADER = "X-Max-Init-Data"
DEV_HEADER = "X-Dev-User"


class InitDataError(ValueError):
    pass


@dataclass
class InitData:
    max_user_id: int
    user: dict = field(default_factory=dict)       # {id, first_name, last_name, username, ...}
    auth_date: int = 0
    start_param: str | None = None
    fields: dict[str, str] = field(default_factory=dict)


def _secret(token: str) -> bytes:
    return hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()


def _check_hash(pairs: list[tuple[str, str]], token: str) -> tuple[bool, dict[str, str]]:
    data = dict(pairs)
    received = data.pop("hash", "")
    check = "\n".join(sorted(f"{k}={v}" for k, v in data.items()))
    expected = hmac.new(_secret(token), check.encode(), hashlib.sha256).hexdigest()
    return bool(received) and hmac.compare_digest(received, expected), data


def validate_init_data(init_data: str, token: str, ttl: int = 24 * 3600, now: float | None = None) -> InitData:
    """Проверяет подпись и срок; → InitData. Ошибка → InitDataError."""
    if not init_data or not token:
        raise InitDataError("empty init data or token")
    ok, data = False, {}
    for raw in (init_data, unquote(init_data)):  # как пришло и с двойным декодированием (как Go-клиент)
        ok, data = _check_hash(parse_qsl(raw, keep_blank_values=True), token)
        if ok:
            break
    if not ok:
        raise InitDataError("bad hash")
    try:
        auth_date = int(data.get("auth_date", "0"))
    except ValueError as e:
        raise InitDataError("bad auth_date") from e
    now = time.time() if now is None else now
    if not auth_date or now - auth_date > ttl:
        raise InitDataError("expired")
    try:
        user = json.loads(data.get("user") or "{}")
        uid = int(user["id"])
    except (ValueError, KeyError, TypeError) as e:
        raise InitDataError("no user") from e
    return InitData(uid, user, auth_date, data.get("start_param") or None, data)


def sign_init_data(fields: dict[str, str], token: str) -> str:
    """Собрать подписанную строку initData (для тестов и локальной отладки)."""
    check = "\n".join(sorted(f"{k}={v}" for k, v in fields.items()))
    digest = hmac.new(_secret(token), check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


async def current_user(request: Request) -> InitData:
    """FastAPI Depends: пользователь мини-приложения. Нет/невалидный initData → 401."""
    settings = request.app.state.settings
    raw = request.headers.get(HEADER, "")
    if raw:
        try:
            return validate_init_data(raw, settings.bot_token, settings.init_data_ttl)
        except InitDataError as e:
            raise HTTPException(401, {"code": "unauthorized", "message": "Откройте мини-приложение из чата с ботом."}) from e
    dev = request.headers.get(DEV_HEADER, "")
    if settings.dev_auth and dev.isdigit():  # только локальная разработка, по умолчанию выключено
        return InitData(int(dev), {"id": int(dev)}, int(time.time()))
    raise HTTPException(401, {"code": "unauthorized", "message": "Откройте мини-приложение из чата с ботом."})
