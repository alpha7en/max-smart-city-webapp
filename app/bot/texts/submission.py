"""Тексты подачи показаний (бот) и сообщения сервиса подачи (app/readings.py, для API).

Текстовые значения вынесены в yaml/submission.yaml.
"""
from __future__ import annotations

import re

from app.bot.texts.fmt import TYPE_GEN, esc
from app.bot.texts.loader import load_texts

_D = load_texts("submission.yaml")

# Общая оговорка про смоделированную передачу
UK_MOCK: str = _D["UK_MOCK"]

# --- Вход ---
INSTRUCTION: str = _D["INSTRUCTION"]
RETAKE: str = _D["RETAKE"]
ADD_PROMPT: str = _D["ADD_PROMPT"]
PHOTO_RECEIVED: str = _D["PHOTO_RECEIVED"]
PHOTO_REPLACED: str = _D["PHOTO_REPLACED"]
PHOTO_FAILED: str = _D["PHOTO_FAILED"]
PHOTO_GONE: str = _D["PHOTO_GONE"]
PENDING_PHOTO: str = _D["PENDING_PHOTO"]

# --- Выбор счётчика ---
PICK_PHOTO: str = _D["PICK_PHOTO"]
PICK_MANUAL: str = _D["PICK_MANUAL"]
NO_METERS_PHOTO: str = _D["NO_METERS_PHOTO"]
NO_METERS_MANUAL: str = _D["NO_METERS_MANUAL"]
ASK_TYPE: str = _D["ASK_TYPE"]
ASK_TARIFF: str = _D["ASK_TARIFF"]
ASK_ADDRESS: str = _D["ASK_ADDRESS"]
NO_METER: str = _D["NO_METER"]
PAGE_NOTE: str = _D["PAGE_NOTE"]

# --- Новый адрес ---
ASK_NEW_ADDRESS: str = _D["ASK_NEW_ADDRESS"]
ADDRESS_NOT_FOUND: str = _D["ADDRESS_NOT_FOUND"]
ADDRESS_NOT_IN_REGISTRY: str = _D["ADDRESS_NOT_IN_REGISTRY"]
ADDRESS_ONE: str = _D["ADDRESS_ONE"]
ADDRESS_LOCAL_NOTE: str = _D["ADDRESS_LOCAL_NOTE"]
ADDRESS_MANY: str = _D["ADDRESS_MANY"]
ASK_FLAT: str = _D["ASK_FLAT"]
FLAT_ERROR: str = _D["FLAT_ERROR"]
ADDRESS_ALREADY: str = _D["ADDRESS_ALREADY"]

# --- Распознавание ---
LOOKING: str = _D["LOOKING"]
RECOGNIZE_FAILED: str = _D["RECOGNIZE_FAILED"]
FAILED_HEAD: str = _D["FAILED_HEAD"]
FAILED_TAIL: str = _D["FAILED_TAIL"]
FAILED_TAIL_SERVICE: str = _D["FAILED_TAIL_SERVICE"]
ISSUE_TEXTS: dict[str, str] = _D["ISSUE_TEXTS"]
FAILED_SERIAL: str = _D["FAILED_SERIAL"]
MAX_ISSUES: int = _D["MAX_ISSUES"]
NOTE_LIMIT: int = _D["NOTE_LIMIT"]
WRONG_TYPE_WARN: str = _D["WRONG_TYPE_WARN"]
SWITCHED_METER: str = _D["SWITCHED_METER"]
SERIAL_MISMATCH: str = _D["SERIAL_MISMATCH"]
SERIAL_OF_OTHER: str = _D["SERIAL_OF_OTHER"]

# Мини-приложение
API_UNREADABLE: str = _D["API_UNREADABLE"]
API_SERIAL_MISMATCH: str = _D["API_SERIAL_MISMATCH"]
API_PARTIAL: str = _D["API_PARTIAL"]
API_SERIAL_NOT_ON_PHOTO: str = _D["API_SERIAL_NOT_ON_PHOTO"]
API_SERIAL_REQUIRED: str = _D["API_SERIAL_REQUIRED"]
API_SERIAL_BAD: str = _D["API_SERIAL_BAD"]
API_SERIAL_TAKEN: str = _D["API_SERIAL_TAKEN"]


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[а-яёa-z]{4,}", text.casefold())}


def issue_lines(issues: list[str], note: str | None, meter_type: str, *, markup: bool = True) -> list[str]:
    """До MAX_ISSUES причин с советом + пояснение модели, если оно не повторяет причины.
    serial_not_visible показанию не мешает — не показываем. markup=False — для мини-приложения (без esc)."""
    codes = [c for c in dict.fromkeys(issues) if c in ISSUE_TEXTS]
    if note and codes != ["service"]:
        codes = [c for c in codes if c != "other"]  # у «другого» пояснение модели точнее
    lines = [ISSUE_TEXTS[c].format(kind=TYPE_GEN.get(meter_type, "")) for c in codes[:MAX_ISSUES]]
    note = (note or "").strip()
    if note and "service" not in codes:
        if len(note) > NOTE_LIMIT:
            note = note[:NOTE_LIMIT].rsplit(" ", 1)[0] + "…"
        stems, seen = _stems(note), _stems(" ".join(lines))
        if not stems or len(stems & seen) < len(stems) * 0.6:
            note = note[0].upper() + note[1:]
            lines.append((esc(note) if markup else note) + ("" if note[-1] in ".!?…" else "."))
    return lines


def recognize_failed(issues: list[str], note: str | None, meter_type: str, *, need_serial: bool = False) -> str:
    """Экран «не получилось распознать»: что не так и что делать. Нет причин — общий текст.
    need_serial — у счётчика нет сохранённого номера, а на фото его тоже не видно."""
    lines = issue_lines(issues, note, meter_type)
    if need_serial and "serial_not_visible" in issues:
        lines.append(FAILED_SERIAL)
    if not lines:
        return RECOGNIZE_FAILED
    tail = FAILED_TAIL_SERVICE if "service" in issues else FAILED_TAIL
    return "\n".join([FAILED_HEAD, "", *lines, "", tail])


# Прочитали, но с оговорками
REVIEW_WARN: dict[str, str] = _D["REVIEW_WARN"]
TYPICAL_WHOLE: dict[str, str] = _D["TYPICAL_WHOLE"]


def review_warnings(issues: list[str], meter_type: str) -> list[str]:
    """До MAX_ISSUES конкретных предупреждений; «не уверены» — только если конкретных нет."""
    codes = [c for c in dict.fromkeys(issues) if c in REVIEW_WARN and c != "low_confidence"]
    if not codes and "low_confidence" in issues:
        codes = ["low_confidence"]
    return [REVIEW_WARN[c].format(typical=TYPICAL_WHOLE.get(meter_type, "")) for c in codes[:MAX_ISSUES]]


# --- Проверка ---
VALUE_LINE: str = _D["VALUE_LINE"]
TARIFF_LINE: str = _D["TARIFF_LINE"]
SERIAL_MATCH: str = _D["SERIAL_MATCH"]
SERIAL_NEW: str = _D["SERIAL_NEW"]
SERIAL_IGNORED: str = _D["SERIAL_IGNORED"]
SERIAL_REJECTED: str = _D["SERIAL_REJECTED"]
SERIAL_TYPICAL: dict[str, str] = _D["SERIAL_TYPICAL"]
SERIAL_WARN: str = _D["SERIAL_WARN"]
SERIAL_NOT_ON_PHOTO: str = _D["SERIAL_NOT_ON_PHOTO"]
SERIAL_MISSING: str = _D["SERIAL_MISSING"]
ASK_SERIAL: str = _D["ASK_SERIAL"]
SERIAL_EXAMPLES: dict[str, str] = _D["SERIAL_EXAMPLES"]
SERIAL_ERRORS: dict[str, str] = _D["SERIAL_ERRORS"]
SERIAL_TAKEN: str = _D["SERIAL_TAKEN"]
PREV_LINE: str = _D["PREV_LINE"]
PREV_LINE_MULTI: str = _D["PREV_LINE_MULTI"]
CHECK_DIGITS: str = _D["CHECK_DIGITS"]
STUB_NOTE: str = _D["STUB_NOTE"]
REVIEW_QUESTION: str = _D["REVIEW_QUESTION"]

# --- Ручной ввод ---
ASK_VALUE: str = _D["ASK_VALUE"]
ASK_TARIFF_VALUE: str = _D["ASK_TARIFF_VALUE"]
RECOGNIZED_HINT: str = _D["RECOGNIZED_HINT"]
PARTIAL_GOT: str = _D["PARTIAL_GOT"]
ASK_TARIFF_ONLY: str = _D["ASK_TARIFF_ONLY"]
PREV_HINT: str = _D["PREV_HINT"]
PARSE_ERRORS: dict[str, str] = _D["PARSE_ERRORS"]
EXAMPLES: dict[str, str] = _D["EXAMPLES"]

# --- Правдоподобие и повторная подача ---
LESS_THAN_PREV: str = _D["LESS_THAN_PREV"]
TOO_BIG: str = _D["TOO_BIG"]
ALREADY_SUBMITTED: str = _D["ALREADY_SUBMITTED"]
KEPT_OLD: str = _D["KEPT_OLD"]

# --- Готово ---
DONE: str = _D["DONE"]
FLAGGED_DONE: str = _D["FLAGGED_DONE"]
ASK_VERIF: str = _D["ASK_VERIF"]
VERIF_ERRORS: dict[str, str] = _D["VERIF_ERRORS"]
VERIF_SAVED: str = _D["VERIF_SAVED"]
VERIF_LATER: str = _D["VERIF_LATER"]

# --- Кнопки ---
BTN_MANUAL: str = _D["BTN_MANUAL"]
BTN_NEW_METER: str = _D["BTN_NEW_METER"]
BTN_OTHER_ADDRESS: str = _D["BTN_OTHER_ADDRESS"]
TARIFF_BUTTONS: dict[int, str] = {int(k): v for k, v in _D["TARIFF_BUTTONS"].items()}
BTN_YES: str = _D["BTN_YES"]
BTN_REENTER: str = _D["BTN_REENTER"]
BTN_NOT_MINE: str = _D["BTN_NOT_MINE"]
BTN_SAVE_AS_IS: str = _D["BTN_SAVE_AS_IS"]
BTN_FIX: str = _D["BTN_FIX"]
BTN_PRIVATE_HOUSE: str = _D["BTN_PRIVATE_HOUSE"]
BTN_SEND: str = _D["BTN_SEND"]
BTN_EDIT: str = _D["BTN_EDIT"]
BTN_RETAKE: str = _D["BTN_RETAKE"]
BTN_OTHER_METER: str = _D["BTN_OTHER_METER"]
BTN_SAME_METER: str = _D["BTN_SAME_METER"]
BTN_SERIAL: str = _D["BTN_SERIAL"]
BTN_PICK_OTHER: str = _D["BTN_PICK_OTHER"]
BTN_CONFIRM_BIG: str = _D["BTN_CONFIRM_BIG"]
BTN_REPLACE: str = _D["BTN_REPLACE"]
BTN_KEEP_OLD: str = _D["BTN_KEEP_OLD"]
BTN_MORE: str = _D["BTN_MORE"]
BTN_LATER: str = _D["BTN_LATER"]
BTN_PAGE_NEXT: str = _D["BTN_PAGE_NEXT"]
BTN_PAGE_FIRST: str = _D["BTN_PAGE_FIRST"]
LATER_WORDS: set[str] = set(_D["LATER_WORDS"])

# --- Сообщения сервиса подачи ---
API_ACCEPTED: str = _D["API_ACCEPTED"]
API_FLAGGED: str = _D["API_FLAGGED"]
API_NO_ACCESS: str = _D["API_NO_ACCESS"]
API_BAD_FORMAT: str = _D["API_BAD_FORMAT"]
API_EMPTY: str = _D["API_EMPTY"]
API_LESS: str = _D["API_LESS"]
API_NEEDS_CONFIRM: str = _D["API_NEEDS_CONFIRM"]
API_ALREADY: str = _D["API_ALREADY"]
