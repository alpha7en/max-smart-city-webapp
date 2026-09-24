"""Сборка промпта: общий костяк + блок известного типа; без типа — универсальный промпт."""
import pytest

from meter_reader.prompts import ISSUE_CODES, METER_TYPES, RECOGNIZE_PROMPT, build_prompt

SKELETON = ['"drums"', '"color"', "left to right", "3. Identification", "4. Problems",
            "Return JSON exactly in this shape", '"readable"', '"issues"', '"issue_note"']

TYPE_MARKERS = {
    "cold_water": "Known meter type: cold water meter",
    "hot_water": "Known meter type: hot water meter",
    "electricity": "Do NOT read: date, time, voltage",
    "gas": "Do NOT read: temperature, pressure",
    "heat": "accumulated heat",
}


@pytest.mark.parametrize("meter_type", METER_TYPES)
def test_known_type_has_block_and_skeleton(meter_type):
    prompt = build_prompt(meter_type, 1)
    assert "Known meter type:" in prompt
    assert f'"meter_type": "{meter_type}"' in prompt
    assert TYPE_MARKERS[meter_type] in prompt
    for part in SKELETON:
        assert part in prompt
    for code in ISSUE_CODES:
        assert f'"{code}"' in prompt
    for other, marker in TYPE_MARKERS.items():
        if other != meter_type:
            assert marker not in prompt  # чужие типовые блоки не подмешиваются


def test_universal_prompt_without_type():
    for prompt in (build_prompt(), build_prompt(None, 2), build_prompt("unknown"), build_prompt("boiler")):
        assert prompt == RECOGNIZE_PROMPT
    assert "Known meter type" not in RECOGNIZE_PROMPT
    assert "1. Meter type:" in RECOGNIZE_PROMPT and "red means hot, blue means cold" in RECOGNIZE_PROMPT
    assert "5 black drums followed by 3 red drums" in RECOGNIZE_PROMPT
    for part in SKELETON:
        assert part in RECOGNIZE_PROMPT


def test_water_does_not_argue_about_color():
    prompt = build_prompt("hot_water")
    assert "Do NOT re-decide hot vs cold" in prompt
    assert "red means hot, blue means cold" not in prompt
    assert "5 black drums followed by 3 red drums" in prompt


def test_electricity_tariffs_hint():
    assert "3-tariff meter" in build_prompt("electricity", 3)
    assert "2-tariff meter" in build_prompt("electricity", 2)
    assert "-tariff meter" not in build_prompt("electricity", 1)
    assert "-tariff meter" not in build_prompt("electricity", None)
    assert "3-tariff meter" in build_prompt("electricity", 9)  # зажимается в 1..3
    assert "-tariff meter" not in build_prompt("gas", 3)
    assert "{tariffs}" not in build_prompt("electricity", 1)


def test_heat_prompt_units():
    prompt = build_prompt("heat")
    assert "Гкал" in prompt and "MWh" in prompt and "GJ" in prompt and '"Gcal"' in prompt


@pytest.mark.parametrize("meter_type, marker", [
    ("cold_water", '"18-123456"'), ("hot_water", '"18-123456"'), ("electricity", "Энергомера 12-15"),
    ("gas", "usually 7-8 digits"), ("heat", "usually 6-10 digits"),
])
def test_serial_hint_per_type(meter_type, marker):
    prompt = build_prompt(meter_type, 1)
    assert marker in prompt and "seal number" in prompt
    assert prompt.count('"serial_number": usually') + prompt.count('"serial_number": a long') == 1
    assert "seal number" not in RECOGNIZE_PROMPT  # универсальный промпт не меняется
