"""Контекст обработки одного события: зависимости, событие, сессия, пользователь, ответы."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.bot import keyboards as K
from app.bot.events import Event
from app.bot.session import Session
from app.config import Settings
from app.integrations.max_api import MaxApi, MaxApiError, message_body
from app.integrations.recognizer import Recognizer
from app.repo import Repo, Row

log = logging.getLogger(__name__)


@dataclass
class Deps:
    """Общие зависимости бота (одни на процесс)."""
    api: MaxApi                # None, если BOT_TOKEN не задан (работает только веб-часть)
    repo: Repo
    settings: Settings
    recognizer: Recognizer
    addresses: Any = None      # AddressService (поток S3); None — сервис не подключён
    bot_username: str = ""     # для кнопок open_app


@dataclass
class Ctx:
    deps: Deps
    event: Event
    session: Session
    user: Row                  # строка users
    now: datetime              # aware, МСК (app.clock)
    action: str | None = None  # действие кнопки (или распознанный «да/нет/назад» в кнопочном шаге)
    arg: str = ""
    answered: bool = False     # на callback уже ответили (дальше — только новые сообщения)
    drop_session: bool = False  # не сохранять сессию (например, данные пользователя удалены)
    _notes: list[str] = field(default_factory=list)

    # --- Ярлыки ---
    @property
    def api(self) -> MaxApi:
        return self.deps.api

    @property
    def repo(self) -> Repo:
        return self.deps.repo

    @property
    def settings(self) -> Settings:
        return self.deps.settings

    @property
    def state(self):
        return self.session.state

    @property
    def data(self) -> dict:
        return self.session.data

    @property
    def text(self) -> str:
        return (self.event.text or "").strip()

    @property
    def registered(self) -> bool:
        return bool(self.user.get("registered_at"))

    @property
    def is_callback(self) -> bool:
        return self.event.kind == "callback"

    # --- Кнопки ---
    def btn(self, text: str, action: str, arg: str | int = "") -> K.Button:
        """Кнопка текущего сценария (flow_id сессии)."""
        return K.callback(text, self.session.flow_id, action, arg)

    def app_btn(self, text: str, payload: str | None = None) -> K.Button | None:
        """Кнопка мини-приложения; None, если username бота неизвестен."""
        return K.open_app(text, self.deps.bot_username, payload)

    # --- Ответы ---
    def note(self, line: str) -> None:
        """Строка, которая добавится в начало следующего сообщения (например, «Взяли новое фото»)."""
        self._notes.append(line)

    def _with_notes(self, text: str) -> str:
        if not self._notes:
            return text
        head = "\n".join(self._notes)
        self._notes.clear()
        return f"{head}\n\n{text}" if text else head

    async def reply(self, text: str, keyboard: dict | None = None, *, new: bool = False) -> str | None:
        """Ответ пользователю. На callback — замена сообщения с кнопкой (POST /answers),
        иначе (или new=True, или уже ответили) — новое сообщение. → mid нового сообщения или None."""
        text = self._with_notes(text)
        ev = self.event
        if ev.kind == "callback" and not self.answered and not new and ev.message_mid:
            self.answered = True
            await self.api.answer(ev.callback_id, message=message_body(text, keyboard, clear_keyboard=True))
            return None
        return await self.send(text, keyboard)

    async def send(self, text: str, keyboard: dict | None = None) -> str | None:
        """Всегда новое сообщение. → mid."""
        msg = await self.api.send(self._with_notes(text), user_id=self.event.user_id, keyboard=keyboard)
        return (msg.get("body") or {}).get("mid")

    async def flush_notes(self) -> None:
        """Отправить накопленные note() отдельным сообщением, если они никуда не попали."""
        if self._notes:
            await self.send("")

    async def toast(self, text: str) -> None:
        """Всплывающее уведомление на нажатие кнопки (если ещё не ответили)."""
        if self.is_callback and not self.answered:
            self.answered = True
            await self.api.answer(self.event.callback_id, notification=text)

    async def ack(self) -> None:
        """Ответить на callback без изменений; дальнейшие reply() пойдут новыми сообщениями."""
        if self.is_callback and not self.answered:
            self.answered = True
            try:
                await self.api.answer(self.event.callback_id)
            except MaxApiError as e:
                log.warning("empty callback answer failed: %s", e)

    async def clear_keyboard(self) -> None:
        """Убрать кнопки у сообщения, на которое нажали (для устаревших кнопок)."""
        if self.is_callback and self.event.message_mid:
            try:
                await self.api.edit(self.event.message_mid, keyboard=None)
            except MaxApiError as e:
                log.info("clear keyboard failed: %s", e)

    async def typing(self) -> None:
        if self.event.chat_id:
            try:
                await self.api.typing(self.event.chat_id)
            except MaxApiError:
                pass
