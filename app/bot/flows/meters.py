"""Карточка счётчика и его удаление (из «Мои счётчики»).

Кнопки глобальные и несут id счётчика: g|meter|id (карточка), g|m_sub|id (подать по нему),
g|m_del|id (вопрос «удалить?»), g|m_del_ok|id (удалить). Каждое нажатие заново читает счётчик из БД,
поэтому устаревшая или повторная кнопка безопасна: удалённого счётчика нет — «его уже нет» + список.
Удаление мягкое (repo.delete_meter, active=0): показания остаются в БД. Удалять может пользователь
с доступом granted; если у адреса есть другие жильцы с доступом — только собственник.
"""
from __future__ import annotations

from datetime import date

from app import arshin_service as AS
from app.bot import keyboards as K
from app.bot.ctx import Ctx
from app.bot.flows.menu import MENU, METER_CARD, METERS, last_values, my_meters
from app.bot.router import call_hook, on_global
from app.bot.texts import common as C
from app.bot.texts import meters as T
from app.bot.texts.fmt import esc, full_date, short_date
from app.domain.access import meter_delete_denial
from app.domain.dashboard import local_time, meter_names
from app.domain.meters import format_serial
from app.repo import Row

SUBMIT, DELETE, DELETE_OK = "m_sub", "m_del", "m_del_ok"


async def _find(ctx: Ctx) -> tuple[Row, str] | None:
    """Счётчик из ctx.arg среди активных счётчиков пользователя (granted) и его подпись как в подаче."""
    mid = K.parse_id(ctx.arg)
    meters = await ctx.repo.user_meters(ctx.user["id"]) if mid else []
    m = next((x for x in meters if x["id"] == mid), None)
    return (m, meter_names(meters)[m["id"]]) if m else None


async def _gone(ctx: Ctx) -> None:
    ctx.note(T.GONE)
    await my_meters(ctx)


def _verification(m: Row) -> tuple[str, str | None]:
    """Строка поверки и оговорка (модельный срок, демо-данные ФГИС)."""
    due = m.get("verification_due")
    if not due:
        return T.CARD_NO_VERIF, None
    src = m.get("verification_source")
    label = AS.source_label(m) if src == "arshin" else T.VERIF_SOURCE.get(src or "")
    note = T.NOTE_MODEL if src == "model" else T.NOTE_ARSHIN_DEMO if src == "arshin" and AS.is_demo(m) else None
    return T.CARD_VERIF.format(date=full_date(date.fromisoformat(due)), source=label or T.VERIF_SOURCE_NONE), note


def card_text(m: Row, name: str, address: Row | None) -> str:
    """Тип · адрес, полный адрес, номер, последнее показание, поверка; оговорки — последней строкой-цитатой."""
    lines = [f"**{esc(name)}**"]
    if address:
        lines.append(T.CARD_ADDRESS.format(address=esc(address["full_text"])))
    serial = format_serial(m.get("serial"), m["type"])
    lines.append(T.CARD_SERIAL.format(serial=esc(serial)) if serial else T.CARD_NO_SERIAL)
    if m.get("last_id"):
        when = local_time(m.get("last_created_at"))
        lines.append(T.CARD_LAST.format(value=last_values(m),
                                        date=short_date(when.date()) if when else m["last_period"]))
    else:
        lines.append(T.CARD_NO_READINGS)
    verif, verif_note = _verification(m)
    lines.append(verif)
    notes = [n for n in (T.NOTE_ADDRESS if address and address["status"] == "unverified" else None, verif_note) if n]
    if notes:
        lines += ["", T.NOTE.format(text=" ".join(notes))]
    return "\n".join(lines)


@on_global(METER_CARD)
async def show_card(ctx: Ctx) -> None:
    found = await _find(ctx)
    if found is None:
        await _gone(ctx)
        return
    m, name = found
    address = await ctx.repo.user_address(ctx.user["id"], m["address_id"])
    await ctx.reply(card_text(m, name, address), K.kb(
        K.gbtn(T.BTN_SUBMIT, SUBMIT, m["id"]),
        [K.gbtn(T.BTN_DELETE, DELETE, m["id"]), K.gbtn(C.BTN_BACK, METERS)],
    ))


@on_global(SUBMIT)
async def submit_for_meter(ctx: Ctx) -> None:
    found = await _find(ctx)
    if found is None:
        await _gone(ctx)
        return
    m, name = found
    await call_hook("submission.for_meter", ctx, meter_id=m["id"], label=name)


async def _not_owner(ctx: Ctx, meter_id: int) -> None:
    await ctx.reply(T.NOT_OWNER, K.kb([K.gbtn(C.BTN_BACK, METER_CARD, meter_id), K.gbtn(C.BTN_MENU, MENU)]))


@on_global(DELETE)
async def ask_delete(ctx: Ctx) -> None:
    """«Удалить счётчик …?» [Да, удалить] [Отмена]; не собственнику при других жильцах — отказ сразу."""
    found = await _find(ctx)
    if found is None:
        await _gone(ctx)
        return
    m, name = found
    others = await ctx.repo.address_granted_others(m["address_id"], ctx.user["id"])
    if meter_delete_denial(m["role"], m["access"], others):
        await _not_owner(ctx, m["id"])
        return
    await ctx.reply(T.ASK_DELETE.format(meter=esc(name)), K.kb(
        [K.gbtn(T.BTN_DELETE_YES, DELETE_OK, m["id"]), K.gbtn(C.BTN_CANCEL, METER_CARD, m["id"])],
    ))


@on_global(DELETE_OK)
async def confirm_delete(ctx: Ctx) -> None:
    found = await _find(ctx)
    if found is None:
        await _gone(ctx)
        return
    m, name = found
    res = await ctx.repo.delete_meter(ctx.user["id"], m["id"])
    if res == "not_owner":
        await _not_owner(ctx, m["id"])
    elif res != "ok":
        await _gone(ctx)
    else:
        ctx.note(T.DELETED.format(meter=esc(name)))
        await my_meters(ctx)
