"""Приглашение жильца собственником и управление доступом по адресу.

Собственник: [Пригласить жильца] (g|inv_new|<aid>) → одноразовая ссылка max.ru/<бот>?start=inv_<token>
на 7 дней (не больше 3 действующих на адрес). Открыл зарегистрированный → [Принять]/[Отказаться];
незарегистрированный → регистрация, токен в сессии (data["invite"]), адрес из приглашения предлагаем
на шаге адреса, после «Всё верно» — tenant/granted. Список доступа (g|acc_list) и отзыв с подтверждением
(g|acc_rev → g|acc_rev_ok): access=denied — связь с адресом остаётся, поданные показания тоже.
Из мини-приложения: start=inv_new_<aid> (создать приглашение), start=inv_acc_<aid> (список доступа).
"""
from __future__ import annotations

import logging
import secrets
import string
from datetime import UTC, datetime, timedelta

from app import clock
from app.bot import keyboards as K
from app.bot.ctx import Ctx
from app.bot.router import (
    call_hook, cancel_scenario, continue_registration, drop_scenario, on_global, on_hook, show_menu,
)
from app.bot.texts import common as C
from app.bot.texts import invite as T
from app.bot.texts import profile as PT
from app.bot.texts.fmt import day_month, esc
from app.domain.people import short_name
from app.integrations.max_api import MaxApiError
from app.repo import Row

log = logging.getLogger(__name__)
INVITE_TTL = timedelta(days=7)
INVITE_LIMIT = 3            # действующих приглашений на адрес
TOKEN_LEN = 20              # [a-z0-9]: без «_», чтобы inv_new_<aid> и inv_<token> не путались
_ALPHABET = string.ascii_lowercase + string.digits
DECISION_KIND = "access_decision"  # как в profile: старый запрос доступа уже решён — не дублировать ответ


def new_token() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(TOKEN_LEN))


def _menu_kb(*rows) -> dict:
    return K.kb(*rows, [K.gbtn(C.BTN_MENU, "menu")])


def _until(expires_at: str) -> str:
    """'2026-10-26 09:00:00' (UTC в БД) → '26 октября' по МСК."""
    dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).astimezone(clock.TZ)
    return day_month(dt.date())


async def check_invite(ctx: Ctx, token: str) -> tuple[Row | None, str | None]:
    """(приглашение, ошибка): ошибка None — действует; 'unknown' (приглашения нет), 'used', 'expired'."""
    token = (token or "").strip().lower()
    inv = await ctx.repo.get_invite(token) if token.isascii() and token.isalnum() and len(token) <= 64 else None
    if inv is None:
        return None, "unknown"
    if inv["used_at"]:
        return inv, "used"
    if datetime.strptime(inv["expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC) <= ctx.now:
        return inv, "expired"
    return inv, None


# === Собственник: адреса, приглашение, список доступа ===

async def _owned(ctx: Ctx, aid: int | None, action: str) -> Row | None:
    """Адрес собственника: по aid; без aid — единственный, иначе выбор адреса. Чужой — «только собственник»."""
    owned = [a for a in await ctx.repo.user_addresses(ctx.user["id"]) if a["role"] == "owner"]
    ua = next((a for a in owned if a["id"] == aid), None) if aid else (owned[0] if len(owned) == 1 else None)
    if ua:
        return ua
    if aid or not owned:
        await ctx.reply(T.OWNER_ONLY, _menu_kb([K.gbtn(PT.BTN_PROFILE, "profile")]))
    else:
        await ctx.reply(T.PICK_ADDRESS, _menu_kb(*[[K.gbtn(a["label"], action, a["id"])] for a in owned]))
    return None


async def _bot_username(ctx: Ctx) -> str:
    if not ctx.deps.bot_username and ctx.api is not None:
        try:
            ctx.deps.bot_username = (await ctx.api.get_me()).get("username") or ""
        except MaxApiError as e:
            log.warning("GET /me for invite link failed: %s", e)
    return ctx.deps.bot_username


@on_global("inv_new")
async def create_invite(ctx: Ctx, aid: int | None = None) -> None:
    ua = await _owned(ctx, aid or K.parse_id(ctx.arg), "inv_new")
    if ua is None:
        return
    manage = [K.gbtn(T.BTN_MANAGE, "acc_list", ua["id"])]
    bot = await _bot_username(ctx)
    if not bot:
        await ctx.reply(T.NO_USERNAME, _menu_kb(manage))
        return
    token = new_token()
    if not await ctx.repo.create_invite(token, ua["id"], ctx.user["id"], ctx.now, ctx.now + INVITE_TTL, INVITE_LIMIT):
        first = (await ctx.repo.active_invites(ua["id"], ctx.now))[0]
        await ctx.reply(T.INVITE_LIMIT.format(label=esc(ua["label"]), count=INVITE_LIMIT,
                                              until=_until(first["expires_at"])), _menu_kb(manage))
        return
    until = day_month((ctx.now + INVITE_TTL).date())
    # Ссылку — отдельным сообщением без разметки: его удобно переслать, «_» в имени бота не станет курсивом.
    await ctx.api.send(T.INVITE_LINK.format(address=ua["full_text"], url=f"https://max.ru/{bot}?start=inv_{token}",
                                            until=until), user_id=ctx.event.user_id, fmt=None)
    await ctx.reply(T.INVITE_CREATED.format(label=esc(ua["label"])), _menu_kb(manage))


def _fit(template: str, fallback: str, name: str, n: int) -> str:
    text = template.format(name=name)
    return text if len(text) <= 24 else fallback.format(n=n)


@on_global("acc_list")
async def show_members(ctx: Ctx, aid: int | None = None) -> None:
    ua = await _owned(ctx, aid or K.parse_id(ctx.arg), "acc_list")
    if ua is None:
        return
    invite = [K.gbtn(T.BTN_INVITE, "inv_new", ua["id"])]
    members = await ctx.repo.address_members(ua["id"])
    if not members:
        await ctx.reply(T.MEMBERS_EMPTY.format(label=esc(ua["label"])), _menu_kb(invite))
        return
    lines, rows = [], []
    for n, m in enumerate(members, 1):
        name = short_name(m["full_name"])
        lines.append(f"{n}. {esc(name)} — {T.STATUS[m['access']]}")
        arg = f"{m['user_id']}.{ua['id']}"
        if m["access"] == "granted":
            rows.append([K.gbtn(_fit(T.BTN_REVOKE, T.BTN_REVOKE_N, name, n), "acc_rev", arg)])
        elif m["access"] == "pending":  # тот же ответ, что на запрос доступа (profile.acc_ok)
            rows.append([K.gbtn(_fit(T.BTN_ALLOW, T.BTN_ALLOW_N, name, n), "acc_ok", arg)])
    await ctx.reply(T.MEMBERS.format(label=esc(ua["label"]), lines="\n".join(lines)), _menu_kb(*rows, invite))


def members_line(members: list[Row]) -> str:
    """Строка профиля собственника: «Доступ: Анна И., Пётр С. (ждёт)» или «Доступ: только вы»."""
    names = [esc(short_name(m["full_name"])) + (f" ({T.WAITS})" if m["access"] == "pending" else "")
             for m in members if m["access"] != "denied"]
    return T.ACCESS_LINE.format(names=", ".join(names) or T.ONLY_YOU)


# --- Отзыв ---

async def _member(ctx: Ctx) -> tuple[Row, Row, Row] | None:
    """(адрес собственника, пользователь, его связь с адресом) по arg «tid.aid»; ошибки — ответом."""
    tid_s, _, aid_s = ctx.arg.partition(".")
    tid, aid = K.parse_id(tid_s), K.parse_id(aid_s)
    ua = await _owned(ctx, aid or -1, "acc_list")
    if ua is None:
        return None
    back = _menu_kb([K.gbtn(T.BTN_MANAGE, "acc_list", ua["id"])])
    if tid == ctx.user["id"]:
        await ctx.reply(T.REVOKE_SELF, back)
        return None
    user = await ctx.repo.get_user_by_id(tid) if tid else None
    member = await ctx.repo.user_address(tid, ua["id"]) if user else None
    if member is None or member["role"] == "owner":
        await ctx.reply(T.REVOKE_GONE, back)
        return None
    if member["access"] == "denied":
        await ctx.reply(T.REVOKE_ALREADY.format(name=esc(short_name(user["full_name"]))), back)
        return None
    return ua, user, member


@on_global("acc_rev")
async def ask_revoke(ctx: Ctx) -> None:
    found = await _member(ctx)
    if found is None:
        return
    ua, user, _ = found
    await ctx.reply(T.REVOKE_ASK.format(name=esc(short_name(user["full_name"])), label=esc(ua["label"])), K.kb(
        [K.gbtn(T.BTN_REVOKE_YES, "acc_rev_ok", ctx.arg), K.gbtn(T.BTN_REVOKE_NO, "acc_list", ua["id"])]))


@on_global("acc_rev_ok")
async def revoke(ctx: Ctx) -> None:
    found = await _member(ctx)
    if found is None:
        return
    ua, user, member = found
    await ctx.clear_keyboard()
    await ctx.repo.set_access(user["id"], ua["id"], "denied")
    try:
        await ctx.api.send(T.TENANT_REVOKED.format(label=esc(member["label"])), user_id=user["max_user_id"],
                           keyboard=_menu_kb([K.gbtn(PT.BTN_PROFILE, "profile")]))
    except MaxApiError as e:
        log.warning("revoke notice to tenant failed: %s", e)
    await ctx.reply(T.REVOKED.format(name=esc(short_name(user["full_name"])), label=esc(ua["label"])),
                    _menu_kb([K.gbtn(T.BTN_MANAGE, "acc_list", ua["id"])]))


# === Приглашённый ===

async def _plain_start(ctx: Ctx) -> None:
    """Как обычный /start: регистрация, «продолжим» или меню."""
    if ctx.session.state.is_reg:
        await continue_registration(ctx)
    elif not ctx.registered:
        await drop_scenario(ctx)
        await call_hook("registration.begin", ctx)
    else:
        await cancel_scenario(ctx)
        await show_menu(ctx)


@on_hook("invite.start")
async def open_start(ctx: Ctx, arg: str = "", **_) -> None:
    """Диплинк inv_<token> / inv_new_<aid> / inv_acc_<aid>."""
    kind, _, rest = arg.partition("_")
    if kind.lower() in ("new", "acc"):
        if not ctx.registered or ctx.session.state.is_reg:
            await _plain_start(ctx)
            return
        await cancel_scenario(ctx)
        await (create_invite if kind.lower() == "new" else show_members)(ctx, K.parse_id(rest) or -1)
        return
    await open_invite(ctx, arg)


async def open_invite(ctx: Ctx, token: str) -> None:
    inv, err = await check_invite(ctx, token)
    if not ctx.registered:
        if ctx.session.state.is_reg:
            ctx.note(T.INVALID[err] if err else T.REG_KEPT)
        else:
            await drop_scenario(ctx)
            addr = await ctx.repo.get_address(inv["address_id"]) if not err else None
            ctx.note(T.INVALID[err] if err else T.REG_INVITED.format(address=esc(addr["full_text"])))
        if not err:
            ctx.data["invite"] = inv["token"]  # registration.begin его сохраняет, адрес предложим на шаге адреса
        if ctx.session.state.is_reg:
            await continue_registration(ctx)
        else:
            await call_hook("registration.begin", ctx)
        return
    await cancel_scenario(ctx)
    if err:
        await ctx.reply(T.INVALID[err], _menu_kb())
        return
    if await _already(ctx, inv):
        return
    owner = await ctx.repo.get_user_by_id(inv["owner_user_id"])
    addr = await ctx.repo.get_address(inv["address_id"])
    await ctx.reply(T.OFFER.format(owner=esc(short_name(owner["full_name"])), address=esc(addr["full_text"])),
                    K.kb([K.gbtn(T.BTN_ACCEPT, "inv_ok", inv["token"]), K.gbtn(T.BTN_DECLINE, "inv_no", inv["token"])]))


async def _already(ctx: Ctx, inv: Row) -> bool:
    """Собственник открыл свою ссылку или доступ уже есть — ответить и не тратить приглашение."""
    ua = await ctx.repo.user_address(ctx.user["id"], inv["address_id"])
    if ua and ua["role"] == "owner":
        await ctx.reply(T.SELF.format(label=esc(ua["label"])), _menu_kb([K.gbtn(T.BTN_MANAGE, "acc_list", ua["id"])]))
    elif ua and ua["access"] == "granted":
        await ctx.reply(T.ALREADY.format(label=esc(ua["label"])), _menu_kb([K.gbtn(PT.BTN_SUBMIT, "submit")]))
    else:
        return False
    return True


@on_global("inv_ok")
async def accept(ctx: Ctx) -> None:
    await ctx.clear_keyboard()
    inv, err = await check_invite(ctx, ctx.arg)
    if err:
        await ctx.reply(T.INVALID[err], _menu_kb())
        return
    if await _already(ctx, inv) or not await use_invite(ctx, inv):
        return
    ua = await ctx.repo.user_address(ctx.user["id"], inv["address_id"])
    await ctx.reply(T.ACCEPTED.format(label=esc(ua["label"])), _menu_kb([K.gbtn(PT.BTN_SUBMIT, "submit")]))


@on_global("inv_no")
async def decline(ctx: Ctx) -> None:
    await ctx.clear_keyboard()
    await ctx.reply(T.DECLINED, _menu_kb())


async def use_invite(ctx: Ctx, inv: Row) -> bool:
    """Принять приглашение (доступ granted) и сообщить собственнику. False — уже не действует."""
    u = ctx.user
    aid = await ctx.repo.use_invite(inv["token"], u["id"], ctx.now)
    if aid is None:
        return False
    await ctx.repo.try_mark_sent(u["id"], DECISION_KIND, f"{u['id']}:{aid}", ctx.now)
    owner = await ctx.repo.get_user_by_id(inv["owner_user_id"])
    owner_ua = await ctx.repo.user_address(owner["id"], aid) if owner else None
    if owner_ua is not None:
        try:
            await ctx.api.send(
                T.OWNER_ACCEPTED.format(name=esc(short_name(u["full_name"])), label=esc(owner_ua["label"])),
                user_id=owner["max_user_id"], keyboard=_menu_kb([K.gbtn(T.BTN_MANAGE, "acc_list", aid)]))
        except MaxApiError as e:
            log.warning("invite accepted notice to owner failed: %s", e)
    return True


async def accept_after_registration(ctx: Ctx, address_id: int) -> str | None:
    """«Всё верно» с токеном в сессии. Адрес совпал с приглашением → доступ открыт ('accepted');
    приглашение истекло или использовано → 'expired'; адрес другой или токена нет — None (приглашение не тратим)."""
    inv, err = await check_invite(ctx, ctx.data.get("invite", ""))
    if inv is None or inv["address_id"] != address_id:
        return None
    return "accepted" if not err and await use_invite(ctx, inv) else "expired"
