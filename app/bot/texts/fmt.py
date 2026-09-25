"""Форматирование для текстов: экранирование, разметка MAX, числа, деньги, даты по-русски.

Разметка сообщений (format=markdown): **жирный** — показания, суммы, даты, сроки, номера (1–3 на сообщение);
демо- и MVP-оговорки — последним блоком-цитатой «> …» через пустую строку (with_notes).
Предупреждения, важные для действия (блики, номер не совпадает), остаются в тексте.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from app.domain.meters import format_value, format_stored, spec
from app.domain.people import format_phone  # noqa: F401 — реэкспорт для текстов

MONTHS_NOM = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
              "сентябрь", "октябрь", "ноябрь", "декабрь")
MONTHS_GEN = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря")
_MD = str.maketrans({c: "\\" + c for c in "\\*_~`[]"})


def esc(text: str | None) -> str:
    """Экранирует пользовательский текст для markdown MAX."""
    return (text or "").translate(_MD)


def b(text: object) -> str:
    """Жирный фрагмент: '4 312 ₽' → '**4 312 ₽**'. Текст экранируем; пустое — без звёздочек."""
    s = esc("" if text is None else str(text)).strip()
    return f"**{s}**" if s else ""


def quote(text: str | Iterable[str | None]) -> str:
    """Цитата MAX: каждая непустая строка с «> ». Строка или список строк (в них тоже могут быть переносы)."""
    parts = [text] if isinstance(text, str) else [t for t in text if t]
    return "\n".join(f"> {line.strip()}" for part in parts for line in part.splitlines() if line.strip())


def with_notes(text: str, *notes: str | None) -> str:
    """Сообщение + оговорки (демо, модель, «не сверен с ФИАС») последним блоком-цитатой через пустую строку.
    Пустые и повторы пропускаем; без оговорок — текст как есть."""
    block = quote(dict.fromkeys(n for n in notes if n))
    if not block:
        return text
    return f"{text.rstrip()}\n\n{block}" if text.strip() else block


def value(v: int | None, meter_type: str, unit: bool = True) -> str:
    """123456 → '123,456 м³'."""
    return "—" if v is None else format_value(v, meter_type, unit=unit)


def stored(v: int | None, meter_type: str, unit: bool = True) -> str:
    """Значение из базы без хвостовых нулей: '2168 кВт·ч', '118,2 м³'."""
    return "—" if v is None else format_stored(v, meter_type, unit=unit)


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


def left_days(n: int, bold: bool = False) -> str:
    """'остался 1 день', 'осталось 3 дня'; 0 → 'сегодня последний день'. bold — 'осталось **3 дня**'."""
    if n <= 0:
        return f"сегодня {b('последний день')}" if bold else "сегодня последний день"
    return f"{plural(n, 'остался', 'осталось', 'осталось')} {b(n_days(n)) if bold else n_days(n)}"


def days(n: int, bold: bool = False) -> str:
    """'осталось 6 дней', 'остался 1 день'; 0 → 'сегодня последний день'; <0 → 'просрочено на 3 дня'."""
    if n >= 0:
        return left_days(n, bold)
    return f"просрочено на {b(n_days(-n)) if bold else n_days(-n)}"


def flats(n: int) -> str:
    """'40 квартир', '21 квартира'."""
    return f"{n} {plural(n, 'квартира', 'квартиры', 'квартир')}"


# Названия типов счётчиков для полных предложений (сокращения «Хол. вода» — только на кнопках и в списках).
TYPE_GEN = {"cold_water": "холодной воды", "hot_water": "горячей воды", "electricity": "электричества",
            "gas": "газа", "heat": "отопления"}


# Полные названия типов для заголовка счётчика: «Горячая вода · Дубнинская 37к1, кв 198».
TYPE_NOM = {"cold_water": "Холодная вода", "hot_water": "Горячая вода", "electricity": "Электричество",
            "gas": "Газ", "heat": "Отопление"}


def meter_title(meter_type: str, label: str | None) -> str:
    """Подпись из списка → заголовок с полным типом: 'Гор. вода · Арбат 47к1, кв 32' → 'Горячая вода · Арбат 47к1, кв 32'."""
    rest = (label or "").partition(" · ")[2].strip()
    name = TYPE_NOM.get(meter_type, "")
    return f"{name} · {rest}" if rest and name else name or rest or (label or "")


def meter_of(meter_type: str, label: str | None) -> str:
    """Подпись счётчика из списка → для предложения «счётчика …»:
    'Хол. вода · Арбат 47к1, кв 32' → 'холодной воды (Арбат 47к1, кв 32)'."""
    rest = (label or "").partition(" · ")[2].strip()
    name = TYPE_GEN.get(meter_type, "")
    return f"{name} ({rest})" if rest else name
