"""ТОЛЬКО ДЛЯ ХАКАТОНА: демо-профиль для проверяющих (app/bot/flows/hackathon_demo.py)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.bot.router import Router
from app.bot.states import S
from app.bot.texts import hackathon_demo as HT
from app.bot.texts import menu as MT
from app.bot.texts import registration as RT
from app.domain.meters import current_period, shift_period
from tests.conftest import NOW, Chat
from tests.test_registration import UID, UID2, labels, last_kb, register, session


@pytest.fixture
def demo_router(deps) -> Router:
    return Router(replace(deps, settings=replace(deps.settings, hackathon_demo_profile=True)))


@pytest.fixture
def demo_chat(demo_router, api) -> Chat:
    return Chat(demo_router, api, UID)


async def test_offer_after_registration(demo_chat, api, repo):
    await register(demo_chat)
    text = api.last_text()
    assert text.startswith(RT.DONE) and HT.OFFER in text
    assert labels(last_kb(api)) == [HT.BTN_YES, HT.BTN_NO]
    assert (await session(repo)).state == S.IDLE


async def test_yes_generates_profile(demo_chat, api, repo):
    await register(demo_chat)
    api.clear()
    await demo_chat.press(HT.BTN_YES)
    user = await repo.get_user(UID)
    addrs = await repo.user_addresses(user["id"])
    assert len(addrs) == 3 and {(a["role"], a["access"]) for a in addrs} == {("owner", "granted")}
    assert [a["label"] for a in addrs][1:] == ["Мск, Тверская 12, кв 45", "Истра, Садовая 3"]
    meters = await repo.user_meters(user["id"])
    assert sorted(m["type"] for m in meters) == ["cold_water", "electricity", "electricity", "gas", "hot_water"]
    assert len(await repo.unpaid_bills(user["id"])) == 3

    period = current_period(NOW.date())
    by_type = {(m["type"], m["address_label"]): m for m in meters}
    cold = by_type[("cold_water", "Мск, Тверская 12, кв 45")]
    hist = await repo.history(cold["id"])
    assert [r["period"] for r in hist] == [shift_period(period, -i) for i in range(1, 7)]
    assert all(a["t1"] > b["t1"] for a, b in zip(hist, hist[1:]))  # показания растут
    assert hist[0]["created_at"].startswith(shift_period(period, -1))  # дата подачи — в своём месяце
    gas = by_type[("gas", "Истра, Садовая 3")]
    assert gas["last_period"] == period  # за текущий месяц на даче уже подано
    two_tariff = by_type[("electricity", "Мск, Тверская 12, кв 45")]
    assert two_tariff["tariffs"] == 2 and two_tariff["last_t2"] is not None

    text = api.last_text()
    assert text.startswith("Добавили вымышленный демо-профиль: **2 адреса** и **5 счётчиков**.")
    assert any(t.startswith(MT.URGENT_VERIFICATION.split("{")[0]) for t in labels(last_kb(api)))


async def test_yes_twice_does_not_duplicate(demo_chat, api, repo):
    await register(demo_chat)
    await demo_chat.press(HT.BTN_YES)
    api.clear()
    await demo_chat.press(HT.BTN_YES)
    user = await repo.get_user(UID)
    assert len(await repo.user_addresses(user["id"])) == 3
    assert len(await repo.user_meters(user["id"])) == 5
    assert api.last_text().startswith(HT.ALREADY)


async def test_no_shows_menu_without_demo(demo_chat, api, repo):
    await register(demo_chat)
    await demo_chat.press(HT.BTN_NO)
    user = await repo.get_user(UID)
    assert len(await repo.user_addresses(user["id"])) == 1
    assert MT.BTN_SUBMIT in labels(last_kb(api))


async def test_two_reviewers_both_own_their_demo_addresses(demo_router, api, repo):
    for uid, name in ((UID, "Иванова Анна"), (UID2, "Петров Пётр")):
        chat = Chat(demo_router, api, uid)
        await register(chat, name=name, address=f"Москва, Арбат 47к1, кв. {uid % 100}")
        await chat.press(HT.BTN_YES)
        user = await repo.get_user(uid)
        assert {a["access"] for a in await repo.user_addresses(user["id"])} == {"granted"}
        assert len(await repo.user_meters(user["id"])) == 5


async def test_command_offers_for_registered_user(demo_chat, api, repo):
    await register(demo_chat)
    await demo_chat.press(HT.BTN_NO)
    await demo_chat.text("/demo_profile")
    assert api.last_text() == HT.OFFER


async def test_disabled_by_default(chat, api, repo):
    """Settings() по умолчанию выключено: регистрация ведёт в меню, команда и кнопка ничего не создают."""
    await register(chat)
    assert HT.OFFER not in api.last_text()
    await chat.text("/demo_profile")
    await chat.payload("g|hackathon_demo|")
    user = await repo.get_user(UID)
    assert len(await repo.user_addresses(user["id"])) == 1
