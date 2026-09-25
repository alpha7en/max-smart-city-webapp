"""Главное меню = короткий дашборд + кнопки (SPEC §5.7), «Мои счётчики», заглушки поверки и оплаты.

Кнопки меню глобальные (g|…): работают из любого состояния и отвечают новым сообщением.
"""
from __future__ import annotations

import logging
from datetime import date

from app.bot import keyboards as K
from app.bot.ctx import Ctx
from app.bot.router import call_hook, on_global, on_hook, on_state
from app.bot.states import S
from app import arshin_service as AS
from app.bot.texts import arshin as TA
from app.bot.texts import common as C
from app.bot.texts import menu as T
from app.bot.texts import notify as N
from app.bot.texts.fmt import esc, full_date, short_date
from app.domain.dashboard import (
    VERIFICATION_SHOW_DAYS,
    Dashboard,
    Urgent,
    load_dashboard,
    local_time,
    meter_names,
)
from app.domain.meters import days_left, field_labels, format_value, spec

log = logging.getLogger(__name__)

# Действия кнопок меню (g|action|arg).
SUBMIT, METERS, PROFILE, ADD_METER, VERIFY, PAY, MENU = (
    "submit", "meters", "profile", "add_meter", "verify", "pay", "menu",
)
ANTIFRAUD_DAYS = 365
URGENT_ACTIONS = {"verification": VERIFY, "bill": PAY, "submit": SUBMIT}


async def dashboard(ctx: Ctx) -> Dashboard:
    s = ctx.settings
    return await load_dashboard(ctx.repo, ctx.user["id"], ctx.now.date(), s.submit_day_from, s.submit_day_to)


async def app_button(ctx: Ctx) -> K.Button | None:
    """Кнопка мини-приложения; username бота — из настроек или GET /me (запоминаем в deps)."""
    if not ctx.deps.bot_username and ctx.api is not None:
        try:
            ctx.deps.bot_username = (await ctx.api.get_me()).get("username") or ""
        except Exception as e:  # noqa: BLE001 — меню покажем без кнопки
            log.warning("GET /me for open_app failed: %s", e)
    return ctx.app_btn(C.BTN_MINIAPP)


def urgent_button(u: Urgent) -> K.Button:
    return K.gbtn(u.text, URGENT_ACTIONS[u.kind], u.ref or "")


def render(d: Dashboard) -> str:
    """Строки дашборда → markdown: пользовательское экранируем, срочное — жирным."""
    lines = [esc(x) for x in d.lines]
    if d.urgent:
        lines[0] = f"**{lines[0]}**"
    return "\n".join(lines)


@on_hook("menu")
async def send_menu(ctx: Ctx, header: str | None = None, **_) -> None:
    """Меню-дашборд; сессия → IDLE. header (или ctx.note) — строка над дашбордом."""
    ctx.session.reset()
    if header:
        ctx.note(header)
    d = await dashboard(ctx)
    await ctx.reply(render(d), K.kb(
        urgent_button(d.urgent) if d.urgent else None,
        None if d.urgent and d.urgent.kind == "submit" else K.gbtn(T.BTN_SUBMIT, SUBMIT),
        [K.gbtn(T.BTN_METERS, METERS), K.gbtn(T.BTN_PROFILE, PROFILE)],
        await app_button(ctx),
    ))


def back_to_menu() -> dict:
    return K.kb(K.gbtn(C.BTN_MENU, MENU))


@on_global(MENU)
async def menu_button(ctx: Ctx) -> None:
    await send_menu(ctx)


@on_state(S.IDLE)
async def idle_text(ctx: Ctx) -> None:
    """Свободный текст вне сценариев — меню (без эха введённого)."""
    await send_menu(ctx)


@on_global(SUBMIT)
async def submit(ctx: Ctx) -> None:
    await call_hook("submission.start", ctx)


@on_global(ADD_METER)
async def add_meter(ctx: Ctx) -> None:
    await call_hook("submission.add_meter", ctx)


@on_global(VERIFY)
async def verify(ctx: Ctx) -> None:
    """Запись на поверку — честная заглушка. arg — meter_id: кнопка могла устареть."""
    if ctx.arg:
        mid = K.parse_id(ctx.arg)
        m = await ctx.repo.user_meter(ctx.user["id"], mid) if mid else None
        if m is None:
            await send_menu(ctx, header=N.METER_GONE)
            return
        due = m.get("verification_due")
        if not due or days_left(ctx.now.date(), date.fromisoformat(due)) > VERIFICATION_SHOW_DAYS:
            await send_menu(ctx, header=N.VERIFICATION_UPDATED)
            return
    await ctx.reply(T.VERIFICATION_STUB, back_to_menu())


@on_global(PAY)
async def pay(ctx: Ctx) -> None:
    """Оплата — честная заглушка. arg — bill_id: счёт могли уже оплатить."""
    if ctx.arg:
        bid = K.parse_id(ctx.arg)
        bill = await ctx.repo.get_bill(bid) if bid else None
        if bill is None or bill["status"] == "paid":
            await send_menu(ctx, header=N.ALREADY_PAID)
            return
    await ctx.reply(T.PAY_STUB, back_to_menu())


def _values(m: dict) -> str:
    """Последнее показание: '123,456 м³' или 'Т1 день 1234,56 · Т2 ночь 234,50 кВт·ч'."""
    labels = field_labels(m["type"], m["tariffs"])
    parts = [
        (f"{label} " if label else "") + format_value(m[f"last_{f}"], m["type"])
        for f, label in labels.items() if m.get(f"last_{f}") is not None
    ]
    return f"{' · '.join(parts)} {spec(m['type']).unit}"


def _antifraud(meters: list[dict], today: date) -> str | None:
    """Все сроки поверки дальше года — напоминаем о листовках «срочной поверки» (без запугивания)."""
    dues = [date.fromisoformat(m["verification_due"]) for m in meters if m.get("verification_due")]
    if not dues or len(dues) < len(meters) or days_left(today, min(dues)) <= ANTIFRAUD_DAYS:
        return None
    tpl = TA.ANTIFRAUD if len(dues) == 1 else TA.ANTIFRAUD_MANY
    return tpl.format(date=full_date(min(dues)))


@on_global(METERS)
async def my_meters(ctx: Ctx) -> None:
    """Список счётчиков: тип · адрес, последнее показание, поверка."""
    meters = await ctx.repo.user_meters(ctx.user["id"])
    addresses = await ctx.repo.user_addresses(ctx.user["id"])
    names = meter_names(meters)
    blocks = []
    for m in meters:
        if m.get("last_id"):
            when = local_time(m.get("last_created_at"))
            info = T.METERS_LAST.format(value=_values(m), date=short_date(when.date()) if when else m["last_period"])
        else:
            info = T.METERS_NO_READINGS
        if m.get("verification_due"):
            info += ", " + T.METERS_VERIFICATION.format(date=full_date(date.fromisoformat(m["verification_due"])))
            if source := AS.source_label(m):
                info += TA.METERS_SOURCE.format(source=source)
        blocks.append(f"{esc(names[m['id']])}\n{info}")
    text = T.METERS_TITLE + "\n\n" + "\n\n".join(blocks) if meters else T.NO_METERS
    if line := _antifraud(meters, ctx.now.date()):
        text += "\n\n" + line
    if any(a["access"] == "pending" for a in addresses):
        text += "\n\n" + T.METERS_PENDING
    await ctx.reply(text, K.kb([K.gbtn(T.BTN_ADD_METER, ADD_METER), K.gbtn(C.BTN_MENU, MENU)]))
