"""Счётчики: типы, единицы, разбор и формат значений, правдоподобие, периоды и сроки.

Значения показаний везде — int в тысячных долях единицы (123,456 м³ → 123456). Без float.
"""
from __future__ import annotations

import calendar
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Literal


class MeterType(StrEnum):
    COLD_WATER = "cold_water"
    HOT_WATER = "hot_water"
    ELECTRICITY = "electricity"
    GAS = "gas"
    HEAT = "heat"


@dataclass(frozen=True)
class MeterSpec:
    label: str          # короткая подпись для кнопок и дашборда
    unit: str
    int_digits: int     # цифр до запятой на табло
    frac_digits: int    # цифр после запятой
    monthly_limit: int  # порог правдоподобного прироста за месяц, в целых единицах


SPECS: dict[MeterType, MeterSpec] = {
    MeterType.COLD_WATER: MeterSpec("Хол. вода", "м³", 5, 3, 30),
    MeterType.HOT_WATER: MeterSpec("Гор. вода", "м³", 5, 3, 20),
    MeterType.ELECTRICITY: MeterSpec("Свет", "кВт·ч", 6, 2, 1500),
    MeterType.GAS: MeterSpec("Газ", "м³", 5, 3, 300),
    MeterType.HEAT: MeterSpec("Тепло", "Гкал", 4, 3, 5),
}
TYPE_LABELS: dict[str, str] = {t.value: s.label for t, s in SPECS.items()}
UNITS: dict[str, str] = {t.value: s.unit for t, s in SPECS.items()}
FIELDS = ("t1", "t2", "t3")
# Подписи полей по тарифности (для однотарифного подпись не нужна).
TARIFF_LABELS: dict[int, tuple[str, ...]] = {
    1: ("",),
    2: ("Т1 день", "Т2 ночь"),
    3: ("Т1 пик", "Т2 ночь", "Т3 полупик"),
}


def spec(meter_type: str) -> MeterSpec:
    return SPECS[MeterType(meter_type)]


def fields_for(tariffs: int) -> tuple[str, ...]:
    return FIELDS[: max(1, min(3, tariffs))]


def field_labels(meter_type: str, tariffs: int) -> dict[str, str]:
    """{'t1': 'Т1 день', ...}; для однотарифного — {'t1': ''}."""
    t = tariffs if meter_type == MeterType.ELECTRICITY else 1
    return dict(zip(fields_for(t), TARIFF_LABELS[t], strict=True))


# --- Разбор и формат значений ---

class ValueParseError(ValueError):
    """code: 'empty' | 'format' | 'too_many_digits' | 'too_many_decimals'."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


_NUM = re.compile(r"^(\d+)(?:[.,](\d+))?$")


def parse_value(text: str, meter_type: str | None = None) -> int:
    """'123,456' → 123456 (тысячные). Разделитель «,» или «.», пробелы только по краям."""
    s = (text or "").strip()
    if not s:
        raise ValueParseError("empty")
    m = _NUM.match(s)
    if not m:
        raise ValueParseError("format")
    whole, frac = m.group(1), m.group(2) or ""
    sp = spec(meter_type) if meter_type else None
    max_int = sp.int_digits if sp else 9
    max_frac = sp.frac_digits if sp else 3
    if len(whole.lstrip("0")) > max_int:
        raise ValueParseError("too_many_digits")
    if len(frac) > max_frac:
        raise ValueParseError("too_many_decimals")
    return int(whole) * 1000 + int(frac.ljust(3, "0") or "0")


def format_value(value: int, meter_type: str | None = None, *, unit: bool = False) -> str:
    """123456 → '123,456' (знаков после запятой — как на табло счётчика этого типа)."""
    digits = spec(meter_type).frac_digits if meter_type else 3
    sign = "-" if value < 0 else ""
    v = abs(value)
    step = 10 ** (3 - digits)
    v = (v + step // 2) // step  # округление до нужного числа знаков
    whole, frac = divmod(v, 10**digits)
    text = f"{sign}{whole}" + (f",{frac:0{digits}d}" if digits else "")
    return f"{text} {spec(meter_type).unit}" if unit and meter_type else text


# Кириллица, похожая на латиницу: модель и человек пишут «АВ-77» по-разному.
_SERIAL_LOOKALIKES = str.maketrans("авекмнорстух", "abekmhopctyx")


def normalize_serial(serial: str | None) -> str | None:
    """Серийник для сравнения: без пробелов, дефисов, «№», «#» и точек, casefold, кириллица-двойник → латиница.
    Пусто → None."""
    if not serial:
        return None
    s = re.sub(r"[\s\-‐-―№#.]+", "", serial).casefold().translate(_SERIAL_LOOKALIKES)
    return s or None


# --- Правдоподобие ---

Plausibility = Literal["ok", "less", "too_big"]


def check_plausibility(
    meter_type: str, values: dict[str, int | None], prev: dict[str, int | None] | None, months: int = 1
) -> Plausibility:
    """'less' — меньше прошлого; 'too_big' — прирост выше порога × месяцев; иначе 'ok'."""
    if not prev:
        return "ok"
    pairs = [(values.get(f), prev.get(f)) for f in FIELDS]
    pairs = [(n, p) for n, p in pairs if n is not None and p is not None]
    if not pairs:
        return "ok"
    if any(n < p for n, p in pairs):
        return "less"
    if meter_type == MeterType.ELECTRICITY:
        delta = sum(n - p for n, p in pairs)
    else:
        delta = pairs[0][0] - pairs[0][1]
    if delta > spec(meter_type).monthly_limit * 1000 * max(1, months):
        return "too_big"
    return "ok"


# --- Периоды и сроки ---

def current_period(today: date) -> str:
    return f"{today.year:04d}-{today.month:02d}"


def _period_tuple(period: str) -> tuple[int, int]:
    y, m = period.split("-")
    return int(y), int(m)


def shift_period(period: str, months: int) -> str:
    y, m = _period_tuple(period)
    idx = y * 12 + (m - 1) + months
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def months_between(earlier: str, later: str) -> int:
    """Число месяцев между периодами, минимум 1."""
    (y1, m1), (y2, m2) = _period_tuple(earlier), _period_tuple(later)
    return max(1, (y2 - y1) * 12 + (m2 - m1))


def _day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


@dataclass(frozen=True)
class Window:
    start: date
    end: date
    is_open: bool
    days_left: int  # открыто: дней до конца (0 — последний день); закрыто: дней до открытия


def submission_window(today: date, day_from: int = 15, day_to: int = 25) -> Window:
    """Окно подачи показаний (модель): с day_from по day_to число месяца."""
    start, end = _day(today.year, today.month, day_from), _day(today.year, today.month, day_to)
    if today < start:
        return Window(start, end, False, (start - today).days)
    if today <= end:
        return Window(start, end, True, (end - today).days)
    ny, nm = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    start, end = _day(ny, nm, day_from), _day(ny, nm, day_to)
    return Window(start, end, False, (start - today).days)


def days_left(today: date, target: date) -> int:
    """Дней до даты (отрицательное — просрочено)."""
    return (target - today).days


def demo_bill(address_id: int, today: date) -> tuple[str, int, date]:
    """Демо-счёт за прошлый месяц: (period, amount_kop, due_date). Сумма 2 000–6 000 ₽ от id адреса."""
    period = shift_period(current_period(today), -1)
    amount_rub = 2000 + (address_id * 7919) % 4001
    if today.day <= 10:
        due = date(today.year, today.month, 10)
    else:
        ny, nm = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        due = date(ny, nm, 10)
    return period, amount_rub * 100, due


# --- Подача показаний (S2): подписи счётчиков, прирост, ввод ---

def tariffs_of(meter_type: str, tariffs: int | None) -> int:
    """Тарифность, которая реально действует: несколько тарифов бывает только у электричества."""
    return max(1, min(3, tariffs or 1)) if meter_type == MeterType.ELECTRICITY else 1


def _serial_tail(serial: str | None, n: int = 4) -> str | None:
    s = normalize_serial(serial)
    return s[-n:].upper() if s else None


def meter_labels(meters: Sequence[Mapping[str, Any]], max_len: int = 40) -> list[str]:
    """Подписи набора счётчиков: «Хол. вода · Арбат 47к1, кв 32».

    Строки — dict с type, address_id, address_label, id, serial (как из repo.user_meters).
    Счётчики одного типа по одному адресу различаются хвостом серийника («… 4521»),
    а если серийники не у всех или совпадают хвосты — номером («№2», по порядку id).
    """
    labels = []
    for m in meters:
        base = TYPE_LABELS[m["type"]] + (f" · {m['address_label']}" if m.get("address_label") else "")
        group = sorted(
            (x for x in meters if x["type"] == m["type"] and x.get("address_id") == m.get("address_id")),
            key=lambda x: x["id"],
        )
        if len(group) > 1:
            tails = [_serial_tail(x.get("serial")) for x in group]
            tail = _serial_tail(m.get("serial"))
            by_serial = f"{base} …{tail}"
            if all(tails) and len(set(tails)) == len(tails) and len(by_serial) <= max_len:
                base = by_serial
            else:
                base = f"{base} №{[x['id'] for x in group].index(m['id']) + 1}"
        labels.append(base)
    return labels


def meter_label(meter: Mapping[str, Any], meters: Sequence[Mapping[str, Any]] = ()) -> str:
    """Подпись одного счётчика с учётом остальных счётчиков пользователя (см. meter_labels)."""
    group = list(meters) if any(m["id"] == meter["id"] for m in meters) else [*meters, meter]
    return meter_labels(group)[[m["id"] for m in group].index(meter["id"])]


def growth_limit(meter_type: str, months: int = 1) -> int:
    """Порог правдоподобного прироста (тысячные) за `months` месяцев (минимум 1)."""
    return spec(meter_type).monthly_limit * 1000 * max(1, months)


def value_delta(values: Mapping[str, int | None], prev: Mapping[str, int | None] | None) -> dict[str, int]:
    """Прирост по полям, где есть оба значения: {'t1': 5256, ...}."""
    if not prev:
        return {}
    return {f: values[f] - prev[f] for f in FIELDS
            if values.get(f) is not None and prev.get(f) is not None}


def total_delta(meter_type: str, delta: Mapping[str, int]) -> int:
    """Прирост для сравнения с порогом: у электричества — сумма тарифов, иначе T1."""
    if meter_type == MeterType.ELECTRICITY:
        return sum(delta.values())
    return delta.get("t1", 0)


_VALUE_LIKE = re.compile(r"^[\d\s.,+\-]+$")


def looks_like_value(text: str | None) -> bool:
    """Похоже на введённое показание (цифры, разделители) — для ручного ввода вместо фото."""
    s = (text or "").strip()
    return bool(s) and bool(_VALUE_LIKE.match(s)) and any(ch.isdigit() for ch in s)


class DateParseError(ValueError):
    """code: 'format' | 'no_such_date' | 'past' | 'too_far'."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


_DATE = re.compile(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$")


def parse_due_date(text: str | None, today: date, max_years: int = 30) -> date:
    """Дата следующей поверки «15.03.2030» (не раньше сегодняшнего дня)."""
    m = _DATE.match((text or "").strip())
    if not m:
        raise DateParseError("format")
    d, mo, y = (int(g) for g in m.groups())
    try:
        result = date(y, mo, d)
    except ValueError:
        raise DateParseError("no_such_date") from None
    if result < today:
        raise DateParseError("past")
    if result.year > today.year + max_years:
        raise DateParseError("too_far")
    return result
