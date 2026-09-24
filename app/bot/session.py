"""Сессия диалога (состояние + данные сценария) в таблице sessions — переживает рестарт."""
from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.bot.states import S
from app.repo import Repo

log = logging.getLogger(__name__)
SCENARIO_TTL = timedelta(minutes=30)


def new_flow_id() -> str:
    return secrets.token_hex(3)  # 6 символов


@dataclass
class Session:
    user_id: int                       # users.id (внутренний)
    state: S = S.IDLE
    data: dict[str, Any] = field(default_factory=dict)
    flow_id: str = field(default_factory=new_flow_id)
    expires_at: datetime | None = None

    def new_flow(self) -> str:
        """Новый flow_id: кнопки прошлых сообщений сценария становятся устаревшими."""
        self.flow_id = new_flow_id()
        return self.flow_id

    def go(self, state: S, **data: Any) -> None:
        """Перейти в состояние, дописав данные."""
        self.state = state
        self.data.update(data)

    def reset(self) -> None:
        """В IDLE: данные очищаются, новый flow."""
        self.state = S.IDLE
        self.data = {}
        self.expires_at = None
        self.new_flow()

    def is_expired(self, now: datetime) -> bool:
        return self.state.is_scenario and self.expires_at is not None and self.expires_at < now


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


async def load_session(repo: Repo, user_id: int) -> Session:
    """Загружает сессию; нет или битая → IDLE."""
    row = await repo.get_session(user_id)
    if not row:
        return Session(user_id)
    try:
        data = json.loads(row["data"] or "{}")
        if not isinstance(data, dict):
            raise ValueError("data is not an object")
        return Session(user_id, S(row["state"]), data, row["flow_id"], _parse_ts(row["expires_at"]))
    except (ValueError, TypeError) as e:
        log.warning("broken session user=%s: %s — reset to IDLE", user_id, e)
        return Session(user_id)


async def save_session(repo: Repo, s: Session, now: datetime) -> None:
    """Сохраняет; для подачи/профиля продлевает TTL, регистрация и IDLE не истекают."""
    s.expires_at = now + SCENARIO_TTL if s.state.is_scenario else None
    await repo.save_session(s.user_id, s.state.value, s.data, s.flow_id, now, s.expires_at)
