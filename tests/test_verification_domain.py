"""Поверка по ФГИС «Аршин»: выбор записи (domain/verification.py)."""
from __future__ import annotations

from datetime import date

from app.domain.verification import Record, choose

# Пример ответа из руководства «Внешние публичные интерфейсы» v2.2 (§ vri) — термометр, не счётчик.


def rec(vri: str, title: str = "Счетчики холодной и горячей воды", number: str = "1-01", notation: str = "",
        vdate: date = date(2024, 2, 7), valid: date | None = date(2030, 2, 6), fit: bool = True,
        modification: str = "") -> Record:
    return Record(vri_id=vri, mit_number=number, mit_title=title, mit_notation=notation, mi_modification=modification,
                  mi_number="123456", verification_date=vdate, valid_date=valid, applicable=fit)


def test_collision_other_types_filtered_out():
    m = choose([rec("1-1", "Манометры", "11-11"), rec("1-2", number="22-22")], "cold_water")
    assert (m.level, m.best.vri_id) == ("high", "1-2")


def test_latest_in_group():
    old, new = rec("1-1", vdate=date(2018, 3, 1)), rec("1-2", vdate=date(2024, 3, 1), valid=date(2030, 2, 28))
    m = choose([new, old], "hot_water")
    assert (m.level, m.best.vri_id, m.best.valid_date) == ("high", "1-2", date(2030, 2, 28))


def test_unfit_and_none():
    m = choose([rec("1-1", vdate=date(2020, 1, 1)), rec("1-2", vdate=date(2025, 1, 1), valid=None, fit=False)],
               "cold_water")
    assert m.level == "high" and m.best.unfit and m.best.valid_date is None
    assert choose([], "gas").level == "none"
    assert choose([rec("1-1", "Манометры")], "gas").level == "none"
