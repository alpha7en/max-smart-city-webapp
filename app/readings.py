"""Сервис подачи показаний — общий для бота (flows/submission.py) и API мини-приложения (web/api.py).

Использование в API (S5b):

    from app.readings import submit_reading, values_to_units

    res = await submit_reading(repo, user_id=user["id"], meter_id=body.meter_id, draft=None,
                               values=body.values,            # {'t1': '123,456', ...} — строки как ввёл пользователь
                               source="miniapp", recognized=None,
                               confirm=body.confirm, replace=body.replace, today=clock.today())
    res.status → HTTP:
        accepted | flagged                → 200 {status, reading: {id, values: values_to_units(res.values)}}
        bad_format | less_than_previous   → 422 {code: res.status, message: res.message}
        needs_confirm | already_submitted → 409 {code: res.status, message: res.message}
        no_access                         → 403 {code: 'no_access', message: res.message}
    no_access возвращается и для несуществующего счётчика (404 API определяет само через repo.get_meter).

Значения в SubmitResult (values, previous, delta) — int в тысячных; в JSON — через values_to_units().
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.bot.texts import fmt
from app.bot.texts import submission as T
from app.domain.access import can_submit
from app.domain.meters import (
    FIELDS,
    ValueParseError,
    check_plausibility,
    current_period,
    fields_for,
    format_value,
    months_between,
    parse_value,
    spec,
    tariffs_of,
    total_delta,
    value_delta,
)
from app.repo import ReadingExists, Repo, values_of

Status = Literal["accepted", "flagged", "needs_confirm", "less_than_previous", "already_submitted",
                 "no_access", "bad_format"]
Values = dict[str, int | None]


@dataclass
class SubmitResult:
    status: Status
    reading_id: int | None
    meter_id: int | None
    previous: Values | None   # прошлое показание (для already_submitted — уже поданное за период)
    delta: dict[str, int] | None
    message: str              # человеческий текст для API
    values: Values | None = None  # разобранные значения (тысячные)
    period: str | None = None     # 'YYYY-MM'
    months: int | None = None     # месяцев с прошлой подачи (для порога прироста)

    @property
    def ok(self) -> bool:
        return self.status in ("accepted", "flagged")


def to_units(v: int | None) -> float | None:
    """Тысячные → число в единицах счётчика для JSON: 123456 → 123.456."""
    return None if v is None else v / 1000


def values_to_units(values: Values | dict[str, int] | None) -> dict[str, float | None]:
    return {f: to_units(v) for f, v in (values or {}).items()}


def format_values(values: Values, meter_type: str, tariffs: int = 1) -> str:
    """'123,456 м³' или для нескольких тарифов '12345,67 / 5000,00 кВт·ч'."""
    fs = fields_for(tariffs_of(meter_type, tariffs))
    nums = [format_value(values[f], meter_type) if values.get(f) is not None else "—" for f in fs]
    return f"{' / '.join(nums)} {spec(meter_type).unit}"


def format_months(n: int) -> str:
    """'1 месяц', '3 месяца', '5 месяцев'."""
    return f"{n} {fmt.plural(n, 'месяц', 'месяца', 'месяцев')}"


def format_delta(meter_type: str, delta: dict[str, int]) -> str:
    """'+5,256 м³' (у электричества — сумма по тарифам)."""
    return f"{fmt.delta(total_delta(meter_type, delta), meter_type)} {spec(meter_type).unit}"


def parse_values(values: dict[str, str | int | float | None], meter_type: str, tariffs: int) -> Values:
    """Строки как ввёл пользователь (или int в тысячных) → {'t1','t2','t3'} в тысячных.
    Нужны все поля по тарифности, лишние игнорируются. Ошибка → ValueParseError (с атрибутом field)."""
    out: Values = dict.fromkeys(FIELDS)
    int_digits = spec(meter_type).int_digits
    for f in fields_for(tariffs_of(meter_type, tariffs)):
        v = values.get(f)
        try:
            if isinstance(v, bool):
                raise ValueParseError("format")
            if isinstance(v, int):
                if v < 0:
                    raise ValueParseError("format")
                if len(str(v // 1000)) > int_digits:
                    raise ValueParseError("too_many_digits")
                out[f] = v
            else:
                out[f] = parse_value("" if v is None else str(v), meter_type)
        except ValueParseError as e:
            e.field = f  # type: ignore[attr-defined]
            raise
    return out


def _bad_format(e: ValueParseError, meter_type: str, tariffs: int, meter_id: int | None) -> SubmitResult:
    field = getattr(e, "field", "t1")
    names = dict(zip(fields_for(3), ("Т1", "Т2", "Т3"), strict=True))
    label = f" в поле {names[field]}" if tariffs_of(meter_type, tariffs) > 1 else ""
    msg = T.API_BAD_FORMAT.format(field=label, example=T.EXAMPLES[meter_type])
    return SubmitResult("bad_format", None, meter_id, None, None, msg)


def _no_access(meter_id: int | None) -> SubmitResult:
    return SubmitResult("no_access", None, meter_id, None, None, T.API_NO_ACCESS)


async def submit_reading(
    repo: Repo,
    *,
    user_id: int,
    meter_id: int | None,
    draft: dict | None,
    values: dict[str, str | int],
    source: str,
    recognized: dict | None,
    confirm: bool = False,
    replace: bool = False,
    today: date,
) -> SubmitResult:
    """Подать показание за текущий месяц — проверки и запись одной транзакцией.

    meter_id — существующий счётчик; иначе draft={address_id, type, tariffs, serial?} — новый счётчик,
    он создаётся вместе с первым показанием (если по адресу уже есть счётчик с этим серийником — пишем в него).
    source: 'photo' | 'photo_edited' | 'manual' | 'miniapp'. recognized — что вернул распознаватель
    ({t1,t2,t3,serial,confidence,stub}); его serial сохраняется у счётчика без серийника.
    Порядок проверок (SPEC_REVIEW C5): права → формат → уже подано (если не replace) →
    меньше прошлого (последнее показание ДО текущего периода) → прирост выше порога × месяцев (если не confirm).
    Первое показание счётчика проверяется только на формат. confirm=True с большим приростом → 'flagged'.
    """
    period = current_period(today)
    try:
        async with repo.tx():
            return await _submit(repo, user_id, meter_id, draft, values, source, recognized,
                                 confirm, replace, period)
    except ReadingExists as e:  # гонка двух подач: запись откатили, отвечаем как «уже подано»
        row = await repo.get_meter(e.reading["meter_id"])
        return _already(row["type"], row["tariffs"], e.reading, None, period)


def _already(meter_type: str, tariffs: int, old: dict, new: Values | None, period: str) -> SubmitResult:
    prev = values_of(old)
    msg = T.API_ALREADY.format(month=fmt.month_name(period), old=format_values(prev, meter_type, tariffs))
    return SubmitResult("already_submitted", None, old["meter_id"], prev, None, msg, new, period)


async def _submit(repo: Repo, user_id: int, meter_id: int | None, draft: dict | None,
                  values: dict, source: str, recognized: dict | None, confirm: bool, replace: bool,
                  period: str) -> SubmitResult:
    # 1. Права.
    if meter_id is not None:
        meter = await repo.meter_access(user_id, meter_id)
        if not meter or not meter["active"] or not can_submit(meter["access"]):
            return _no_access(meter_id)
        mtype, tariffs, address_id = meter["type"], meter["tariffs"], meter["address_id"]
    elif draft:
        link = await repo.user_address(user_id, draft["address_id"])
        if not link or not can_submit(link["access"]):
            return _no_access(None)
        mtype, tariffs, address_id = draft["type"], tariffs_of(draft["type"], draft.get("tariffs")), draft["address_id"]
        same = await repo.find_meter_by_serial(address_id, draft["serial"]) if draft.get("serial") else None
        if same:
            meter, meter_id = same, same["id"]
            mtype, tariffs = same["type"], same["tariffs"]
    else:
        raise ValueError("meter_id or draft required")

    # 2. Формат.
    try:
        parsed = parse_values(values, mtype, tariffs)
    except ValueParseError as e:
        return _bad_format(e, mtype, tariffs, meter_id)

    status, prev, delta, months = "accepted", None, None, None
    if meter_id is not None:
        # 3. Уже подано за период.
        if not replace:
            old = await repo.reading_for_period(meter_id, period)
            if old:
                return _already(mtype, tariffs, old, parsed, period)
        # 4–5. Меньше прошлого / большой прирост.
        prev_row = await repo.last_reading(meter_id, before_period=period)
        if prev_row:
            prev = values_of(prev_row)
            delta = value_delta(parsed, prev)
            months = months_between(prev_row["period"], period)
            verdict = check_plausibility(mtype, parsed, prev, months)
            if verdict == "less":
                msg = T.API_LESS.format(prev=format_values(prev, mtype, tariffs))
                return SubmitResult("less_than_previous", None, meter_id, prev, delta, msg, parsed, period, months)
            if verdict == "too_big":
                if not confirm:
                    msg = T.API_NEEDS_CONFIRM.format(delta=format_delta(mtype, delta), months=format_months(months))
                    return SubmitResult("needs_confirm", None, meter_id, prev, delta, msg, parsed, period, months)
                status = "flagged"

    # Запись: счётчик из черновика + показание (+ серийник с фото, если его не было).
    meter_id, reading_id = await repo.submit_reading(
        user_id=user_id, period=period, values=parsed, source=source, status=status,
        recognized=recognized, replace=replace, meter_id=meter_id,
        draft=None if meter_id is not None else {**draft, "tariffs": tariffs},
    )
    serial = (recognized or {}).get("serial")
    if serial:
        row = await repo.get_meter(meter_id)
        if not row["serial_norm"] and not await repo.find_meter_by_serial(address_id, serial):
            await repo.set_meter_serial(meter_id, serial)
    msg = T.API_FLAGGED if status == "flagged" else T.API_ACCEPTED
    return SubmitResult(status, reading_id, meter_id, prev, delta, msg, parsed, period, months)
