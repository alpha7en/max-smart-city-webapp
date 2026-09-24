"""Профиль — ЗАГЛУШКА S0. Поток S1 заменит файл целиком."""
from __future__ import annotations

from app.bot import keyboards as K
from app.bot.ctx import Ctx
from app.bot.router import on_global, on_hook, show_menu
from app.bot.texts import common as C
from app.bot.texts import profile as T
from app.bot.texts.fmt import esc, format_phone


@on_global("profile")
async def show_profile(ctx: Ctx) -> None:
    u = ctx.user
    await ctx.reply(
        T.PROFILE.format(name=esc(u.get("full_name")), phone=format_phone(u.get("phone"))),
        K.kb([K.gbtn(C.BTN_MENU, "menu")]),
    )


# Заглушка точки входа — поток S1 заменит её.
@on_hook("access.no_access")
async def _stub_no_access(ctx: Ctx, address_id: int | None = None, **_) -> None:
    await show_menu(ctx)
