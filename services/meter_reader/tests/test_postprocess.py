"""Постобработка ответа модели без сети: барабаны → показание, 5 + 3 у водомера, битый JSON."""
import pytest

from meter_reader.llm import LLMError
from meter_reader.recognizer import _extract_json, _to_reading


def drums(black: str, red: str) -> list[dict]:
    return [{"digit": d, "color": "black"} for d in black] + [{"digit": d, "color": "red"} for d in red]


def test_drums_to_reading():
    r = _to_reading({"meter_type": "hot_water", "drums": drums("00595", "825"), "serial_number": "null"})
    assert (r.reading_text, r.reading, r.integer_digits, r.fraction_digits) == ("00595.825", 595.825, "00595", "825")
    assert r.serial_number is None


def test_water_eight_drums_forced_to_5_plus_3():
    r = _to_reading({"meter_type": "cold_water", "drums": drums("0059", "5825")})
    assert r.reading_text == "00595.825"


def test_unknown_type_and_bad_confidence():
    r = _to_reading({"meter_type": "boiler", "drums": [], "confidence": "high"})
    assert (r.meter_type, r.reading, r.confidence) == ("unknown", None, None)


def test_json_inside_text_and_broken_json():
    assert _extract_json('Ответ: {"meter_type": "gas"} готово') == {"meter_type": "gas"}
    with pytest.raises(LLMError):
        _extract_json("{meter_type: gas")
