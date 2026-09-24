"""Регистрация — ЗАГЛУШКА S0 (ФИО → телефон → готово). Поток S1 заменит файл целиком."""
from __future__ import annotations

from app.bot import keyboards as K
from app.bot import photos
from app.bot.ctx import Ctx
from app.bot.router import on_hook, on_repeat, on_state, show_menu
from app.bot.states import S
from app.bot.texts import common as C
from app.bot.texts import registration as T
from app.bot.texts.fmt import esc
from app.db import ts
from app.domain.people import first_name, normalize_phone, validate_name


@on_hook("registration.begin")
async def begin(ctx: Ctx) -> None:
    pending = ctx.data.get("pending_photo_id")
    ctx.session.reset()
    ctx.session.go(S.REG_NAME)
    if pending:
        ctx.data["pending_photo_id"] = pending
    await ctx.reply(C.WELCOME)
    await ask_name(ctx)


@on_repeat(S.REG_NAME)
async def ask_name(ctx: Ctx) -> None:
    await ctx.reply(T.ASK_NAME)


@on_state(S.REG_NAME)
async def got_name(ctx: Ctx) -> None:
    check = validate_name(ctx.text)
    if check.error:
        await ctx.reply(T.NAME_ERRORS.get(check.error, T.NAME_ERROR_DEFAULT))
        return
    ctx.session.go(S.REG_PHONE, reg={"name": check.value})
    await ask_phone(ctx)


@on_repeat(S.REG_PHONE)
async def ask_phone(ctx: Ctx) -> None:
    name = first_name(ctx.data.get("reg", {}).get("name"))
    await ctx.reply(T.ASK_PHONE.format(name=esc(name)), K.kb(
        [K.request_contact(T.BTN_SHARE_PHONE)],
        [ctx.btn(C.BTN_BACK, "back")],
    ))


@on_state(S.REG_PHONE, accepts=("contact",))
async def got_phone(ctx: Ctx) -> None:
    ev = ctx.event
    if ctx.action == "back" or K.text_alias(ctx.text) == "back":
        ctx.session.go(S.REG_NAME)
        await ask_name(ctx)
        return
    if ev.kind == "contact":
        if ev.contact_owner_id != ev.user_id or not ev.contact_phone:
            await ctx.reply(T.NOT_YOUR_CONTACT)
            return
        phone, verified = ev.contact_phone, True
    else:
        phone, verified = normalize_phone(ctx.text), False
        if not phone:
            await ctx.reply(T.PHONE_ERROR)
            return
    reg = ctx.data.get("reg", {})
    await ctx.repo.update_user(ctx.user["id"], full_name=reg.get("name"), phone=phone,
                               phone_verified=int(verified), registered_at=ts(ctx.now))
    ctx.user = await ctx.repo.get_user_by_id(ctx.user["id"])
    await photos.delete_photo(ctx.repo, ctx.data.get("pending_photo_id"))
    ctx.session.reset()
    ctx.note(T.DONE)
    await show_menu(ctx)
