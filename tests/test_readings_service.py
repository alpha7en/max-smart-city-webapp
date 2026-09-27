"""Сервис подачи app/readings.py: принято, неверный формат, меньше прошлого, большой рост, замена, откат."""
from __future__ import annotations


import pytest

from app.readings import SubmitResult, submit_reading
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


@pytest.mark.parametrize("value,needle", [("12,34,5", "123,456"), ("123,4567", "123,456"), ("-5", "123,456"),
                                          ("abc", "123,456"), ("", "123,456")])
async def test_bad_format(repo, owner, value, needle):
    mid = await _meter(repo, owner)
    res = await submit(repo, owner[0], meter_id=mid, values={"t1": value})
    assert res.status == "bad_format" and needle in res.message
    assert await repo.history(mid) == []


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


async def test_rollback_when_reading_insert_fails(repo, owner, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(repo, "add_reading", boom)
    with pytest.raises(RuntimeError):
        await submit(repo, owner[0], draft={"address_id": owner[1], "type": "gas"}, values={"t1": "1"})
    assert await repo.address_meters(owner[1]) == []  # счётчик из черновика откатился
