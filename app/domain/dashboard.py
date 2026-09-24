"""Дашборд главного меню (SPEC §5.7) — один и тот же для бота и /api/me.

build_dashboard() — чистая функция от строк репозитория и даты; load_dashboard() — загрузка + сборка.
Строки дашборда — без markdown (их показывает и мини-приложение); блоки разделены пустой строкой "".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app import clock
from app.bot.texts import menu as T
from app.bot.texts.fmt import day_month, days, money, month_name, n_days, short_date
from app.domain.meters import (
    TYPE_LABELS,
    UNITS,
    current_period,
    days_left,
    fields_for,
    format_value,
    meter_labels,
    submission_window,
)

UrgentKind = Literal["verification", "bill", "submit"]
MAX_LINES = 9                  # непустых строк в тексте дашборда
VERIFICATION_SHOW_DAYS = 60    # поверку показываем, если до неё ≤ 60 дней
URGENT_VERIFICATION_DAYS = 30
URGENT_BILL_DAYS = 5
URGENT_SUBMIT_DAYS = 3
MAX_METER_LINES = 3            # больше счётчиков → одна строка-сводка

Row = dict[str, Any]


@dataclass(frozen=True)
class Urgent:
    """Самое срочное действие: строка дашборда и текст кнопки (≤ 32)."""
    kind: UrgentKind
    text: str
    days_left: int
    ref: int | None = None     # meter_id (поверка) или bill_id (счёт)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "text": self.text, "days_left": self.days_left}


@dataclass(frozen=True)
class DashboardData:
    """Строки репозитория: user_meters() (granted), unpaid_bills(), user_addresses()."""
    meters: list[Row] = field(default_factory=list)
    bills: list[Row] = field(default_factory=list)
    addresses: list[Row] = field(default_factory=list)


@dataclass
class Dashboard:
    lines: list[str]
    urgent: Urgent | None
    meters: list[dict] = field(default_factory=list)      # для /api/me (SPEC §6)
    addresses: list[dict] = field(default_factory=list)   # [{label, access, role}]
    all_submitted: bool = False                           # счётчики есть и все поданы за текущий период
    period: str = ""

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def to_api(self) -> dict:
        """Поле dashboard ответа /api/me."""
        return {"lines": list(self.lines), "urgent": self.urgent.to_dict() if self.urgent else None}


# --- Помощники ---

def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def local_time(value: str | None, tz: ZoneInfo = clock.TZ) -> datetime | None:
    """'YYYY-MM-DD HH:MM:SS' (UTC, как пишет SQLite) → aware datetime в местной зоне."""
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).astimezone(tz)
    except ValueError:
        return None


def meter_names(meters: list[Row]) -> dict[int, str]:
    """{meter_id: «Хол. вода · Арбат 47к1, кв 32»} — подписи как в подаче (domain.meters.meter_labels)."""
    return dict(zip([m["id"] for m in meters], meter_labels(meters), strict=True))


def submitted(meter: Row, period: str) -> bool:
    return meter.get("last_period") == period


def _api_meter(m: Row, period: str) -> dict:
    last = None
    if m.get("last_id"):
        created = local_time(m.get("last_created_at"))
        last = {
            "period": m["last_period"],
            "values": {f: (format_value(m[f"last_{f}"], m["type"]) if m.get(f"last_{f}") is not None else None)
                       for f in fields_for(m["tariffs"])},
            "created_at": created.isoformat() if created else None,
        }
    return {
        "id": m["id"], "type": m["type"], "type_label": TYPE_LABELS[m["type"]], "unit": UNITS[m["type"]],
        "tariffs": m["tariffs"], "address_label": m.get("address_label") or "", "serial": m.get("serial"),
        "last": last, "submitted_this_period": submitted(m, period),
        "verification_due": m.get("verification_due"),
    }


def _urgent_text(kind: UrgentKind, n: int) -> str:
    texts = {
        "verification": (T.URGENT_VERIFICATION, T.URGENT_VERIFICATION_TODAY, T.URGENT_VERIFICATION_OVERDUE),
        "bill": (T.URGENT_BILL, T.URGENT_BILL_TODAY, T.URGENT_BILL_OVERDUE),
        "submit": (T.URGENT_SUBMIT, T.URGENT_SUBMIT_TODAY, T.URGENT_SUBMIT_TODAY),
    }[kind]
    return texts[0].format(n=n_days(n)) if n > 0 else texts[1] if n == 0 else texts[2]


# --- Сборка ---

def build_dashboard(data: DashboardData, today: date, day_from: int = 15, day_to: int = 25) -> Dashboard:
    """Дашборд: срочное → доступ → подача → поверка/счёт → «подробнее». Не больше 9 непустых строк."""
    period = current_period(today)
    window = submission_window(today, day_from, day_to)
    meters = data.meters
    names = meter_names(meters)
    not_done = [m for m in meters if not submitted(m, period)]

    # Поверка: ближайшая дата среди счётчиков.
    verif = sorted(
        ((d, m) for m in meters if (d := _date(m.get("verification_due")))), key=lambda x: (x[0], x[1]["id"])
    )
    verif_soon = [(d, m) for d, m in verif if days_left(today, d) <= VERIFICATION_SHOW_DAYS]
    bills = sorted(
        ((d, b) for b in data.bills if (d := _date(b.get("due_date")))), key=lambda x: (x[0], x[1]["id"])
    )

    # Срочное — одно, по приоритету: поверка → счёт → подача.
    urgent: Urgent | None = None
    if verif and (n := days_left(today, verif[0][0])) <= URGENT_VERIFICATION_DAYS:
        urgent = Urgent("verification", _urgent_text("verification", n), n, verif[0][1]["id"])
    elif bills and (n := days_left(today, bills[0][0])) <= URGENT_BILL_DAYS:
        urgent = Urgent("bill", _urgent_text("bill", n), n, bills[0][1]["id"])
    elif window.is_open and not_done and window.days_left <= URGENT_SUBMIT_DAYS:
        urgent = Urgent("submit", _urgent_text("submit", window.days_left), window.days_left)

    blocks: list[list[str]] = []
    if urgent:
        blocks.append([urgent.text])

    pending = [a for a in data.addresses if a.get("access") == "pending"]
    if len(pending) == 1:
        blocks.append([T.PENDING.format(label=pending[0].get("label") or "")])
    elif pending:
        blocks.append([T.PENDING_MANY.format(n=len(pending))])

    granted = any(a.get("access") == "granted" for a in data.addresses)
    if meters:
        if window.is_open:
            head = T.PERIOD_OPEN.format(month=month_name(period), deadline=day_month(window.end),
                                        left=days(window.days_left))
        else:
            head = T.PERIOD_NEXT.format(start=day_month(window.start))
        block = [head]
        if len(meters) > MAX_METER_LINES:
            block.append(T.METERS_SUMMARY.format(total=len(meters), left=len(not_done)) if not_done
                         else T.METERS_ALL_DONE.format(total=len(meters)))
        else:
            missed = window.is_open or today.day > day_to  # до открытия окна «не подано» не пишем
            for m in meters:
                if submitted(m, period):
                    when = local_time(m.get("last_created_at"))
                    block.append(T.METER_SUBMITTED.format(meter=names[m["id"]],
                                                          date=short_date(when.date() if when else today)))
                else:
                    block.append(T.METER_NOT_SUBMITTED.format(meter=names[m["id"]]) if missed else names[m["id"]])
        blocks.append(block)
    elif granted or not data.addresses:
        blocks.append([T.NO_METERS])

    info: list[str] = []
    if verif_soon:
        d, m = verif_soon[0]
        tpl = T.VERIFICATION_OVERDUE if d < today else T.VERIFICATION
        line = tpl.format(meter=names[m["id"]], date=day_month(d))
        info.append(line + (T.MORE.format(n=len(verif_soon) - 1) if len(verif_soon) > 1 else ""))
    if len(bills) == 1:
        d, b = bills[0]
        tpl = T.BILL_OVERDUE if d < today else T.BILL
        info.append(tpl.format(amount=money(b["amount_kop"]), date=day_month(d)))
    elif bills:
        total = sum(b["amount_kop"] for _, b in bills)
        info.append(T.BILLS.format(count=len(bills), amount=money(total), date=day_month(bills[0][0])))
    if info:
        blocks.append(info)
    blocks.append([T.FOOTER])

    lines: list[str] = []
    for block in blocks:
        if lines:
            lines.append("")
        lines.extend(block)
    return Dashboard(
        lines=lines,
        urgent=urgent,
        meters=[_api_meter(m, period) for m in meters],
        addresses=[{"label": a.get("label") or "", "access": a["access"], "role": a["role"]}
                   for a in data.addresses],
        all_submitted=bool(meters) and not not_done,
        period=period,
    )


async def load_data(repo, user_id: int) -> DashboardData:
    return DashboardData(
        meters=await repo.user_meters(user_id),
        bills=await repo.unpaid_bills(user_id),
        addresses=await repo.user_addresses(user_id),
    )


async def load_dashboard(repo, user_id: int, today: date, day_from: int = 15, day_to: int = 25) -> Dashboard:
    """Дашборд пользователя (users.id). Используют меню бота, уведомления и /api/me."""
    return build_dashboard(await load_data(repo, user_id), today, day_from, day_to)
