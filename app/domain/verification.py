"""Поверка по данным ФГИС «Аршин» (eapi/vri): разбор записей, варианты номера и выбор записи. Без I/O.

Источники: «Руководство пользователя. Внешние публичные интерфейсы» v2.2 (табл. 3.3.7.1, поля списка vri)
и открытые клиенты (fgis_tg_bot, metroGen, pasport_print). Заводской номер уникален только внутри типа СИ,
поэтому записи с тем же номером отбираем по наименованию типа (mit_title) и марке/модели с фото.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from app.domain.serials import clean_serial

# Межповерочный интервал (типичный, лет) — для окна поиска verification_date_start = сегодня − МПИ − 1 год.
# Берём верхнюю границу по типу: ГВС 4–6, газ 8–12, свет 8–16 (точное значение — в описании типа СИ).
MPI_YEARS = {"cold_water": 6, "hot_water": 6, "electricity": 16, "gas": 12, "heat": 6}
CARD_URL = "https://fgis.gost.ru/fundmetrology/cm/results/{vri_id}"
MAX_OPTIONS = 3
_LOOKALIKES = str.maketrans("авекмнорстухё", "abekmhopctyxe")

Level = Literal["high", "low", "none"]


@dataclass(frozen=True)
class Record:
    """Элемент списка /eapi/vri."""
    vri_id: str
    mit_number: str = ""
    mit_title: str = ""
    mit_notation: str = ""
    mi_modification: str = ""
    mi_number: str = ""
    org_title: str = ""
    verification_date: date | None = None
    valid_date: date | None = None
    applicable: bool = True

    @property
    def unfit(self) -> bool:
        return not self.applicable

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        for k in ("verification_date", "valid_date"):
            d[k] = d[k].isoformat() if d[k] else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Record:
        return cls(**{**d, "verification_date": parse_date(d.get("verification_date")),
                      "valid_date": parse_date(d.get("valid_date"))})


@dataclass(frozen=True)
class Match:
    """high — записываем дату; low — спрашиваем «Это ваш счётчик?» (options); none — записи нет."""
    level: Level
    best: Record | None = None
    options: list[Record] = field(default_factory=list)


def parse_date(value: Any) -> date | None:
    """ISO ('2021-10-07T12:00:00Z', '2021-10-07') и 'dd.mm.yyyy' (реальные ответы 2024 г.) → date."""
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}", s):
            return date.fromisoformat(s[:10])
        return datetime.strptime(s[:10], "%d.%m.%Y").date()
    except ValueError:
        return None


def parse_item(item: Any) -> Record | None:
    """Элемент ответа → Record; без vri_id — None. Некоторых полей в ответе может не быть."""
    if not isinstance(item, dict) or not item.get("vri_id"):
        return None
    s = lambda k: re.sub(r"[\ufeff\ufffc\u200b-\u200f]", "", str(item.get(k) or "")).strip()  # noqa: E731
    applicable = item.get("applicability")
    return Record(
        vri_id=s("vri_id"), mit_number=s("mit_number"), mit_title=s("mit_title"), mit_notation=s("mit_notation"),
        mi_modification=s("mi_modification"), mi_number=s("mi_number"), org_title=s("org_title"),
        verification_date=parse_date(item.get("verification_date")), valid_date=parse_date(item.get("valid_date")),
        applicable=applicable not in (False, "false", 0),
    )


def parse_list(payload: Any) -> list[Record] | None:
    """{"result": {"items": [...]}} → записи; не тот формат — None."""
    try:
        items = payload["result"]["items"]
    except (KeyError, TypeError):
        return None
    if not isinstance(items, list):
        return None
    return [r for r in map(parse_item, items) if r]


def search_start(meter_type: str, today: date) -> date:
    """Начало окна поиска: без year/verification_date_start API ищет только текущий год."""
    years = MPI_YEARS.get(meter_type, 16) + 1
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # 29 февраля
        return today.replace(year=today.year - years, day=28)


def serial_variants(serial: str | None, meter_type: str) -> list[str]:
    """Как номер мог попасть в реестр: как есть, без разделителей, без ведущих нулей,
    у воды — без префикса года («18-123456» → «123456»). Не больше трёх, без повторов."""
    s = clean_serial(serial)
    if not s:
        return []
    compact = re.sub(r"[\s\-‐-―./]+", "", s)
    out = [s, compact]
    if compact.isdigit() and compact.startswith("0"):
        out.append(compact.lstrip("0"))
    if meter_type in ("cold_water", "hot_water") and (m := re.fullmatch(r"(\d{2})[\s\-](\d{5,})", s)):
        out.append(m.group(2))
    seen: list[str] = []
    for v in out:
        if v and len(v) >= 4 and v not in seen:
            seen.append(v)
    return seen[:3]


def _norm(text: str | None) -> str:
    return re.sub(r"[^0-9a-zа-я]+", "", (text or "").casefold().replace("ё", "е")).translate(_LOOKALIKES)


def type_fit(title: str, meter_type: str) -> int:
    """1 — наименование типа подходит, 0 — нельзя сказать (нет наименования, универсальный), −1 — другой тип."""
    t = (title or "").casefold().replace("ё", "е")
    if not t:
        return 0
    if meter_type in ("cold_water", "hot_water"):
        if "вод" not in t or "тепл" in t:
            return -1
        hot, cold = "горяч" in t, "холодн" in t
        if meter_type == "hot_water" and cold and not hot or meter_type == "cold_water" and hot and not cold:
            return -1
        return 1
    if meter_type == "electricity":
        return 1 if "электр" in t or ("энерги" in t and "тепл" not in t) else -1
    if meter_type == "gas":
        return 1 if "газ" in t and "анализ" not in t else -1
    if meter_type == "heat":
        return 1 if "тепл" in t else -1
    return 0


def brand_score(r: Record, brand: str | None, model: str | None) -> int:
    """Сколько из марки и модели с фото нашлось в обозначении/модификации/наименовании типа."""
    hay = _norm(f"{r.mit_notation} {r.mi_modification} {r.mit_title}")
    return sum(1 for x in (brand, model) if len(n := _norm(x)) >= 3 and n in hay)


def _latest(group: list[Record]) -> Record:
    return max(group, key=lambda r: (r.verification_date or date.min, r.vri_id))


def choose(records: list[Record], meter_type: str, brand: str | None = None, model: str | None = None) -> Match:
    """Отбор по типу → группы по mit_number → в группе последняя поверка.
    high: одна группа с подтверждённым типом или единственная группа, где совпала марка/модель;
    low: несколько групп или тип не подтверждён (до трёх вариантов); none: подходящих нет."""
    fits = [(r, type_fit(r.mit_title, meter_type)) for r in records]
    groups: dict[str, list[Record]] = {}
    fit_of: dict[str, int] = {}
    for r, fit in fits:
        if fit < 0:
            continue
        key = r.mit_number or r.mit_title or r.vri_id
        groups.setdefault(key, []).append(r)
        fit_of[key] = max(fit_of.get(key, 0), fit)
    if not groups:
        return Match("none")
    best = {k: _latest(g) for k, g in groups.items()}
    scores = {k: max(brand_score(r, brand, model) for r in g) for k, g in groups.items()}
    branded = [k for k, s in scores.items() if s > 0]
    if len(branded) == 1:
        return Match("high", best[branded[0]])
    if len(groups) == 1 and fit_of[next(iter(groups))] > 0:
        return Match("high", next(iter(best.values())))
    order = sorted(groups, key=lambda k: (scores[k], fit_of[k], best[k].verification_date or date.min), reverse=True)
    return Match("low", options=[best[k] for k in order[:MAX_OPTIONS]])


def card_url(vri_id: str | None) -> str | None:
    """Карточка поверки для людей (fgis_tg_bot: /cm/results/<vri_id>, id из eapi уже с префиксом «1-»/«2-»).
    Демо-записи ссылки не получают."""
    if not vri_id or not re.fullmatch(r"(?:\d-)?\d+", vri_id):
        return None
    return CARD_URL.format(vri_id=vri_id if "-" in vri_id else f"1-{vri_id}")


def is_demo_id(vri_id: str | None) -> bool:
    """Запись из демо-данных ФГИС (ARSHIN_MODE=fixtures), не из реестра."""
    return str(vri_id or "").startswith("demo-")


def short_title(r: Record, limit: int = 22) -> str:
    """Короткое имя типа для кнопки: обозначение (СГВ-15) или начало наименования."""
    name = r.mit_notation or ""
    if (len(name) > limit or "," in name) and r.mi_modification:
        name = r.mi_modification
    name = name or r.mi_modification or r.mit_title or r.mit_number or "Прибор"
    name = " ".join(name.split())
    return name if len(name) <= limit else name[: limit - 1].rstrip() + "…"

