"""Уведомления и /demo — ЗАГЛУШКА S0. Поток S4 заменит файл целиком
(кнопки уведомлений — глобальные: @on_global("notify_..."))."""
from __future__ import annotations

from app.bot import keyboards as K
from app.bot.ctx import Ctx
from app.bot.router import on_command
from app.bot.texts import common as C
from app.bot.texts import notify as T


@on_command("/demo")
async def demo(ctx: Ctx) -> None:
    await ctx.reply(T.DEMO_SOON, K.kb([K.gbtn(C.BTN_MENU, "menu")]))
