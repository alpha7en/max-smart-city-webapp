"""ФГИС «Аршин» на реальных ответах из tests/fixtures/arshin/: поиск, выбор записи, сценарий бота; сеть не трогаем."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.bot.router import Router
from app.bot.texts import common as C
from app.bot.texts import submission as T
from app.bot.texts import verification as TA
from app.domain.verification import card_url, choose
from app.integrations.arshin import ArshinClient
from tests.conftest import NOW, Chat
from tests.test_submission import buttons, give_serial, register

TODAY = NOW.date()
WATER = {"vri_id": "1-331", "org_title": "ООО «Поверка»", "mit_number": "74189-19",
         "mit_title": "Счетчики холодной и горячей воды", "mit_notation": "СГВ", "mi_modification": "СГВ-15",
         "mi_number": "18-765432", "verification_date": "2023-10-19T12:00:00Z", "valid_date": "2029-10-18T12:00:00Z",
         "applicability": True}
OTHER = {**WATER, "vri_id": "1-332", "mit_number": "55555-13", "mit_title": "Счетчики воды крыльчатые",
         "mit_notation": "ВСКМ", "mi_modification": "", "verification_date": "10.03.2022", "valid_date": "09.03.2028"}


def listing(*items) -> dict:
    return {"result": {"count": len(items), "start": 0, "rows": 100, "items": list(items)}}


class Clock:
    """Монотонные часы, которые двигает подменённый sleep."""

    def __init__(self):
        self.t, self.sleeps = 1000.0, []

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.sleeps.append(round(s, 3))
        self.t += s


def make(handler, repo=None, mode="live") -> tuple[ArshinClient, list[httpx.Request], Clock]:
    seen: list[httpx.Request] = []

    async def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        res = handler(request)
        return await res if asyncio.iscoroutine(res) else res

    clock = Clock()
    client = ArshinClient(mode, "https://fgis.test/eapi", repo, transport=httpx.MockTransport(wrapped),
                          sleep=clock.sleep, monotonic=clock)
    return client, seen, clock


def ok(*items) -> httpx.Response:
    return httpx.Response(200, json=listing(*items))


async def test_off_and_fixtures_modes_do_not_touch_network():
    off, seen, _ = make(lambda r: ok(WATER), mode="off")
    assert (await off.lookup("12345678", "gas", TODAY)).status == "off"
    demo, seen2, _ = make(lambda r: ok(WATER), mode="fixtures")
    res = await demo.lookup("12345678", "gas", TODAY)
    assert res.status == "found" and res.demo and res.records[0].vri_id.startswith("demo-")
    assert res.records[0].mi_number == "12345678" and res.records[0].valid_date > TODAY
    assert (await demo.lookup("12345670", "gas", TODAY)).status == "none"
    assert len((await demo.lookup("12345679", "cold_water", TODAY)).records) == 2  # коллизия номеров
    assert seen == seen2 == []


@pytest.fixture
async def user(repo):
    return await register(repo)


def arshin_chat(deps, api, handler, recognizer=None, mode="live") -> tuple[Chat, list]:
    client, seen, _ = make(handler, deps.repo, mode)
    d = replace(deps, arshin=client, recognizer=recognizer or deps.recognizer)
    return Chat(Router(d), api), seen


async def new_water_meter(chat: Chat, serial: str = "18-765432") -> None:
    await chat.photo()
    await chat.press("Хол. вода")
    await chat.press("Арбат 47к1, кв 32")
    await give_serial(chat, serial)
    await chat.press(T.BTN_SEND)


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "arshin"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text("utf-8"))


async def test_real_water_fixture_lookup():
    water_data = _fixture("search_water_18452178.json")
    client, _, _ = make(lambda r: httpx.Response(200, json=water_data))
    res = await client.lookup("18-452178", "cold_water", TODAY)
    assert res.status == "found"
    assert len(res.records) == 1
    r = res.records[0]
    assert r.vri_id == "1-333392815"
    assert r.verification_date == date(2023, 11, 14)
    assert r.valid_date == date(2029, 11, 13)
    assert r.applicable is True
    assert "СГВ-15" in r.mi_modification
    match = choose(res.records, "cold_water")
    assert match.level == "high" and match.best.vri_id == "1-333392815"
    assert card_url(match.best.vri_id) == "https://fgis.gost.ru/fundmetrology/cm/results/1-333392815"


async def test_real_collision_and_inapplicable_fixture():
    collision_data = _fixture("search_collision_0112456.json")
    inapplicable_card = _fixture("card_inapplicable_340080782.json")

    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/1-340080782"):
            return httpx.Response(200, json=inapplicable_card)
        return httpx.Response(200, json=collision_data)

    client, seen, _ = make(handler)
    res = await client.lookup("0112456", "cold_water", TODAY)
    assert res.status == "found"
    vri_ids = [r.vri_id for r in res.records]
    assert "2-120021534" in vri_ids
    assert "1-340080782" in vri_ids
    assert "1-368481110" in vri_ids
    assert card_url("2-120021534") == "https://fgis.gost.ru/fundmetrology/cm/results/2-120021534"
    unfit_rec = next(r for r in res.records if r.vri_id == "1-340080782")
    assert unfit_rec.unfit is True and unfit_rec.valid_date is None
    # Запросы деталей для термометра 1-390257330 не должны отправляться
    assert not any("1-390257330" in r.url.path for r in seen)
    gas_match = choose(res.records, "gas")
    assert gas_match.level == "high" and gas_match.best.vri_id == "1-368481110"
    assert gas_match.best.valid_date == date(2032, 9, 5)


async def test_real_water_fixture_bot_flow(deps, api, repo, user):
    water_data = _fixture("search_water_18452178.json")
    chat, _ = arshin_chat(deps, api, lambda r: httpx.Response(200, json=water_data))
    await new_water_meter(chat, "18-452178")
    text = api.last_text()
    assert TA.FOUND.format(date="13.11.2029") in text
    assert buttons(api) == [C.BTN_MENU, T.BTN_MORE, TA.BTN_CARD]
    link = api.outgoing()[-1][1]["payload"]["buttons"][1][0]
    assert link["url"] == "https://fgis.gost.ru/fundmetrology/cm/results/1-333392815"
    (m,) = await repo.address_meters(user[1])
    assert m["verification_due"] == "2029-11-13" and m["verification_source"] == "arshin"
    assert m["arshin_vri_id"] == "1-333392815"
