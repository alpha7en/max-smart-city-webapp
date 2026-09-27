"""Серийные номера (app/domain/serials.py): очистка и ключ сравнения."""
import pytest

from app.domain.serials import clean_serial, normalize_serial


@pytest.mark.parametrize("raw, clean", [
    (None, None), ("№", None), ("№ 18-123456", "18-123456"), ("S/N: 011234567890", "011234567890"),
    ("Зав. № 12345678", "12345678"), ("  18 - 123456 .", "18-123456"), ("«18–123456»", "18-123456"),
])
def test_clean_serial(raw, clean):
    assert clean_serial(raw) == clean


def test_normalize_is_comparison_key():
    same = ["18-123 456", "№ 18123456", "18.123.456", " 18123456 ", "18/123456"]
    assert {normalize_serial(s) for s in same} == {"18123456"}
    assert normalize_serial("АВ-7712345") == normalize_serial("ab7712345")  # кириллица-двойник
    assert normalize_serial("0123456") != normalize_serial("123456")        # ведущие нули значимы
    assert normalize_serial(None) is None and normalize_serial("№ ") is None
