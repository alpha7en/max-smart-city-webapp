"""Серийные (заводские) номера счётчиков: очистка, ключ сравнения, показ и проверка формата по типу.

Храним два поля (meters.serial, meters.serial_norm): номер как распознали/ввели после clean_serial
(без «№», «S/N», пробелов по краям) — для показа, и normalize_serial — для сравнения.

Типичные форматы (по шильдикам распространённых моделей; при сомнении — предупреждение, не отказ):
  вода  — обычно 8 цифр, часто с префиксом года «18-123456», у старых 6–7 цифр, бывают буквы производителя;
  свет  — Меркурий 8 цифр, Нева 8 и больше, Энергомера 12–15; встречается до 16 цифр;
  газ   — BK-G4 и похожие 7–8 цифр, иногда с буквами;
  тепло — 6–10 цифр.
Номером не считаем: год выпуска, ГОСТ/ТУ, Qn/DN/класс, дату, номер пломбы.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# Кириллица, похожая на латиницу: модель и человек пишут «АВ-77» по-разному.
_LOOKALIKES = str.maketrans("авекмнорстух", "abekmhopctyx")
_DASHES = re.compile(r"\s*[\-‐-―]\s*")
# «№», «N°», «No.», «#», «S/N», «SN:», «зав. №», «заводской номер», «серийный номер» в начале.
_PREFIX = re.compile(
    r"^(?:(?:s/?n|зав(?:одской|\.)?\s*(?:номер|№)?|серийный\s+номер|номер|n[°º]|no\.|no(?=[\s\d])|№|#)"
    r"\s*[:.]?\s*)+",
    re.I,
)
_EDGES = " \t\r\n.,:;\"'«»()[]\ufeff\ufffc\u200b\u200c\u200d\u200e\u200f"
_INVISIBLE = re.compile(r"[\ufeff\ufffc\u200b-\u200f]")  # BOM, U+FFFC, нулевой ширины, метки направления
_NOT_SERIAL = re.compile(
    r"гост|gost|\bту\b|\bqn|\bq[1-4]\b|qmax|qmin|\bdn\s*\d|\bду\s*\d|класс|class|\bip\s*\d|имп|imp|"
    r"квт|kwh|гкал|gcal|м3|m3|°|пломб|seal",
    re.I,
)
_YEAR = re.compile(r"^(?:19|20)\d\d\s*(?:г\.?|года?)?$", re.I)
_DATE = re.compile(r"^\d{1,2}[./]\d{1,2}[./]\d{2,4}$")

# Обычное число цифр в номере по типу (включительно).
DIGITS: dict[str, tuple[int, int]] = {
    "cold_water": (6, 10), "hot_water": (6, 10), "electricity": (8, 16), "gas": (6, 12), "heat": (6, 10),
}
MIN_DIGITS = 4          # меньше — точно не заводской номер
MAX_LETTERS = 4         # буквы производителя: «ВК», «АВ», «SGN»
MAX_LEN = 32            # длиннее — точно не номер (вставили текст)


def strip_invisible(s: str) -> str:
    """Убрать невидимые символы (встречаются в ответах ФГИС и в скопированном тексте)."""
    return _INVISIBLE.sub("", s)


def clean_serial(raw: str | None) -> str | None:
    """Номер для хранения и показа: без «№»/«S/N» в начале, без мусора по краям, пробелы схлопнуты,
    тире — «-» без пробелов вокруг. Пусто → None."""
    if not raw:
        return None
    s = strip_invisible(str(raw))
    s = " ".join(s.split()).strip(_EDGES)
    s = _PREFIX.sub("", s).strip(_EDGES)
    s = _DASHES.sub("-", s).strip("-")
    return s or None


def normalize_serial(raw: str | None, meter_type: str | None = None) -> str | None:
    """Ключ для сравнения: без «№», пробелов, тире, точек, «/» и «#», casefold, кириллица-двойник → латиница.
    «18-123 456», «№ 18123456» и «18123456» совпадают. Ведущие нули значимы (у электросчётчиков это часть
    номера), поэтому тип счётчика на ключ не влияет — параметр для единообразия API. Пусто → None."""
    s = clean_serial(raw)
    if not s:
        return None
    s = re.sub(r"[\s\-‐-―№#./]+", "", s).casefold().translate(_LOOKALIKES)
    return s or None


def format_serial(raw: str | None, meter_type: str | None = None) -> str | None:
    """Номер для показа: как на шильдике, но единообразно — без «№», буквы заглавные.
    У света, газа и тепла номер из одних цифр — слитно («0112 3456 7890» → «011234567890»);
    у воды разделитель года сохраняем («18-123456»)."""
    s = clean_serial(raw)
    if not s:
        return None
    s = s.upper()
    if meter_type not in ("cold_water", "hot_water") and re.fullmatch(r"[\d\s\-]+", s):
        s = re.sub(r"\D", "", s)
    return s


Level = Literal["ok", "warning", "bad"]


@dataclass(frozen=True)
class SerialCheck:
    """level: ok; warning — необычно для типа, но сохраняем; bad — это не заводской номер, не сохраняем.
    code: None | 'empty' | 'not_serial' | 'too_short' | 'too_long' | 'unusual_length' | 'many_letters'."""
    level: Level
    code: str | None = None

    @property
    def ok(self) -> bool:
        return self.level == "ok"

    @property
    def usable(self) -> bool:
        return self.level != "bad"


def validate_serial(raw: str | None, meter_type: str | None = None) -> SerialCheck:
    """Проверка номера по типу счётчика. Сомнение — warning (номер сохраняем), явно не номер — bad."""
    s = clean_serial(raw)
    if not s:
        return SerialCheck("bad", "empty")
    digits = sum(ch.isdigit() for ch in s)
    letters = sum(ch.isalpha() for ch in s)
    if _NOT_SERIAL.search(s) or _YEAR.match(s) or _DATE.match(s) or not digits:
        return SerialCheck("bad", "not_serial")
    if digits < MIN_DIGITS:
        return SerialCheck("bad", "too_short")
    if len(s) > MAX_LEN:
        return SerialCheck("bad", "too_long")
    lo, hi = DIGITS.get(meter_type or "", (MIN_DIGITS, 16))
    if not lo <= digits <= hi:
        return SerialCheck("warning", "unusual_length")
    if letters > MAX_LETTERS:
        return SerialCheck("warning", "many_letters")
    return SerialCheck("ok")


def usable_serial(raw: str | None, meter_type: str | None = None) -> str | None:
    """Очищенный номер, если его можно показать и сохранить (не 'bad'), иначе None."""
    return clean_serial(raw) if validate_serial(raw, meter_type).usable else None
