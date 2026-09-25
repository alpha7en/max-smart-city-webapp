"""Серийные номера (app/domain/serials.py): очистка, ключ сравнения, показ и проверка по типу счётчика."""
import pytest

from app.domain import meters as M
from app.domain.serials import clean_serial, format_serial, normalize_serial, usable_serial, validate_serial


@pytest.mark.parametrize("raw, clean", [
    (None, None), ("", None), ("  ", None), ("№", None),
    ("№ 18-123456", "18-123456"), ("№18123456", "18123456"), ("N° 0412345", "0412345"), ("No. 123456", "123456"),
    ("S/N: 011234567890", "011234567890"), ("Зав. № 12345678", "12345678"), ("заводской номер 12345678", "12345678"),
    ("  18 - 123456 .", "18-123456"), ("«18–123456»", "18-123456"), ("ВК  1234  5678", "ВК 1234 5678"),
])
def test_clean_serial(raw, clean):
    assert clean_serial(raw) == clean


def test_normalize_is_comparison_key():
    same = ["18-123 456", "№ 18123456", "18.123.456", " 18123456 ", "18/123456"]
    assert {normalize_serial(s) for s in same} == {"18123456"}
    assert normalize_serial("АВ-7712345") == normalize_serial("ab7712345")  # кириллица-двойник
    assert normalize_serial("0123456") != normalize_serial("123456")        # ведущие нули значимы
    assert normalize_serial(None) is None and normalize_serial("№ ") is None
    assert M.normalize_serial is normalize_serial  # старый импорт из meters работает


@pytest.mark.parametrize("raw, mtype, shown", [
    ("№ 18-123456", "cold_water", "18-123456"),       # у воды разделитель года как на шильдике
    ("18 - 123456", "hot_water", "18-123456"),
    ("0112 3456 7890", "electricity", "011234567890"),  # у света цифры слитно
    ("№ 12 345 678", "gas", "12345678"),
    ("вк-g4 1234567", "gas", "ВК-G4 1234567"),
    ("123-456", "heat", "123456"),
    (None, "gas", None),
])
def test_format_serial(raw, mtype, shown):
    assert format_serial(raw, mtype) == shown


@pytest.mark.parametrize("raw, mtype, level, code", [
    ("18-123456", "cold_water", "ok", None),
    ("№ 0123456", "hot_water", "ok", None),
    ("12345678", "electricity", "ok", None),          # Меркурий
    ("009217063001234", "electricity", "ok", None),   # Энергомера, 15 цифр
    ("1234567", "gas", "ok", None),                   # BK-G4
    ("12345678", "heat", "ok", None),
    ("12345", "cold_water", "warning", "unusual_length"),
    ("123456", "electricity", "warning", "unusual_length"),
    ("12345678901234567", "electricity", "warning", "unusual_length"),
    ("ABCDEF123456", "gas", "warning", "many_letters"),
    ("2018", "cold_water", "bad", "not_serial"),      # год выпуска
    ("2018 г.", "gas", "bad", "not_serial"),
    ("ГОСТ 50193", "cold_water", "bad", "not_serial"),
    ("Qn 1,5", "cold_water", "bad", "not_serial"),
    ("DN 15", "hot_water", "bad", "not_serial"),
    ("пломба 123456", "gas", "bad", "not_serial"),
    ("12.03.2018", "heat", "bad", "not_serial"),
    ("abc", "gas", "bad", "not_serial"),
    ("123", "gas", "bad", "too_short"),
    ("", "gas", "bad", "empty"),
])
def test_validate_serial(raw, mtype, level, code):
    check = validate_serial(raw, mtype)
    assert (check.level, check.code) == (level, code)
    assert check.usable == (level != "bad")
    assert (usable_serial(raw, mtype) is None) == (level == "bad")


def test_clean_serial_unicode_artifacts():
    assert clean_serial("\ufffc598048919") == "598048919"
    assert clean_serial("\ufeff18-452178") == "18-452178"
    assert normalize_serial("\ufffc598048919") == "598048919"
    assert normalize_serial("\u200b0112456\u200c") == "0112456"

