"""Сервис подачи app/readings.py: все статусы, порядок проверок (C5), откат, замена + домен S2."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from app import clock
from app.domain import meters as M
from app.domain.dashboard import load_dashboard
from app.readings import SubmitResult, format_values, submit_reading, to_units, values_to_units
from app.web.api import iso
from tests.conftest import NOW

TODAY = NOW.date()  # 2026-10-19 → период 2026-10
ADDR = {"full_text": "г Москва, ул Арбат, д 47, кв 32", "region": "Москва", "locality": "г Москва",
        "street": "ул Арбат", "house": "47", "block": "к1", "flat": "32", "status": "unverified", "source": "local"}


async def _user(repo, uid: int, key: str = "k1") -> tuple[int, int]:
    u = await repo.ensure_user(uid, uid)
    r = await repo.complete_registration(u["id"], full_name="Иванова Анна", phone="+79123456789",
                                         phone_verified=True, address=ADDR, norm_key=key, raw_input=None, now=NOW)
    return u["id"], r["address_id"]


@pytest.fixture
async def owner(repo):
    return await _user(repo, 1)


async def _meter(repo, owner, mtype="cold_water", tariffs=1, serial=None, readings=()) -> int:
    uid, aid = owner
    mid = await repo.create_meter(aid, mtype, tariffs, serial, uid)
    for period, t1 in readings:
        await repo.add_reading(mid, uid, period, {"t1": t1}, "manual")
    return mid


async def submit(repo, uid, **kw) -> SubmitResult:
    base = {"meter_id": None, "draft": None, "source": "manual", "recognized": None, "today": TODAY}
    return await submit_reading(repo, user_id=uid, **{**base, **kw})


async def test_accepted_with_previous_and_delta(repo, owner):
    mid = await _meter(repo, owner, readings=[("2026-09", 118_200)])
    res = await submit(repo, owner[0], meter_id=mid, values={"t1": "123,456"})
    assert (res.status, res.ok, res.period, res.months) == ("accepted", True, "2026-10", 1)
    assert res.previous["t1"] == 118_200 and res.delta == {"t1": 5_256} and res.values["t1"] == 123_456
    row = (await repo.history(mid))[0]
    assert (row["id"], row["t1"], row["status"], row["source"]) == (res.reading_id, 123_456, "accepted", "manual")
    assert "смоделирована" in res.message


async def test_first_reading_only_format(repo, owner):
    mid = await _meter(repo, owner)
    res = await submit(repo, owner[0], meter_id=mid, values={"t1": "99999,999"})
    assert res.status == "accepted" and res.previous is None and res.delta is None


@pytest.mark.parametrize("value,needle", [("12,34,5", "123,456"), ("123,4567", "123,456"), ("-5", "123,456"),
                                          ("abc", "123,456"), ("", "123,456")])
async def test_bad_format(repo, owner, value, needle):
    mid = await _meter(repo, owner)
    res = await submit(repo, owner[0], meter_id=mid, values={"t1": value})
    assert res.status == "bad_format" and needle in res.message
    assert await repo.history(mid) == []


async def test_bad_format_names_tariff_field(repo, owner):
    mid = await _meter(repo, owner, "electricity", 2)
    res = await submit(repo, owner[0], meter_id=mid, values={"t1": "100"})  # нет t2
    assert res.status == "bad_format" and "Т2" in res.message


async def test_less_than_previous_is_hard(repo, owner):
    mid = await _meter(repo, owner, readings=[("2026-09", 118_200)])
    res = await submit(repo, owner[0], meter_id=mid, values={"t1": "100"}, confirm=True)
    assert res.status == "less_than_previous" and res.previous == {"t1": 118_200, "t2": None, "t3": None}
    assert "118,200" in res.message and len(await repo.history(mid)) == 1


async def test_needs_confirm_then_flagged(repo, owner):
    mid = await _meter(repo, owner, readings=[("2026-09", 100_000)])
    res = await submit(repo, owner[0], meter_id=mid, values={"t1": "131"})  # +31 > 30
    assert res.status == "needs_confirm" and "+31,000 м³" in res.message and res.reading_id is None
    res = await submit(repo, owner[0], meter_id=mid, values={"t1": "131"}, confirm=True)
    assert res.status == "flagged" and (await repo.history(mid))[0]["status"] == "flagged"


async def test_threshold_multiplied_by_months(repo, owner):
    mid = await _meter(repo, owner, readings=[("2026-07", 100_000)])  # 3 мес. назад: порог 90
    res = await submit(repo, owner[0], meter_id=mid, values={"t1": "185"})
    assert (res.status, res.months) == ("accepted", 3)
    mid2 = await _meter(repo, owner, "hot_water", readings=[("2026-07", 100_000)])
    assert (await submit(repo, owner[0], meter_id=mid2, values={"t1": "161"})).status == "needs_confirm"


async def test_electricity_sum_of_tariffs(repo, owner):
    uid, aid = owner
    mid = await repo.create_meter(aid, "electricity", 2, None, uid)
    await repo.add_reading(mid, uid, "2026-09", {"t1": 1_000_000, "t2": 500_000}, "manual")
    res = await submit(repo, uid, meter_id=mid, values={"t1": "1800", "t2": "1300"})  # +800 +800 > 1500
    assert res.status == "needs_confirm" and res.delta == {"t1": 800_000, "t2": 800_000}
    res = await submit(repo, uid, meter_id=mid, values={"t1": "1800", "t2": "1100", "t3": "ignored"})
    assert res.status == "accepted" and res.values == {"t1": 1_800_000, "t2": 1_100_000, "t3": None}


async def test_already_submitted_and_replace_compares_with_previous_period(repo, owner):
    mid = await _meter(repo, owner, readings=[("2026-09", 100_000), ("2026-10", 120_000)])
    res = await submit(repo, owner[0], meter_id=mid, values={"t1": "110"})
    assert res.status == "already_submitted" and res.previous["t1"] == 120_000 and "октябрь" in res.message
    # Меньше уже поданного за октябрь, но больше сентябрьского — с заменой это нормально.
    res = await submit(repo, owner[0], meter_id=mid, values={"t1": "110"}, replace=True)
    assert res.status == "accepted" and res.previous["t1"] == 100_000
    rows = await repo._all("SELECT period, t1, status FROM readings WHERE meter_id=? ORDER BY id", (mid,))
    assert rows == [{"period": "2026-09", "t1": 100_000, "status": "accepted"},
                    {"period": "2026-10", "t1": 120_000, "status": "replaced"},
                    {"period": "2026-10", "t1": 110_000, "status": "accepted"}]


async def test_check_order_already_before_less(repo, owner):
    mid = await _meter(repo, owner, readings=[("2026-09", 100_000), ("2026-10", 120_000)])
    assert (await submit(repo, owner[0], meter_id=mid, values={"t1": "1"})).status == "already_submitted"
    assert (await submit(repo, owner[0], meter_id=mid, values={"t1": "x"})).status == "bad_format"


async def test_no_access(repo, owner):
    mid = await _meter(repo, owner)
    tenant, _ = await _user(repo, 2)  # тот же адрес → tenant/pending
    assert (await submit(repo, tenant, meter_id=mid, values={"t1": "x"})).status == "no_access"  # права раньше формата
    assert (await submit(repo, owner[0], meter_id=999, values={"t1": "1"})).status == "no_access"
    res = await submit(repo, tenant, draft={"address_id": owner[1], "type": "gas"}, values={"t1": "1"})
    assert res.status == "no_access" and len(await repo.address_meters(owner[1])) == 1


async def test_draft_creates_meter_with_reading_and_serial(repo, owner):
    uid, aid = owner
    rec = {"t1": 1_234_560, "t2": 600_000, "serial": "18-4521", "confidence": 0.9, "stub": True}
    res = await submit(repo, uid, draft={"address_id": aid, "type": "electricity", "tariffs": 2, "serial": "18-4521"},
                       values={"t1": 1_234_560, "t2": 600_000}, source="photo", recognized=rec)
    meter = await repo.get_meter(res.meter_id)
    assert (res.status, meter["tariffs"], meter["serial"], meter["created_by"]) == ("accepted", 2, "18-4521", uid)
    assert (await repo.history(res.meter_id))[0]["recognized_json"]


async def test_draft_with_existing_serial_uses_that_meter(repo, owner):
    mid = await _meter(repo, owner, serial="18 4521", readings=[("2026-09", 100_000)])
    res = await submit(repo, owner[0], draft={"address_id": owner[1], "type": "cold_water", "serial": "184521"},
                       values={"t1": "105"})
    assert res.meter_id == mid and res.status == "accepted"
    assert len(await repo.address_meters(owner[1])) == 1


async def test_recognized_serial_saved_for_meter_without_one(repo, owner):
    mid = await _meter(repo, owner)
    await submit(repo, owner[0], meter_id=mid, values={"t1": "1"}, recognized={"serial": "AB-77"})
    assert (await repo.get_meter(mid))["serial"] == "AB-77"


async def test_rollback_when_reading_insert_fails(repo, owner, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(repo, "add_reading", boom)
    with pytest.raises(RuntimeError):
        await submit(repo, owner[0], draft={"address_id": owner[1], "type": "gas"}, values={"t1": "1"})
    assert await repo.address_meters(owner[1]) == []  # счётчик из черновика откатился


def test_units_and_format():
    assert to_units(123_456) == 123.456 and to_units(None) is None
    assert values_to_units({"t1": 1_500, "t2": None}) == {"t1": 1.5, "t2": None}
    assert format_values({"t1": 123_456}, "cold_water") == "123,456 м³"
    assert format_values({"t1": 12_345_670, "t2": 500_000}, "electricity", 2) == "12345,67 / 500,00 кВт·ч"


# --- Домен S2 ---

def _m(mid, mtype="cold_water", aid=1, label="Арбат 47к1, кв 32", serial=None):
    return {"id": mid, "type": mtype, "address_id": aid, "address_label": label, "serial": serial}


def test_meter_labels_distinguish_same_type():
    assert M.meter_labels([_m(1), _m(2, "hot_water")]) == ["Хол. вода · Арбат 47к1, кв 32",
                                                           "Гор. вода · Арбат 47к1, кв 32"]
    assert M.meter_labels([_m(1, serial="18-12 4521"), _m(2, serial="99-0007")]) == [
        "Хол. вода · Арбат 47к1, кв 32 …4521", "Хол. вода · Арбат 47к1, кв 32 …0007"]
    assert M.meter_labels([_m(2, serial="1"), _m(1)]) == ["Хол. вода · Арбат 47к1, кв 32 №2",
                                                          "Хол. вода · Арбат 47к1, кв 32 №1"]
    long = "Мск, Краснопресненская н"  # 24 символа — хвост серийника не влезает в 40
    labels = M.meter_labels([_m(1, label=long, serial="1111"), _m(2, label=long, serial="2222")])
    assert labels[1].endswith("№2") and all(len(x) <= 40 for x in labels)
    assert M.meter_label(_m(3, aid=2), [_m(1), _m(2)]) == "Хол. вода · Арбат 47к1, кв 32"


def test_growth_delta_and_value_like():
    assert M.growth_limit("cold_water", 3) == 90_000 and M.growth_limit("heat", 0) == 5_000
    assert M.value_delta({"t1": 5, "t2": None}, {"t1": 2, "t2": 1}) == {"t1": 3}
    assert M.total_delta("electricity", {"t1": 1, "t2": 2}) == 3 and M.total_delta("gas", {"t1": 4}) == 4
    assert M.looks_like_value(" 123,45 ") and M.looks_like_value("-5") and not M.looks_like_value("кв 5")
    assert M.tariffs_of("gas", 3) == 1 and M.tariffs_of("electricity", 3) == 3


def test_parse_due_date():
    assert M.parse_due_date("15.03.2030", TODAY) == date(2030, 3, 15)
    assert M.parse_due_date("1/2/2027", TODAY) == date(2027, 2, 1)
    for text, code in [("31.02.2030", "no_such_date"), ("01.01.2020", "past"), ("март", "format"),
                       ("15.03.2090", "too_far")]:
        with pytest.raises(M.DateParseError) as e:
            M.parse_due_date(text, TODAY)
        assert e.value.code == code


async def test_created_at_follows_app_clock(repo):
    """created_at пишется по app.clock (не datetime('now') SQLite); в дашборде и API — дата по Москве."""
    late = datetime(2026, 10, 20, 1, 30, tzinfo=clock.TZ)  # по UTC ещё 19.10 22:30
    clock.set_now(late)
    uid, aid = await _user(repo, 7)
    mid = await _meter(repo, (uid, aid))
    res = await submit(repo, uid, meter_id=mid, values={"t1": "123,456"}, today=late.date())
    assert res.ok
    rows = [await repo.get_reading(res.reading_id), await repo.get_meter(mid), await repo.get_address(aid),
            await repo.get_user_by_id(uid)]
    linked = (await repo.user_addresses(uid))[0]["linked_at"]
    assert {r["created_at"] for r in rows} | {linked} == {"2026-10-19 22:30:00"}
    assert iso(rows[0]["created_at"]) == "2026-10-20T01:30:00+03:00"
    dash = await load_dashboard(repo, uid, late.date())
    assert "Хол. вода · Арбат 47к1, кв 32 — подано 20.10" in dash.lines
