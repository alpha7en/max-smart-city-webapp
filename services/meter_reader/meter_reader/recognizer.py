import json
import re
from typing import Any, Optional

from .config import Settings
from .image_utils import prepare_image
from .llm import LLMError, YandexQwenClient
from .prompts import RECOGNIZE_PROMPT, SYSTEM_PROMPT
from .schemas import MeterReading

_METER_TYPES = {"hot_water", "cold_water", "electricity", "gas", "unknown"}


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


def _to_reading(raw: dict[str, Any]) -> MeterReading:
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
        unit=_str_or_none(raw.get("unit")),
        tariff=_str_or_none(raw.get("tariff")),
        brand=_str_or_none(raw.get("brand")),
        model=_str_or_none(raw.get("model")),
        serial_number=_str_or_none(raw.get("serial_number")),
        confidence=confidence,
        type_evidence=_str_or_none(raw.get("type_evidence")),
    )


class MeterRecognizer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = YandexQwenClient(settings)

    async def recognize(self, image_bytes: bytes, attempts: int = 2) -> MeterReading:
        image_url = prepare_image(image_bytes, self.settings.max_image_side)
        for attempt in range(attempts):
            # With temperature 0 a retry would repeat the same broken answer.
            temperature = 0.0 if attempt == 0 else 0.3
            answer = await self.client.ask_with_image(
                SYSTEM_PROMPT, RECOGNIZE_PROMPT, image_url, temperature
            )
            try:
                return _to_reading(_extract_json(answer))
            except LLMError:
                if attempt == attempts - 1:
                    raise
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        await self.client.aclose()
