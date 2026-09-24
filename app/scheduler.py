"""Фоновые задачи: sweeper (фото, сессии) и уведомления.

Уведомления — КАРКАС S0: notify_tick() заполняет поток S4 (срок подачи, поверка, демо-счёт;
отправка только 9:00–21:00 МСК, дедуп через repo.try_mark_sent).
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


async def notify_tick(deps: Deps, now: datetime) -> None:
    """Разослать положенные уведомления. Реализует поток S4."""


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
