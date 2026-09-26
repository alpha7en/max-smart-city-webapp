"""ТОЛЬКО ДЛЯ ХАКАТОНА: тестовый профиль /demo_profile (app/bot/flows/hackathon_demo.py)."""
from __future__ import annotations

import random
from dataclasses import replace

import pytest

from app.bot.flows import hackathon_demo as D
from app.bot.router import Router
from app.bot.states import S
from app.bot.texts import common as C
from app.bot.texts import hackathon_demo as HT
from app.bot.texts import menu as MT
from app.domain.meters import current_period, shift_period
from app.main import COMMANDS, bot_commands
from tests.conftest import NOW, Chat
from tests.test_registration import UID, UID2, labels, last_kb, register, session


@pytest.fixture
def demo_router(deps) -> Router:
    return Router(replace(deps, settings=replace(deps.settings, hackathon_demo_profile=True)))


@pytest.fixture
def demo_chat(demo_router, api) -> Chat:
    return Chat(demo_router, api, UID)


async def assert_demo_profile(repo, uid: int = UID) -> dict:
    user = await repo.get_user(uid)
    assert user["registered_at"] and len(user["full_name"].split()) == 3
    assert user["phone"].startswith("+79") and len(user["phone"]) == 12 and not user["phone_verified"]
    addrs = await repo.user_addresses(user["id"])
    assert len(addrs) == 2 and {(a["role"], a["access"]) for a in addrs} == {("owner", "granted")}
    meters = await repo.user_meters(user["id"])
    per_addr = [sum(m["address_id"] == a["id"] for m in meters) for a in addrs]
    assert all(2 <= n <= 3 for n in per_addr)
    assert len(await repo.unpaid_bills(user["id"])) == 2
    period = current_period(NOW.date())
    for m in meters:
        hist = await repo.history(m["id"])
        past = [r for r in hist if r["period"] != period]
        assert [r["period"] for r in past] == [shift_period(period, -i) for i in range(1, 7)]
        assert all(a["t1"] > b["t1"] for a, b in zip(hist, hist[1:]))  # показания растут
        assert past[0]["created_at"].startswith(shift_period(period, -1))  # дата подачи — в своём месяце
    return user


async def test_new_user_gets_random_profile(demo_chat, api, repo):
    await demo_chat.text("/demo_profile")
    user = await assert_demo_profile(repo)
    text = api.last_text()
    assert text.startswith(f"Создали тестовый профиль **{user['full_name']}**: 2 адреса, ")
    assert "Данные вымышленные, только для проверки на хакатоне." in text
    kb = labels(last_kb(api))
    assert kb[0].startswith("Оплатить счета · ")                           # два адреса — два демо-счёта
    assert any(t.startswith(MT.URGENT_VERIFICATION.split("{")[0]) for t in kb)  # скорая поверка видна сразу
    assert (await session(repo)).state == S.IDLE


async def test_command_in_the_middle_of_registration(demo_chat, api, repo):
    await demo_chat.text("/start")
    assert (await session(repo)).state == S.REG_NAME
    await demo_chat.text("/demo_profile")
    await assert_demo_profile(repo)
    assert (await session(repo)).state == S.IDLE


async def test_registered_user_must_delete_profile_first(demo_chat, api, repo):
    await register(demo_chat)
    user = await repo.get_user(UID)
    api.clear()
    await demo_chat.text("/demo_profile")
    assert api.last_text() == HT.HAS_PROFILE and "хакатон" in HT.HAS_PROFILE
    assert labels(last_kb(api)) == [MT.BTN_PROFILE, C.BTN_MENU]
    assert len(await repo.user_addresses(user["id"])) == 1  # ничего не добавили


async def test_after_deleting_profile(demo_chat, api, repo):
    await register(demo_chat)
    await repo.delete_user_data((await repo.get_user(UID))["id"])
    await demo_chat.text("/demo_profile")
    await assert_demo_profile(repo)


async def test_two_reviewers_both_own_their_addresses(demo_router, api, repo):
    for uid in (UID, UID2):
        await Chat(demo_router, api, uid).text("/demo_profile")
        await assert_demo_profile(repo, uid)


async def test_random_profiles_are_valid():
    for seed in range(200):
        p = D.random_profile(random.Random(seed))
        texts = [t for t, _ in p.addresses]
        assert len(set(texts)) == 2
        for t in texts:
            (c,) = D._ADDRESSES.parse_local(t)  # адрес разбирается однозначно, с домом и квартирой
            assert c.house and c.flat
        surname, first, father = p.full_name.split()
        female = first in D.FEMALE
        assert surname.endswith("а") == female and father.endswith("на" if female else "ич")
        assert D.URGENT_DAYS[0] <= p.addresses[0][1][0].verification_days <= D.URGENT_DAYS[1]
        assert 4 <= p.meters <= 6


async def test_disabled_by_default(chat, api, repo):
    """Settings() по умолчанию выключено: без профиля — обычная регистрация, с профилем — меню."""
    await chat.text("/demo_profile")
    assert (await session(repo)).state == S.REG_NAME
    assert not (await repo.get_user(UID))["registered_at"]
    await register(chat)
    await chat.text("/demo_profile")
    assert MT.BTN_SUBMIT in labels(last_kb(api))


def test_command_listed_in_max_only_when_enabled(deps):
    on = replace(deps.settings, hackathon_demo_profile=True)
    assert bot_commands(on) == COMMANDS + [("demo_profile", HT.COMMAND_DESCRIPTION)]
    assert bot_commands(deps.settings) == COMMANDS
