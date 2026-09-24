"""Форматирование для текстов: экранирование, числа, деньги, даты по-русски."""
from __future__ import annotations

from datetime import date

from app.domain.meters import format_value, spec
from app.domain.people import format_phone  # noqa: F401 — реэкспорт для текстов

MONTHS_NOM = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
              "сентябрь", "октябрь", "ноябрь", "декабрь")
MONTHS_GEN = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря")
_MD = str.maketrans({c: "\\" + c for c in "\\*_~`[]"})


def esc(text: str | None) -> str:
    """Экранирует пользовательский текст для markdown MAX."""
    return (text or "").translate(_MD)


def value(v: int | None, meter_type: str, unit: bool = True) -> str:
    """123456 → '123,456 м³'."""
    return "—" if v is None else format_value(v, meter_type, unit=unit)


def delta(v: int, meter_type: str) -> str:
    """Прирост со знаком: '+5,256'."""
    s = format_value(v, meter_type)
    return s if s.startswith("-") else "+" + s


def unit(meter_type: str) -> str:
    return spec(meter_type).unit


def money(kop: int) -> str:
    """431200 → '4 312 ₽' (копейки показываем, только если они есть)."""
    rub, k = divmod(kop, 100)
    whole = f"{rub:,}".replace(",", " ")
    return f"{whole},{k:02d} ₽" if k else f"{whole} ₽"


def day_month(d: date) -> str:
    """'25 октября'."""
    return f"{d.day} {MONTHS_GEN[d.month - 1]}"


def short_date(d: date) -> str:
    """'12.10'."""
    return f"{d.day:02d}.{d.month:02d}"


def full_date(d: date) -> str:
    """'15.03.2030'."""
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


def month_name(period: str) -> str:
    """'2026-10' → 'октябрь'."""
    return MONTHS_NOM[int(period.split("-")[1]) - 1]


def days(n: int) -> str:
    """'осталось 6 дн.'; 0 → 'сегодня последний день'; <0 → 'просрочено на 3 дн.'."""
    if n > 0:
        return f"осталось {n} дн."
    if n == 0:
        return "сегодня последний день"
    return f"просрочено на {-n} дн."


def plural(n: int, one: str, few: str, many: str) -> str:
    """Форма слова по числу: plural(3, 'день', 'дня', 'дней') → 'дня'."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def n_days(n: int) -> str:
    """'1 день', '3 дня', '5 дней'."""
    return f"{n} {plural(n, 'день', 'дня', 'дней')}"


def left_days(n: int) -> str:
    """'остался 1 день', 'осталось 3 дня'; 0 → 'сегодня последний день'."""
    if n <= 0:
        return "сегодня последний день"
    return f"{plural(n, 'остался', 'осталось', 'осталось')} {n_days(n)}"
