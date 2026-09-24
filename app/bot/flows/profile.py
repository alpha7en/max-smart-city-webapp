"""Профиль (SPEC §5.7), «нет прав» (§5.8) и одобрение доступа собственником (C10).

Кнопки профиля и доступа — глобальные (g|…): работают из любого сообщения.
"""
from __future__ import annotations

import logging

from app.bot import keyboards as K
from app.bot.ctx import Ctx
from app.bot.flows.registration import AddressFlow, address_notes, parse_phone, phone_line, said
from app.bot.router import drop_scenario, on_global, on_hook, on_repeat, on_state, show_menu
from app.bot.states import S
from app.bot.texts import common as C
from app.bot.texts import profile as T
from app.bot.texts import registration as RT
from app.bot.texts.fmt import esc
from app.domain.addresses import AddressCandidate, norm_key
from app.integrations.max_api import MaxApiError
from app.repo import Row

log = logging.getLogger(__name__)
ACCESS_KIND = "access"  # notifications.kind для запросов доступа; dedup_key = "{uid}:{aid}"


def _menu_kb(*rows) -> dict:
    return K.kb(*rows, [K.gbtn(C.BTN_MENU, "menu")])


# === Профиль ===

@on_global("profile")
async def show_profile(ctx: Ctx) -> None:
    ctx.session.reset()
    u = ctx.user
    addrs = await ctx.repo.user_addresses(u["id"])
    lines = [f"{esc(a['label'])} — {T.ROLE.get((a['role'], a['access']), a['access'])}" for a in addrs]
    pending = [a for a in addrs if a["access"] == "pending"]
    req = [K.gbtn(T.BTN_REQUEST if len(pending) == 1 else T.BTN_REQUEST_FOR.format(label=a["label"]),
                  "acc_req", a["id"]) for a in pending]
    await ctx.reply(
        T.PROFILE.format(name=esc(u.get("full_name")), phone=phone_line(u.get("phone"), u.get("phone_verified")),
                         addresses="\n".join(lines) or T.NO_ADDRESSES),
        _menu_kb([K.gbtn(T.BTN_EDIT_PHONE, "prof_phone"), K.gbtn(T.BTN_ADD_ADDRESS, "prof_addr")],
                 *req, [K.gbtn(T.BTN_DELETE, "prof_del")]),
    )


async def _cancel(ctx: Ctx) -> None:
    ctx.note(C.PROFILE_CANCELLED_BY_USER)
    await show_profile(ctx)


# --- Телефон ---

@on_global("prof_phone")
async def edit_phone(ctx: Ctx) -> None:
    ctx.session.go(S.PROFILE_PHONE)
    await ask_phone(ctx)


def _phone_kb(ctx: Ctx) -> dict:
    return K.kb([K.request_contact(RT.BTN_SHARE_PHONE)], [ctx.btn(C.BTN_CANCEL, "back")])


@on_repeat(S.PROFILE_PHONE)
async def ask_phone(ctx: Ctx) -> None:
    u = ctx.user
    await ctx.reply(T.ASK_PHONE.format(phone=phone_line(u.get("phone"), u.get("phone_verified"))), _phone_kb(ctx))


@on_state(S.PROFILE_PHONE, accepts=("contact",))
async def got_phone(ctx: Ctx) -> None:
    if said(ctx) in ("back", "no"):
        await _cancel(ctx)
        return
    if ctx.is_callback:
        await ask_phone(ctx)
        return
    phone, verified, error = parse_phone(ctx)
    if error:
        await ctx.reply(error, _phone_kb(ctx))
        return
    await ctx.repo.update_user(ctx.user["id"], phone=phone, phone_verified=int(verified))
    ctx.user = await ctx.repo.get_user_by_id(ctx.user["id"])
    ctx.note(T.PHONE_SAVED)
    await show_profile(ctx)


# --- Адрес ---

@on_global("prof_addr")
async def add_address(ctx: Ctx) -> None:
    await PROFILE_ADDRESS.start(ctx)


async def _address_chosen(ctx: Ctx, c: AddressCandidate) -> None:
    raw = ctx.data.pop("addr_raw", None)
    notes = address_notes(ctx, c, ctx.data.pop("addr_shown", []))
    uid, key = ctx.user["id"], norm_key(c)
    dup = next((a for a in await ctx.repo.user_addresses(uid) if a["norm_key"] == key), None)
    if dup:
        ctx.note(T.ADDRESS_DUP.format(label=esc(dup["label"])))
        await show_profile(ctx)
        return
    res = await ctx.repo.add_user_address(uid, c.to_dict(), key, raw, ctx.now.date())
    ctx.note(T.ADDRESS_SAVED.format(label=esc(res["label"])))
    for n in notes:
        ctx.note(n)
    if res["access"] == "granted":
        await show_profile(ctx)
    else:
        await send_no_access(ctx, res["address_id"])


PROFILE_ADDRESS = AddressFlow(
    S.PROFILE_ADDR_INPUT, S.PROFILE_ADDR_PICK, S.PROFILE_ADDR_FLAT, ask=T.ASK_ADDRESS, back_text=C.BTN_CANCEL,
    on_chosen=_address_chosen, on_back=_cancel,
)


# --- Удаление данных (A1) ---

@on_global("prof_del")
async def delete_data(ctx: Ctx) -> None:
    ctx.session.go(S.PROFILE_DELETE_CONFIRM)
    await ask_delete(ctx)


@on_repeat(S.PROFILE_DELETE_CONFIRM)
async def ask_delete(ctx: Ctx) -> None:
    await ctx.reply(T.DELETE_ASK, K.kb([ctx.btn(T.BTN_DELETE_YES, "delete"), ctx.btn(T.BTN_DELETE_NO, "keep")]))


@on_state(S.PROFILE_DELETE_CONFIRM, buttons=True)
async def got_delete(ctx: Ctx) -> None:
    if ctx.action in ("delete", "yes"):
        await ctx.repo.delete_user_data(ctx.user["id"])  # сессия, фото с файлами, адреса, пользователь
        ctx.drop_session = True
        await ctx.reply(T.DELETED, K.kb([K.gbtn(T.BTN_START_OVER, "menu")]))
    elif ctx.action in ("keep", "no", "back"):
        ctx.note(T.DELETE_KEPT)
        await show_profile(ctx)
    else:
        await ask_delete(ctx)


# === Нет прав (§5.8) ===

@on_hook("access.no_access")
async def send_no_access(ctx: Ctx, address_id: int | None = None, **_) -> None:
    """Сообщение «нет прав» по адресу; сценарий сбрасывается (фото подачи удаляется)."""
    await drop_scenario(ctx)
    ua = await ctx.repo.user_address(ctx.user["id"], address_id) if address_id else None
    if ua is None or ua["access"] == "granted":
        await show_menu(ctx)
        return
    if ua["access"] == "denied":
        await ctx.reply(T.NO_ACCESS_DENIED.format(label=esc(ua["label"])), _menu_kb([K.gbtn(T.BTN_PROFILE, "profile")]))
        return
    await ctx.reply(T.NO_ACCESS.format(label=esc(ua["label"])), K.kb(
        [K.gbtn(T.BTN_REQUEST, "acc_req", ua["id"])],
        [K.gbtn(T.BTN_PROFILE, "profile"), K.gbtn(C.BTN_MENU, "menu")],
    ))


# === Запрос доступа у собственника (C10) ===

def _int(value: str) -> int | None:
    return int(value) if value.isdigit() else None


@on_global("acc_req")
async def request_access(ctx: Ctx) -> None:
    u, aid = ctx.user, _int(ctx.arg)
    ua = await ctx.repo.user_address(u["id"], aid) if aid else None
    if ua is None:
        await ctx.reply(T.ACCESS_UNKNOWN, _menu_kb())
        return
    if ua["access"] == "denied":
        await send_no_access(ctx, aid)
        return
    if ua["access"] == "granted":
        await _granted(ctx, ua)
        return
    owner = await ctx.repo.address_owner(aid)
    if owner is None:  # собственник удалил свои данные — модель прав отдаёт адрес первому
        await ctx.repo.claim_address(u["id"], aid)
        await ctx.reply(T.ACCESS_CLAIMED.format(label=esc(ua["label"])), _menu_kb([K.gbtn(T.BTN_SUBMIT, "submit")]))
        return
    key = f"{u['id']}:{aid}"
    if not await ctx.repo.try_mark_sent(u["id"], ACCESS_KIND, key, ctx.now):
        await ctx.reply(T.REQUEST_DUP, _menu_kb())
        return
    owner_ua = await ctx.repo.user_address(owner["id"], aid)
    arg = f"{u['id']}.{aid}"
    try:
        await ctx.api.send(
            T.OWNER_REQUEST.format(name=esc(u.get("full_name")), label=esc(owner_ua["label"]),
                                   phone=phone_line(u.get("phone"), u.get("phone_verified"))),
            user_id=owner["max_user_id"],
            keyboard=K.kb([K.gbtn(T.BTN_ALLOW, "acc_ok", arg), K.gbtn(T.BTN_DENY, "acc_no", arg)]),
        )
    except MaxApiError as e:
        log.warning("access request to owner failed: %s", e)
        await ctx.repo.unmark_sent(u["id"], ACCESS_KIND, key)
        await ctx.reply(T.REQUEST_FAILED, _menu_kb([K.gbtn(C.BTN_RETRY, "acc_req", aid)]))
        return
    await ctx.reply(T.REQUEST_SENT, _menu_kb())


async def _granted(ctx: Ctx, ua: Row) -> None:
    await ctx.reply(T.ACCESS_ALREADY.format(label=esc(ua["label"])), _menu_kb([K.gbtn(T.BTN_SUBMIT, "submit")]))


@on_global("acc_ok")
async def allow_access(ctx: Ctx) -> None:
    await _decide(ctx, grant=True)


@on_global("acc_no")
async def deny_access(ctx: Ctx) -> None:
    await _decide(ctx, grant=False)


async def _decide(ctx: Ctx, *, grant: bool) -> None:
    """Ответ собственника: жать может только owner адреса; повторное нажатие — «уже решено»."""
    tid_s, _, aid_s = ctx.arg.partition(".")
    tid, aid = _int(tid_s), _int(aid_s)
    owner = await ctx.repo.address_owner(aid) if aid else None
    if owner is None or owner["id"] != ctx.user["id"]:
        await ctx.reply(T.OWNER_ONLY, _menu_kb())
        return
    tenant = await ctx.repo.get_user_by_id(tid) if tid else None
    tenant_ua = await ctx.repo.user_address(tid, aid) if tenant else None
    await ctx.clear_keyboard()
    if tenant_ua is None:
        await ctx.reply(T.REQUEST_GONE, _menu_kb())
        return
    if tenant_ua["access"] != "pending":
        await ctx.reply(T.DECIDED[tenant_ua["access"]], _menu_kb())
        return
    await ctx.repo.set_access(tid, aid, "granted" if grant else "denied")
    owner_label = esc((await ctx.repo.user_address(owner["id"], aid))["label"])
    name = esc(tenant.get("full_name"))
    await ctx.reply((T.OWNER_GRANTED if grant else T.OWNER_DENIED).format(name=name, label=owner_label), _menu_kb())
    if grant:
        text, kb = T.TENANT_GRANTED, _menu_kb([K.gbtn(T.BTN_SUBMIT, "submit")])
    else:
        text, kb = T.TENANT_DENIED, _menu_kb([K.gbtn(T.BTN_PROFILE, "profile")])
    try:
        await ctx.api.send(text.format(label=esc(tenant_ua["label"])), user_id=tenant["max_user_id"], keyboard=kb)
    except MaxApiError as e:
        log.warning("access decision to tenant failed: %s", e)
