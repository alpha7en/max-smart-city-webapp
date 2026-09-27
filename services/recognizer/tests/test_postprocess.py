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


def test_json_inside_text_and_broken_json():
    assert _extract_json('Ответ: {"meter_type": "gas"} готово') == {"meter_type": "gas"}
    with pytest.raises(LLMError):
        _extract_json("{meter_type: gas")


def test_empty_drums_is_not_readable():
    r = _to_reading({"meter_type": "cold_water", "drums": [], "readable": True, "issues": []}, "cold_water")
    assert (r.readable, r.reading, r.issues) == (False, None, ["digits_not_visible"])
    assert r.issue_note  # человеку всегда есть что показать


def test_wrong_type_added_when_type_differs():
    r = _to_reading({"meter_type": "electricity", "drums": drums("012345", "6"), "unit": "kWh"}, "cold_water")
    assert "wrong_type" in r.issues and r.issue_note
    r = _to_reading({"meter_type": "unknown", "drums": [], "issues": ["no_meter"]}, "gas")
    assert r.issues == ["no_meter"]
    # без переданного типа несовпадать не с чем
    assert _to_reading({"meter_type": "gas", "drums": drums("1", "2")}).issues == []


@pytest.mark.parametrize("raw, serial", [
    ("№ 18-123456", "18-123456"), ("  №0412345 ", "0412345"), ("No. 12345678", "12345678"),
    ("S/N: 011234567890", "011234567890"), ("18 123456.", "18 123456"), ("№", None), ("null", None), (None, None),
])
def test_serial_number_cleaned(raw, serial):
    r = _to_reading({"meter_type": "cold_water", "drums": drums("00001", "234"), "serial_number": raw}, "cold_water")
    assert r.serial_number == serial
