"""Подача показаний — ЗАГЛУШКА S0 (инструкция + приём фото). Поток S2 заменит файл целиком."""
from __future__ import annotations

from app.bot import keyboards as K
from app.bot import photos
from app.bot.ctx import Ctx
from app.bot.router import cancel_scenario, on_global, on_hook, on_repeat, on_state, show_menu
from app.bot.states import S
from app.bot.texts import common as C
from app.bot.texts import submission as T


@on_global("submit")
async def start(ctx: Ctx) -> None:
    ctx.session.go(S.SUB_AWAIT_PHOTO)
    await ask_photo(ctx)


@on_repeat(S.SUB_AWAIT_PHOTO)
async def ask_photo(ctx: Ctx) -> None:
    await ctx.reply(T.INSTRUCTION, K.kb(
        [ctx.app_btn(C.BTN_MINIAPP)],
        [ctx.btn(C.BTN_CANCEL, "cancel")],
    ))


@on_state(S.SUB_AWAIT_PHOTO, buttons=True)
async def await_photo(ctx: Ctx) -> None:
    if ctx.action in ("cancel", "no", "back"):
        await cancel_scenario(ctx, by_user=True)
        await show_menu(ctx)
    else:
        await ask_photo(ctx)


@on_hook("submission.photo")
async def on_photo(ctx: Ctx) -> None:
    await photos.delete_photo(ctx.repo, ctx.data.get("photo_id"))
    try:
        pid = await photos.download_to_tmp(ctx.api, ctx.repo, ctx.settings.photos_dir, ctx.user["id"],
                                           ctx.event.photo_url or "", ctx.now)
    except photos.PhotoError:
        ctx.session.go(S.SUB_AWAIT_PHOTO, photo_id=None)
        await ctx.reply(T.PHOTO_FAILED)
        return
    ctx.session.go(S.SUB_AWAIT_PHOTO, photo_id=pid)
    await ctx.reply(T.PHOTO_RECEIVED, K.kb([ctx.btn(C.BTN_CANCEL, "cancel")]))
