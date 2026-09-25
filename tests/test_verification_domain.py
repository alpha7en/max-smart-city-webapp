"""Поверка по ФГИС «Аршин»: разбор ответа, даты, варианты номера, выбор записи (domain/verification.py)."""
from __future__ import annotations

from datetime import date

from app.domain.verification import (
    Record,
    card_url,
    choose,
    parse_date,
    parse_list,
    search_start,
    serial_variants,
    short_title,
    type_fit,
)

# Пример ответа из руководства «Внешние публичные интерфейсы» v2.2 (§ vri) — термометр, не счётчик.
DOC = {"result": {"count": 168142985, "start": 0, "rows": 10, "items": [
    {"mit_notation": "ДТС", "valid_date": "2021-10-07T12:00:00Z", "result_docnum": "Нет данных",
     "org_title": "ООО \"ЗАВОД № 423\"", "mi_number": "09387191044377599", "applicability": True,
     "mit_title": "Термометры сопротивления (Термопреобразователи сопротивления)", "vri_id": "2-166964556",
     "verification_date": "2019-10-08T12:00:00Z", "mit_number": "28354-10"}]}}


def rec(vri: str, title: str = "Счетчики холодной и горячей воды", number: str = "1-01", notation: str = "",
        vdate: date = date(2024, 2, 7), valid: date | None = date(2030, 2, 6), fit: bool = True,
        modification: str = "") -> Record:
    return Record(vri_id=vri, mit_number=number, mit_title=title, mit_notation=notation, mi_modification=modification,
                  mi_number="123456", verification_date=vdate, valid_date=valid, applicable=fit)


def test_dates_iso_and_ddmmyyyy():
    assert parse_date("2021-10-07T12:00:00Z") == date(2021, 10, 7)
    assert parse_date("2021-10-07") == date(2021, 10, 7)
    assert parse_date("07.02.2024") == date(2024, 2, 7)
    assert parse_date("31.02.2024") is None and parse_date("") is None and parse_date(None) is None
    assert parse_date("Нет данных") is None and parse_date(20240207) is None


def test_parse_doc_example_and_missing_fields():
    (r,) = parse_list(DOC)
    assert (r.vri_id, r.mit_number, r.valid_date, r.verification_date, r.applicable) == (
        "2-166964556", "28354-10", date(2021, 10, 7), date(2019, 10, 8), True)
    (bare,) = parse_list({"result": {"items": [{"vri_id": "1-5", "applicability": False,
                                                "verification_date": "07.02.2024"}, {"mit_title": "без id"}]}})
    assert bare.unfit and bare.valid_date is None and bare.mit_title == "" and bare.verification_date == date(2024, 2, 7)
    assert parse_list({"result": {"count": 0, "items": []}}) == []
    assert parse_list({"status": "BAD_REQUEST"}) is None and parse_list("<html>") is None
    assert Record.from_dict(r.to_dict()) == r


def test_search_start_skips_current_year_trap():
    today = date(2026, 10, 19)
    assert search_start("cold_water", today) == date(2019, 10, 19)   # МПИ 6 + 1 год
    assert search_start("electricity", today) == date(2009, 10, 19)
    assert search_start("gas", date(2028, 2, 29)) == date(2015, 2, 28)


def test_serial_variants():
    assert serial_variants("18-123456", "cold_water") == ["18-123456", "18123456", "123456"]
    assert serial_variants("18-123456", "gas") == ["18-123456", "18123456"]  # год-префикс — только у воды
    assert serial_variants("0112 3456 7890", "electricity") == ["0112 3456 7890", "011234567890", "11234567890"]
    assert serial_variants("12345678", "cold_water") == ["12345678"]
    assert serial_variants("№ 00123", "heat") == ["00123"]  # «123» — слишком коротко для поиска
    assert serial_variants(None, "gas") == []


def test_type_fit():
    assert type_fit("Счетчики холодной и горячей воды", "hot_water") == 1
    assert type_fit("Счётчики воды крыльчатые", "cold_water") == 1
    assert type_fit("Счетчики холодной воды", "hot_water") == -1
    assert type_fit("Счетчики горячей воды", "cold_water") == -1
    assert type_fit("Счетчики электрической энергии однофазные", "electricity") == 1
    assert type_fit("Теплосчетчики (счетчики тепловой энергии)", "electricity") == -1
    assert type_fit("Теплосчетчики (счетчики тепловой энергии)", "heat") == 1
    assert type_fit("Счетчики газа объемные диафрагменные", "gas") == 1
    assert type_fit("Газоанализаторы", "gas") == -1
    assert type_fit("Манометры показывающие", "cold_water") == -1
    assert type_fit("", "gas") == 0


def test_collision_other_types_filtered_out():
    m = choose([rec("1-1", "Манометры", "11-11"), rec("1-2", number="22-22")], "cold_water")
    assert (m.level, m.best.vri_id) == ("high", "1-2")


def test_latest_in_group():
    old, new = rec("1-1", vdate=date(2018, 3, 1)), rec("1-2", vdate=date(2024, 3, 1), valid=date(2030, 2, 28))
    m = choose([new, old], "hot_water")
    assert (m.level, m.best.vri_id, m.best.valid_date) == ("high", "1-2", date(2030, 2, 28))


def test_several_groups_low_and_brand_decides():
    a = rec("1-1", number="11-11", notation="СГВ", modification="СГВ-15")
    b = rec("1-2", "Счетчики воды крыльчатые", number="22-22", notation="ВСКМ", vdate=date(2025, 1, 1))
    c = rec("1-3", "Счетчики воды", number="33-33", notation="Пульсар")
    d = rec("1-4", "Счетчики воды", number="44-44", notation="Норма")
    low = choose([a, b, c, d], "cold_water")
    assert low.level == "low" and low.best is None and len(low.options) == 3
    assert low.options[0].vri_id == "1-2"  # без марки — свежая поверка первой
    hit = choose([a, b], "cold_water", brand="Декаст", model="СГВ-15")
    assert (hit.level, hit.best.vri_id) == ("high", "1-1")
    assert choose([a, b], "cold_water", brand="сгв").best.vri_id == "1-1"  # регистр и кириллица-двойник
    assert choose([a, b], "cold_water", brand="ВС").level == "low"  # короче 3 символов — не признак


def test_type_not_confirmed_is_low():
    m = choose([rec("1-1", title="")], "gas")
    assert m.level == "low" and [r.vri_id for r in m.options] == ["1-1"]


def test_unfit_and_none():
    m = choose([rec("1-1", vdate=date(2020, 1, 1)), rec("1-2", vdate=date(2025, 1, 1), valid=None, fit=False)],
               "cold_water")
    assert m.level == "high" and m.best.unfit and m.best.valid_date is None
    assert choose([], "gas").level == "none"
    assert choose([rec("1-1", "Манометры")], "gas").level == "none"


def test_card_url_and_title():
    assert card_url("2-166964556") == "https://fgis.gost.ru/fundmetrology/cm/results/2-166964556"
    assert card_url("166964556") == "https://fgis.gost.ru/fundmetrology/cm/results/1-166964556"
    assert card_url("demo-w1") is None and card_url(None) is None and card_url("1-2/../x") is None
    assert short_title(rec("1", notation="СГВ")) == "СГВ"
    long = short_title(rec("1", title="Счетчики электрической энергии статические однофазные"))
    assert len(long) == 22 and long.endswith("…")
