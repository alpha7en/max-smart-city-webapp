import json
import logging
import re
from typing import Any, Optional

from .config import Settings
from .image_utils import prepare_image
from .llm import LLMError, YandexQwenClient
from .prompts import ISSUE_CODES, METER_TYPES, SYSTEM_PROMPT, build_prompt
from .schemas import MeterReading

log = logging.getLogger("meter_reader")

_METER_TYPES = set(METER_TYPES) | {"unknown"}
_ISSUE_CODES = set(ISSUE_CODES)
_ISSUE_NOTE_MAX = 120

_UNITS = {
    "m3": "m3", "m³": "m3", "м3": "m3", "м³": "m3", "куб.м": "m3",
    "kwh": "kWh", "квт·ч": "kWh", "квт*ч": "kWh", "квтч": "kWh", "квт.ч": "kWh",
    "gcal": "Gcal", "гкал": "Gcal",
    "mwh": "MWh", "мвт·ч": "MWh", "мвт*ч": "MWh", "мвтч": "MWh", "мвт.ч": "MWh",
    "gj": "GJ", "гдж": "GJ",
}
_DEFAULT_UNIT = {"cold_water": "m3", "hot_water": "m3", "gas": "m3", "electricity": "kWh", "heat": "Gcal"}

# Fallback explanations for the user when the model gave no issue_note.
_ISSUE_NOTES = {
    "no_meter": "На фото не видно счётчика",
    "wrong_type": "На фото счётчик другого типа, чем выбран",
    "digits_not_visible": "Не видно цифр показания",
    "blurry": "Фото размыто",
    "glare": "Блик мешает прочитать цифры",
    "too_dark": "Фото слишком тёмное",
    "angle": "Счётчик снят под сильным углом",
    "partially_covered": "Цифры частично закрыты",
    "multiple_meters": "На фото несколько счётчиков",
    "serial_not_visible": "Не удалось прочитать заводской номер",
    "display_off": "Экран счётчика не показывает показание",
    "other": "Не удалось уверенно прочитать показание",
}


def _extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise LLMError(f"no JSON in model answer: {text[:300]}")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise LLMError(f"invalid JSON in model answer: {exc}; {text[:300]}") from exc


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value)) if value is not None else ""


def _str_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.lower() not in {"null", "none", "unknown"} else None


_SERIAL_PREFIX = re.compile(r"^(?:№|#|N[°º]|No\.|S/?N:?)\s*", re.IGNORECASE)


def _serial(value: Any) -> Optional[str]:
    """Serial as printed, without a leading "№"/"No."/"S/N" and edge spaces/punctuation."""
    text = _str_or_none(value)
    if not text:
        return None
    text = _SERIAL_PREFIX.sub("", " ".join(text.split())).strip(" .,:;")
    return text or None


def _split_drums(raw: dict[str, Any]) -> tuple[str, str]:
    """Build integer and fractional parts from per-drum answer: black drums, then red ones."""
    drums = raw.get("drums")
    if not isinstance(drums, list):
        return _digits(raw.get("integer_digits")), _digits(raw.get("fraction_digits"))

    integer, fraction = "", ""
    for drum in drums:
        if not isinstance(drum, dict):
            continue
        digit = _digits(drum.get("digit"))[:1]
        if not digit:
            continue
        if str(drum.get("color", "")).lower() == "red" or fraction:
            fraction += digit
        else:
            integer += digit
    return integer, fraction


def _normalize_water_split(integer: str, fraction: str) -> tuple[str, str]:
    """Russian water meters with 8 drums are practically always 5 black + 3 red.

    The model sometimes misjudges the color of the drum at the boundary, so fix the split.
    """
    digits = integer + fraction
    if len(digits) == 8 and len(integer) != 5:
        return digits[:5], digits[5:]
    return integer, fraction


def _normalize_unit(value: Any, meter_type: str) -> Optional[str]:
    text = _str_or_none(value)
    if text:
        key = text.lower().replace(" ", "")
        if key in _UNITS:
            return _UNITS[key]
    return _DEFAULT_UNIT.get(meter_type)


def _normalize_tariff(value: Any) -> Optional[str]:
    text = _str_or_none(value)
    if text:
        match = re.fullmatch(r"[TtТт]\s*([1-3])", text)
        if match:
            return f"T{match.group(1)}"
    return text


def _normalize_issues(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    issues: list[str] = []
    for item in value:
        code = str(item).strip().lower().replace("-", "_").replace(" ", "_")
        if not code or code in {"none", "null"}:
            continue
        code = code if code in _ISSUE_CODES else "other"
        if code not in issues:
            issues.append(code)
    return issues


def log_summary(raw: Any, reading: "MeterReading") -> dict[str, Any]:
    """Short answer for logs: no image, no serial/brand values (only whether a serial was read)."""
    return {
        "type": reading.meter_type, "reading": reading.reading_text, "tariff": reading.tariff,
        "confidence": reading.confidence, "readable": reading.readable, "issues": reading.issues,
        "note": reading.issue_note, "serial": reading.serial_number is not None,
        "raw_readable": raw.get("readable") if isinstance(raw, dict) else None,
    }


def _bool_or_none(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    return None


def _to_reading(raw: dict[str, Any], expected_type: Optional[str] = None) -> MeterReading:
    """Model answer → MeterReading. Never returns an "empty success": no digits means readable=false + issue."""
    integer, fraction = _split_drums(raw)
    if raw.get("meter_type") in {"hot_water", "cold_water"}:
        integer, fraction = _normalize_water_split(integer, fraction)

    reading_text = None
    reading = None
    if integer or fraction:
        reading_text = f"{integer or '0'}.{fraction}" if fraction else integer
        reading = float(reading_text)

    meter_type = raw.get("meter_type")
    if meter_type not in _METER_TYPES:
        meter_type = "unknown"

    issues = _normalize_issues(raw.get("issues"))
    if expected_type in METER_TYPES and meter_type not in {expected_type, "unknown"} and "wrong_type" not in issues:
        issues.append("wrong_type")

    has_digits = bool(integer or fraction)
    readable = _bool_or_none(raw.get("readable"))
    readable = has_digits if readable is None else (readable and has_digits)
    if not has_digits and not issues:
        issues.append("digits_not_visible")

    issue_note = _str_or_none(raw.get("issue_note"))
    if issue_note is None and issues and (not readable or "wrong_type" in issues):
        main = next((i for i in issues if i != "serial_not_visible"), issues[0])
        issue_note = _ISSUE_NOTES[main]
    if issue_note and len(issue_note) > _ISSUE_NOTE_MAX:
        issue_note = issue_note[: _ISSUE_NOTE_MAX - 1].rstrip() + "…"

    confidence = raw.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = None

    return MeterReading(
        meter_type=meter_type,
        reading=reading,
        reading_text=reading_text,
        integer_digits=integer or None,
        fraction_digits=fraction or None,
        unit=_normalize_unit(raw.get("unit"), meter_type),
        tariff=_normalize_tariff(raw.get("tariff")),
        brand=_str_or_none(raw.get("brand")),
        model=_str_or_none(raw.get("model")),
        serial_number=_serial(raw.get("serial_number")),
        confidence=confidence,
        type_evidence=_str_or_none(raw.get("type_evidence")),
        readable=readable,
        issues=issues,
        issue_note=issue_note,
    )


class MeterRecognizer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = YandexQwenClient(settings)

    async def recognize(
        self,
        image_bytes: bytes,
        attempts: int = 2,
        *,
        meter_type: Optional[str] = None,
        tariffs: Optional[int] = None,
    ) -> MeterReading:
        """meter_type/tariffs are hints from the user; without them the universal prompt is used."""
        expected = meter_type if meter_type in METER_TYPES else None
        prompt = build_prompt(expected, tariffs)
        image_url = prepare_image(image_bytes, self.settings.max_image_side)
        for attempt in range(attempts):
            # With temperature 0 a retry would repeat the same broken answer.
            temperature = 0.0 if attempt == 0 else 0.3
            answer = await self.client.ask_with_image(
                SYSTEM_PROMPT, prompt, image_url, temperature
            )
            try:
                raw = _extract_json(answer)
                reading = _to_reading(raw, expected)
                log.info("recognized (%s, tariffs=%s): %s", expected, tariffs, log_summary(raw, reading))
                return reading
            except LLMError:
                if attempt == attempts - 1:
                    raise
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        await self.client.aclose()
