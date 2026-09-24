"""Уведомления (SPEC §5.9) и /demo.

candidates() — какие уведомления положены пользователю сейчас (чистая функция, по приоритету);
send_due_notice() — отправить первое ещё не отправленное (дедуп через таблицу notifications).
Кнопки уведомлений глобальные: действие + [В меню]; при нажатии проверяют актуальность.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.bot import keyboards as K
from app.bot.ctx import Ctx, Deps
from app.bot.flows.menu import MENU, PAY, SUBMIT, VERIFY, dashboard, send_menu
from app.bot.router import call_hook, on_command, on_global
from app.bot.texts import common as C
from app.bot.texts import notify as T
from app.bot.texts.fmt import day_month, esc, left_days, meter_of, money, month_name
from app.domain.dashboard import DashboardData, load_data, meter_names, submitted
from app.domain.meters import current_period, days_left, submission_window
from app.integrations.max_api import MaxApiError
from app.repo import Row

log = logging.getLogger(__name__)
N_SUBMIT, DEMO = "n_submit", "demo"
DEMO_VERIFICATION_DAYS = 20


@dataclass(frozen=True)
class Notice:
    kind: str       # 'submit' | 'verification' | 'bill' — notifications.kind
    key: str        # notifications.dedup_key
    text: str
    keyboard: dict


def _kb(action: K.Button) -> dict:
    return K.kb(action, K.gbtn(C.BTN_MENU, MENU))


# --- Тексты уведомлений ---

def submit_notice(meters: list[Row], today: date, day_from: int, day_to: int, stage: str | None = None) -> Notice:
    """О подаче за текущее (или ближайшее) окно. stage: 'open' | 'last' (None — по окну)."""
    w = submission_window(today, day_from, day_to)
    period = current_period(w.start)
    stage = stage or ("last" if w.is_open and w.days_left <= 3 else "open")
    month = month_name(period)
    if not w.is_open:  # /demo вне окна — говорим, когда откроется
        head = T.SUBMIT_NEXT.format(month=month, start=day_month(w.start), deadline=day_month(w.end))
    elif stage == "last":
        head = T.SUBMIT_LAST.format(month=month, deadline=day_month(w.end), left=left_days(w.days_left))
    else:
        head = T.SUBMIT_OPEN.format(month=month, deadline=day_month(w.end))
    lines = [head]
    names = meter_names(meters)
    not_done = [esc(names[m["id"]]) for m in meters if not submitted(m, period)]
    if not_done:
        lines.append(T.SUBMIT_NOT_DONE.format(meters=", ".join(not_done)))
    lines.append(T.SUBMIT_HINT)
    return Notice("submit", f"{period}:{stage}", "\n\n".join(lines), _kb(K.gbtn(T.BTN_SUBMIT, N_SUBMIT)))


def verification_notice(meter: Row, name: str, today: date, stage: str = "") -> Notice:
    due = date.fromisoformat(meter["verification_due"])
    n = days_left(today, due)
    full = esc(meter_of(meter["type"], name))  # в предложении — полное название: «холодной воды (адрес)»
    if n < 0:
        head = T.VERIFICATION_OVERDUE.format(meter=full, date=day_month(due))
    elif n == 0:
        head = T.VERIFICATION_TODAY.format(meter=full)
    else:
        head = T.VERIFICATION_SOON.format(meter=full, date=day_month(due), left=left_days(n))
    lines = [head, T.VERIFICATION_WHY]
    if meter.get("verification_source") == "model":
        lines.append(T.VERIFICATION_MODEL)
    return Notice("verification", f"{meter['id']}:{due.isoformat()}:{stage}", "\n\n".join(lines),
                  _kb(K.gbtn(T.BTN_VERIFY, VERIFY, meter["id"])))


def bill_notice(bill: Row, today: date, stage: str = "") -> Notice:
    due = date.fromisoformat(bill["due_date"])
    n = days_left(today, due)
    fields = {"month": month_name(bill["period"]), "address": esc(bill.get("address_label") or ""),
              "amount": money(bill["amount_kop"]), "date": day_month(due), "left": left_days(n)}
    head = (T.BILL_OVERDUE if n < 0 else T.BILL_DUE).format(**fields)
    return Notice("bill", f"{bill['id']}:{stage}", f"{head}\n\n{T.BILL_DEMO}", _kb(K.gbtn(T.BTN_PAY, PAY, bill["id"])))


# --- Что положено отправить ---

def _verification_stage(n: int) -> str | None:
    if n < 0:
        return "overdue"
    for limit in (1, 7, 30):
        if n <= limit:
            return str(limit)
    return None


def _bill_stage(n: int) -> str | None:
    if n < 0:
        return None
    return "1" if n <= 1 else "5" if n <= 5 else None


def candidates(data: DashboardData, today: date, day_from: int = 15, day_to: int = 25) -> list[Notice]:
    """Положенные сейчас уведомления по приоритету: поверка → счёт → подача.
    Этап (за 30/7/1 день, просрочка; счёт за 5/1; окно открылось/последние 3 дня) входит в ключ дедупа,
    поэтому каждое приходит один раз, даже если планировщик пропустил точный день."""
    out: list[Notice] = []
    names = meter_names(data.meters)
    for m in sorted(data.meters, key=lambda m: m.get("verification_due") or "9999"):
        if m.get("verification_due") and (
            stage := _verification_stage(days_left(today, date.fromisoformat(m["verification_due"])))
        ):
            out.append(verification_notice(m, names[m["id"]], today, stage))
    for b in sorted(data.bills, key=lambda b: b["due_date"]):
        if stage := _bill_stage(days_left(today, date.fromisoformat(b["due_date"]))):
            out.append(bill_notice(b, today, stage))
    w = submission_window(today, day_from, day_to)
    period = current_period(today)
    if w.is_open and any(not submitted(m, period) for m in data.meters):
        out.append(submit_notice(data.meters, today, day_from, day_to))
    return out


async def send_due_notice(deps: Deps, user: Row, now: datetime) -> bool:
    """Отправить пользователю первое положенное и ещё не отправленное уведомление. → отправили ли."""
    repo, s = deps.repo, deps.settings
    data = await load_data(repo, user["id"])
    for n in candidates(data, now.date(), s.submit_day_from, s.submit_day_to):
        if not await repo.try_mark_sent(user["id"], n.kind, n.key, now):
            continue
        try:
            await deps.api.send(n.text, user_id=user["max_user_id"], keyboard=n.keyboard)
        except MaxApiError as e:
            if e.status == 0 or e.status == 429 or e.status >= 500:
                await repo.unmark_sent(user["id"], n.kind, n.key)  # временный сбой — попробуем в следующий раз
            raise
        return True
    return False


# --- Кнопки уведомлений ---

@on_global(N_SUBMIT)
async def notice_submit(ctx: Ctx) -> None:
    """«Подать показания» из уведомления: уже всё подано → сообщаем и показываем дашборд."""
    d = await dashboard(ctx)
    if d.all_submitted:
        await send_menu(ctx, header=T.ALREADY_SUBMITTED.format(month=month_name(d.period)))
        return
    await call_hook("submission.start", ctx)


# --- /demo ---

@on_command("/demo")
async def demo(ctx: Ctx) -> None:
    if not ctx.settings.demo_mode:
        ctx.note(C.UNKNOWN_COMMAND)
        await send_menu(ctx)
        return
    await ctx.reply(T.DEMO, K.kb(
        [K.gbtn(T.BTN_DEMO_SUBMIT, DEMO, "submit"), K.gbtn(T.BTN_DEMO_VERIFY, DEMO, "verify"),
         K.gbtn(T.BTN_DEMO_BILL, DEMO, "bill")],
        K.gbtn(C.BTN_MENU, MENU),
    ))


async def _demo_verification(ctx: Ctx, today: date) -> Notice | None:
    """Одному счётчику (без даты от пользователя) ставим поверку через 20 дней (verification_source='model')."""
    meters = await ctx.repo.user_meters(ctx.user["id"])
    if not meters:
        return None
    target = next((m for m in meters if m.get("verification_source") != "user"), None)
    if target is not None:
        due = today + timedelta(days=DEMO_VERIFICATION_DAYS)
        await ctx.repo.set_verification(target["id"], due.isoformat(), "model")
        meters = await ctx.repo.user_meters(ctx.user["id"])
        target = next(m for m in meters if m["id"] == target["id"])
    else:  # все даты введены пользователем — не перетираем, берём ближайшую
        target = min(meters, key=lambda m: m["verification_due"])
    return verification_notice(target, meter_names(meters)[target["id"]], today)


async def _demo_bill(ctx: Ctx, today: date) -> Notice | None:
    uid = ctx.user["id"]
    bills = await ctx.repo.unpaid_bills(uid)
    if not bills:
        for a in await ctx.repo.user_addresses(uid):
            if a["access"] == "granted":
                await ctx.repo.ensure_demo_bill(a["id"], today)
        bills = await ctx.repo.unpaid_bills(uid)
    return bill_notice(bills[0], today) if bills else None


@on_global(DEMO)
async def demo_notice(ctx: Ctx) -> None:
    """Настоящее уведомление с рабочими кнопками — без записи в дедуп."""
    if not ctx.settings.demo_mode:
        await send_menu(ctx)
        return
    today, s = ctx.now.date(), ctx.settings
    if ctx.arg == "verify":
        notice = await _demo_verification(ctx, today)
        if notice is None:
            await ctx.reply(T.DEMO_ADD_METER_FIRST, _kb(K.gbtn(T.BTN_SUBMIT, SUBMIT)))
            return
    elif ctx.arg == "bill":
        notice = await _demo_bill(ctx, today)
        if notice is None:
            await ctx.reply(T.DEMO_NO_BILLS, K.kb(K.gbtn(C.BTN_MENU, MENU)))
            return
    else:
        notice = submit_notice(await ctx.repo.user_meters(ctx.user["id"]), today, s.submit_day_from, s.submit_day_to)
    await ctx.reply(notice.text, notice.keyboard)
