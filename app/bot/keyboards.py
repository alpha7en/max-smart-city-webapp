"""Кнопки MAX и кодек payload "flow|action|arg".

flow — session.flow_id (6 симв.) для кнопок сценария или "g" для глобальных (меню, уведомления).
Ничего не обрезаем молча: превышение лимитов API — ValueError (ошибка разработчика).
Стиль текстов кнопок: обычно ≤ 24 символов, счётчики/варианты адреса ≤ 40, срочная ≤ 32.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

GLOBAL = "g"
PAYLOAD_MAX_BYTES = 64          # наш лимит (API — 1024)
TEXT_MAX = 128                  # лимит API
ROWS_MAX, ROW_MAX, ROW_SPECIAL_MAX = 30, 7, 3
SPECIAL = {"link", "open_app", "request_contact", "request_geo_location"}
_OPEN_APP_PAYLOAD = re.compile(r"^[\w-]*$")
_USERNAME = re.compile(r"^[A-Za-z0-9_.]+$")

Button = dict[str, Any]


@dataclass(frozen=True)
class Payload:
    flow: str
    action: str
    arg: str = ""

    @property
    def is_global(self) -> bool:
        return self.flow == GLOBAL


def encode(flow: str, action: str, arg: str | int = "") -> str:
    arg = str(arg)
    if not flow or not action or "|" in flow or "|" in action:
        raise ValueError(f"bad payload parts: {flow!r} {action!r}")
    s = f"{flow}|{action}|{arg}"
    if len(s.encode()) > PAYLOAD_MAX_BYTES:
        raise ValueError(f"payload longer than {PAYLOAD_MAX_BYTES} bytes: {s!r}")
    return s


def decode(payload: str | None) -> Payload | None:
    """'flow|action|arg' → Payload; мусор → None (кнопку считаем устаревшей)."""
    if not payload:
        return None
    parts = payload.split("|", 2)
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    return Payload(parts[0], parts[1], parts[2] if len(parts) > 2 else "")


def _text(text: str) -> str:
    if not text or len(text) > TEXT_MAX:
        raise ValueError(f"button text must be 1..{TEXT_MAX} chars: {text!r}")
    return text


def callback(text: str, flow: str, action: str, arg: str | int = "") -> Button:
    return {"type": "callback", "text": _text(text), "payload": encode(flow, action, arg)}


def gbtn(text: str, action: str, arg: str | int = "") -> Button:
    """Глобальная кнопка (g|action|arg): работает из любого состояния."""
    return callback(text, GLOBAL, action, arg)


def _public_url(url: str) -> str:
    """MAX отвергает всё сообщение (400), если в кнопке не публичный http(s)-URL."""
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host or len(url) > 2048:
        raise ValueError(f"link button needs absolute http(s) URL: {url!r}")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError(f"link button URL is not public: {url!r}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return url
    if not ip.is_global:
        raise ValueError(f"link button URL is not public: {url!r}")
    return url


def link(text: str, url: str) -> Button:
    """Внешняя ссылка (открывается в браузере). Для мини-приложения — только open_app."""
    return {"type": "link", "text": _text(text), "url": _public_url(url)}


def request_contact(text: str) -> Button:
    return {"type": "request_contact", "text": _text(text)}


def request_geo(text: str, quick: bool = False) -> Button:
    return {"type": "request_geo_location", "text": _text(text), "quick": quick}


def open_app(text: str, web_app: str, payload: str | None = None) -> Button | None:
    """Кнопка мини-приложения. web_app — username бота; без него кнопку не показываем (None).
    contact_id сервер дописывает сам."""
    if not web_app:
        return None
    if not _USERNAME.match(web_app):  # URL здесь → 404 not.found и сообщение не уходит
        raise ValueError(f"open_app web_app must be bot username, got {web_app!r}")
    btn: Button = {"type": "open_app", "text": _text(text), "web_app": web_app}
    if payload:
        if not _OPEN_APP_PAYLOAD.match(payload) or len(payload) > 512:
            raise ValueError(f"bad open_app payload: {payload!r}")
        btn["payload"] = payload
    return btn


def kb(*rows: list[Button | None] | Button | None) -> dict:
    """Клавиатура-вложение. Ряд — список кнопок или одна кнопка; None и пустые ряды пропускаются."""
    buttons: list[list[Button]] = []
    for row in rows:
        items = row if isinstance(row, list) else [row]
        items = [b for b in items if b]
        if not items:
            continue
        if len(items) > ROW_MAX or sum(b["type"] in SPECIAL for b in items) > ROW_SPECIAL_MAX:
            raise ValueError(f"too many buttons in a row: {items}")
        buttons.append(items)
    if len(buttons) > ROWS_MAX:
        raise ValueError("too many rows")
    return {"type": "inline_keyboard", "payload": {"buttons": buttons}}


# Вручную набранные ответы там, где ждём кнопку (правило 8 роутера).
TEXT_ALIASES = {
    "да": "yes", "верно": "yes", "всё верно": "yes", "все верно": "yes",
    "нет": "no", "назад": "back",
}


ID_MAX_DIGITS = 18  # SQLite INTEGER — до 2^63-1; длиннее → OverflowError при запросе


def parse_id(value: object) -> int | None:
    """id из payload/URL: только ASCII-цифры, не длиннее 18, больше 0; иначе None."""
    s = str(value if value is not None else "")
    if not (s.isascii() and s.isdigit() and len(s) <= ID_MAX_DIGITS):
        return None
    return int(s) or None


def text_alias(text: str | None) -> str | None:
    """«да»/«нет»/«назад» → 'yes'/'no'/'back'."""
    return TEXT_ALIASES.get((text or "").strip().strip(".!").lower())
