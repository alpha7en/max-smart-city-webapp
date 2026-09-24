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


def test_empty_drums_is_not_readable():
    r = _to_reading({"meter_type": "cold_water", "drums": [], "readable": True, "issues": []}, "cold_water")
    assert (r.readable, r.reading, r.issues) == (False, None, ["digits_not_visible"])
    assert r.issue_note  # человеку всегда есть что показать


def test_readable_reading_without_issues():
    r = _to_reading({"meter_type": "gas", "drums": drums("01234", "567"), "readable": True, "issues": []}, "gas")
    assert (r.readable, r.issues, r.issue_note, r.unit) == (True, [], None, "m3")


def test_model_issues_kept_and_empty_drums():
    r = _to_reading({"meter_type": "hot_water", "drums": [], "readable": False, "issues": ["glare"],
                     "issue_note": "Блик закрывает последние цифры"}, "hot_water")
    assert (r.readable, r.issues, r.issue_note) == (False, ["glare"], "Блик закрывает последние цифры")


def test_wrong_type_added_when_type_differs():
    r = _to_reading({"meter_type": "electricity", "drums": drums("012345", "6"), "unit": "kWh"}, "cold_water")
    assert "wrong_type" in r.issues and r.issue_note
    r = _to_reading({"meter_type": "unknown", "drums": [], "issues": ["no_meter"]}, "gas")
    assert r.issues == ["no_meter"]
    # без переданного типа несовпадать не с чем
    assert _to_reading({"meter_type": "gas", "drums": drums("1", "2")}).issues == []


def test_issues_normalized():
    r = _to_reading({"meter_type": "gas", "drums": drums("00012", "345"), "readable": True,
                     "issues": ["Glare", "glare", "smudge", "serial-not-visible", None, "cracked"]}, "gas")
    assert r.issues == ["glare", "other", "serial_not_visible"]
    assert r.readable is True
    assert _to_reading({"meter_type": "gas", "drums": drums("1", ""), "issues": "blurry"}).issues == ["blurry"]


def test_long_issue_note_trimmed():
    r = _to_reading({"meter_type": "gas", "drums": [], "issues": ["blurry"], "issue_note": "а" * 300})
    assert len(r.issue_note) <= 120


def test_heat_units():
    r = _to_reading({"meter_type": "heat", "drums": drums("0012", "345"), "unit": "Гкал"}, "heat")
    assert (r.meter_type, r.unit, r.reading_text) == ("heat", "Gcal", "0012.345")
    assert _to_reading({"meter_type": "heat", "drums": drums("5", "1"), "unit": "MWh"}).unit == "MWh"
    assert _to_reading({"meter_type": "heat", "drums": drums("5", "1"), "unit": "ГДж"}).unit == "GJ"
    assert _to_reading({"meter_type": "heat", "drums": drums("5", "1"), "unit": None}).unit == "Gcal"
    assert _to_reading({"meter_type": "heat", "drums": drums("5", "1"), "unit": "parsecs"}).unit == "Gcal"


def test_tariff_normalized():
    r = _to_reading({"meter_type": "electricity", "drums": drums("12345", "6"), "tariff": "Т2"}, "electricity")
    assert (r.tariff, r.unit) == ("T2", "kWh")
