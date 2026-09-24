"""Фоновые задачи: sweeper (фото, сессии) и уведомления.

Уведомления (S4): срок подачи, поверка, демо-счёт; отправка только 9:00–21:00 МСК,
не больше одного уведомления пользователю за тик, дедуп через repo.try_mark_sent.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from app import clock
from app.bot import photos
from app.bot.ctx import Deps

log = logging.getLogger(__name__)
TICK_SECONDS = 60
NOTIFY_EVERY = 10          # тиков (≈10 минут)
NOTIFY_HOURS = range(9, 21)


async def sweep(deps: Deps, now: datetime) -> None:
    """Удаляет просроченные фото. Просроченные сессии сбрасывает роутер при следующем событии."""
    await photos.sweep(deps.repo, now)


async def notify_tick(deps: Deps, now: datetime) -> int:
    """Разослать положенные уведомления зарегистрированным. Сбой у одного не прерывает рассылку.
    → сколько отправили."""
    from app.bot.flows.notify import send_due_notice  # флоу импортируем лениво: они тянут роутер

    if now.hour not in NOTIFY_HOURS:
        return 0
    sent = 0
    for user in await deps.repo.registered_users():
        try:
            sent += await send_due_notice(deps, user, now)
        except Exception:
            log.exception("notification failed user=%s", user["id"])
    return sent


async def run_scheduler(deps: Deps) -> None:
    tick = 0
    while True:
        now = clock.now()
        try:
            await sweep(deps, now)
            if deps.api is not None and tick % NOTIFY_EVERY == 0 and now.hour in NOTIFY_HOURS:
                await notify_tick(deps, now)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduler tick failed")
        tick += 1
        await asyncio.sleep(TICK_SECONDS)
