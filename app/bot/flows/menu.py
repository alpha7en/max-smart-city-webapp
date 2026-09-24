"""Меню — ЗАГЛУШКА S0. Поток S4 заменит файл целиком (дашборд, срочная кнопка)."""
from __future__ import annotations

from app.bot import keyboards as K
from app.bot.ctx import Ctx
from app.bot.router import on_global, on_hook, on_state
from app.bot.states import S
from app.bot.texts import common as C
from app.bot.texts import menu as T


@on_hook("menu")
async def show_menu(ctx: Ctx) -> None:
    ctx.session.reset()
    await ctx.reply(T.MENU, K.kb(
        [K.gbtn(T.BTN_SUBMIT, "submit")],
        [K.gbtn(T.BTN_PROFILE, "profile")],
        [ctx.app_btn(C.BTN_MINIAPP)],
    ))


@on_global("menu")
async def menu_button(ctx: Ctx) -> None:
    await show_menu(ctx)


@on_state(S.IDLE)
async def idle_text(ctx: Ctx) -> None:
    """Свободный текст вне сценариев — показываем меню."""
    await show_menu(ctx)
