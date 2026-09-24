"""FakeMaxApi (записывает вызовы) и фабрики апдейтов в формате MAX (api-schema, Update)."""
from __future__ import annotations

import itertools
import time
from typing import Any

from app.integrations.max_api import KEEP, MaxApiError

BOT_ID = 423938205
_seq = itertools.count(1)


def _ms() -> int:
    return int(time.time() * 1000)


# --- Фабрики апдейтов ---

def user(uid: int, first_name: str = "Анна", last_name: str | None = "Иванова", is_bot: bool = False) -> dict:
    return {"user_id": uid, "first_name": first_name, "last_name": last_name, "username": None,
            "is_bot": is_bot, "last_activity_time": _ms()}


BOT_USER = user(BOT_ID, "Хакатон МАХ 226", None, is_bot=True)


def chat_of(uid: int) -> int:
    return 400_000_000 + uid


def message_created(uid: int, text: str | None = None, attachments: list[dict] | None = None,
                    mid: str | None = None, chat_type: str = "dialog") -> dict:
    n = next(_seq)
    return {
        "update_type": "message_created",
        "timestamp": _ms(),
        "message": {
            "sender": user(uid),
            "recipient": {"chat_id": chat_of(uid), "chat_type": chat_type, "user_id": BOT_ID},
            "timestamp": _ms(),
            "body": {"mid": mid or f"mid.user.{n:016x}", "seq": n, "text": text, "attachments": attachments},
        },
        "user_locale": "ru",
    }


def image(url: str = "https://i.oneme.ru/i?r=photo1") -> dict:
    return {"type": "image", "payload": {"photo_id": next(_seq), "token": "tok", "url": url}}


def file(url: str = "https://fd.oneme.ru/f?r=1", filename: str = "meter.jpg", size: int = 1024) -> dict:
    return {"type": "file", "payload": {"url": url, "token": "tok"}, "filename": filename, "size": size}


def contact(owner_uid: int, phone: str = "79123456789", name: str = "Анна Иванова") -> dict:
    vcf = f"BEGIN:VCARD\r\nVERSION:3.0\r\nTEL;TYPE=cell:{phone}\r\nFN:{name}\r\nEND:VCARD\r\n"
    return {"type": "contact", "payload": {"vcf_info": vcf, "hash": "0" * 64, "max_info": user(owner_uid)}}


def location(lat: float = 55.75, lon: float = 37.59) -> dict:
    return {"type": "location", "latitude": lat, "longitude": lon}


def sticker() -> dict:
    return {"type": "sticker", "payload": {"url": "https://st.max.ru/s.webp", "code": "c1"},
            "width": 128, "height": 128}


def message_callback(uid: int, payload: str, mid: str = "mid.bot.1", callback_id: str | None = None) -> dict:
    return {
        "update_type": "message_callback",
        "timestamp": _ms(),
        "callback": {"timestamp": _ms(), "callback_id": callback_id or f"cb.{next(_seq)}",
                     "payload": payload, "user": user(uid)},
        "message": {  # исходное сообщение бота: sender — бот!
            "sender": BOT_USER,
            "recipient": {"chat_id": chat_of(uid), "chat_type": "dialog", "user_id": uid},
            "timestamp": _ms(),
            "body": {"mid": mid, "seq": 1, "text": "…", "attachments": []},
        },
        "user_locale": "ru",
    }


def bot_started(uid: int, payload: str | None = None) -> dict:
    return {"update_type": "bot_started", "timestamp": _ms(), "chat_id": chat_of(uid),
            "user": user(uid), "payload": payload, "user_locale": "ru"}


# --- FakeMaxApi ---

class FakeMaxApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.files: dict[str, bytes] = {}
        self.update_batches: list[dict] = []
        self._mid = itertools.count(1)
        self._mark = 0  # clear() скрывает старые вызовы от проверок, но кнопки остаются доступны

    def _rec(self, name: str, **kw: Any) -> None:
        self.calls.append((name, kw))

    async def get_me(self) -> dict:
        return {"user_id": BOT_ID, "first_name": "Бот", "username": "test_bot", "is_bot": True}

    async def get_updates(self, marker=None, timeout=25, limit=100, types=()) -> dict:
        self._rec("get_updates", marker=marker, timeout=timeout)
        if self.update_batches:
            return self.update_batches.pop(0)
        return {"updates": [], "marker": marker}

    async def send(self, text, *, user_id=None, chat_id=None, keyboard=None, fmt="markdown", notify=True) -> dict:
        mid = f"mid.bot.{next(self._mid)}"
        self._rec("send", text=text, user_id=user_id, chat_id=chat_id, keyboard=keyboard, mid=mid)
        return {"body": {"mid": mid, "seq": 1, "text": text}, "recipient": {"user_id": user_id}}

    async def edit(self, mid, text=None, keyboard=KEEP, fmt="markdown") -> None:
        self._rec("edit", mid=mid, text=text, keyboard=keyboard)

    async def delete(self, mid) -> None:
        self._rec("delete", mid=mid)

    async def answer(self, callback_id, *, message=None, notification=None) -> None:
        self._rec("answer", callback_id=callback_id, message=message, notification=notification)

    async def typing(self, chat_id) -> None:
        self._rec("typing", chat_id=chat_id)

    async def set_commands(self, commands) -> None:
        self._rec("set_commands", commands=commands)

    async def download(self, url, max_bytes=10 * 1024 * 1024, timeout=15.0) -> tuple[bytes, str]:
        self._rec("download", url=url)
        if url not in self.files:
            raise MaxApiError(404, "not.found", url)
        return self.files[url], "image/jpeg"

    async def close(self) -> None:
        pass

    # --- Помощники для проверок ---

    def named(self, name: str) -> list[dict]:
        return [kw for n, kw in self.calls[self._mark:] if n == name]

    def outgoing(self, everything: bool = False) -> list[tuple[str, dict | None]]:
        """Всё, что увидел пользователь: (текст, клавиатура) из send и из answer(message)."""
        out = []
        for n, kw in self.calls[0 if everything else self._mark:]:
            if n == "send":
                out.append((kw["text"], kw["keyboard"]))
            elif n == "answer" and kw["message"] is not None:
                msg = kw["message"]
                atts = msg.get("attachments") or []
                out.append((msg.get("text"), atts[0] if atts else None))
        return out

    def texts(self) -> list[str]:
        return [t for t, _ in self.outgoing()]

    def last_text(self) -> str:
        return self.texts()[-1]

    def button(self, text: str) -> dict:
        """Последняя отправленная кнопка с таким текстом."""
        for _, kb in reversed(self.outgoing(everything=True)):
            for row in (kb or {}).get("payload", {}).get("buttons", []):
                for b in row:
                    if b["text"] == text:
                        return b
        raise AssertionError(f"button {text!r} not found")

    def clear(self) -> None:
        self._mark = len(self.calls)
