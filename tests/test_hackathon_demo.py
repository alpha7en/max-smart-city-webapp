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
from app.bot.texts import invite as IT
from app.bot.texts import profile as PT
from app.bot.texts import menu as MT
from app.domain.meters import current_period, shift_period
from app.main import COMMANDS, bot_commands
from app.repo import is_demo_person
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
    own = [a for a in addrs if a["role"] == "owner"]
    (shared,) = [a for a in addrs if a["role"] != "owner"]  # чужой адрес демо-собственника
    assert len(own) == 2 and {a["access"] for a in addrs} == {"granted"}
    owner = await repo.get_user_by_id(shared["granted_by"])
    assert is_demo_person(owner) and shared["owner_id"] == owner["id"] and not owner["registered_at"]
    meters = await repo.user_meters(user["id"])
    per_addr = [sum(m["address_id"] == a["id"] for m in meters) for a in addrs]
    assert all(2 <= n <= 3 for n in per_addr[:2]) and per_addr[2] == D.SHARED_METERS
    assert len(await repo.unpaid_bills(user["id"])) == 3
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
    assert kb[0].startswith("Оплатить счета · ")                           # три адреса — три демо-счёта
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


async def test_new_profile_after_deleting_demo_does_not_reuse_old_addresses(demo_chat, api, repo):
    """SQLite отдаёт удалённому последнему пользователю тот же id, а адреса и счётчики после удаления
    остаются за адресом: ключ демо-адреса не должен зависеть от id, иначе вернутся старые адреса."""
    await demo_chat.text("/demo_profile")
    old = await repo.get_user(UID)
    old_addrs = {a["id"] for a in await repo.user_addresses(old["id"])}
    old_meters = {m["id"] for m in await repo.user_meters(old["id"])}
    await repo.delete_user_data(old["id"])

    await demo_chat.text("/demo_profile")
    user = await assert_demo_profile(repo)
    assert user["id"] == old["id"]  # тот же id — из-за этого и был баг
    assert not {a["id"] for a in await repo.user_addresses(user["id"])} & old_addrs
    meters = await repo.user_meters(user["id"])
    assert not {m["id"] for m in meters} & old_meters and 6 <= len(meters) <= 8  # старые не вернулись


async def test_two_reviewers_both_own_their_addresses(demo_router, api, repo):
    for uid in (UID, UID2):
        await Chat(demo_router, api, uid).text("/demo_profile")
        await assert_demo_profile(repo, uid)


async def test_random_profiles_are_valid():
    for seed in range(200):
        p = D.random_profile(random.Random(seed))
        cities = [t.split(", ")[0] for t, _ in p.addresses]
        assert len(set(cities)) == 2  # два разных города
        surname, first, father = p.full_name.split()
        female = first in D.FEMALE
        assert surname.endswith("а") == female and father.endswith("на" if female else "ич")
        assert D.URGENT_DAYS[0] <= p.addresses[0][1][0].verification_days <= D.URGENT_DAYS[1]
        assert 4 <= p.meters <= 6


def test_every_real_house_parses_with_flat():
    for city, houses in D.HOUSES.items():
        for house in houses:
            (c,) = D._ADDRESSES.parse_local(f"{city}, {house}, кв. 17")  # однозначно, с домом и квартирой
            assert c.house == house.rsplit("д. ", 1)[1] and c.flat == "17", (city, house, c)


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


# --- Общий доступ в демо-профиле ---

async def demo_people(repo) -> list[dict]:
    return await repo._all("SELECT * FROM users WHERE max_user_id<0 ORDER BY id")


def sent_ids(api) -> set[int]:
    return {kw["user_id"] for kw in api.named("send")} | {kw["chat_id"] for kw in api.named("send") if kw["chat_id"]}


async def test_demo_profile_has_shared_access(demo_chat, api, repo):
    await demo_chat.text("/demo_profile")
    user = await assert_demo_profile(repo)
    people = await demo_people(repo)
    assert 2 <= len(people) <= 3 and not any(p["registered_at"] for p in people)
    registered = {u["id"] for u in await repo.registered_users()}
    assert registered == {user["id"]}  # планировщик демо-людей не видит
    own0 = (await repo.user_addresses(user["id"]))[0]
    members = await repo.address_members(own0["id"])
    assert 1 <= len(members) <= 2 and {m["access"] for m in members} == {"granted"}
    for m in members:
        assert (await repo.user_address(m["user_id"], own0["id"]))["granted_by"] == user["id"]
    assert members[0]["last_period"] == current_period(NOW.date())  # первый жилец подал в этом месяце
    if len(members) == 2:
        assert members[1]["last_period"] == shift_period(current_period(NOW.date()), -1)
    (inv,) = await repo.owner_invites(user["id"], NOW)  # одна действующая ссылка
    await demo_chat.payload("g|sh_list|")
    text = api.last_text()
    for part in (IT.SHARED_OWNED, IT.SHARED_INVITES, IT.SHARED_RECEIVED, "доступ от", "> " + HT.DEMO_PEOPLE):
        assert part in text
    assert "показания " in (await _card(demo_chat, api, own0["label"]))
    await demo_chat.payload("g|profile|")
    assert "доступ от" in api.last_text() and "Доступ: " in api.last_text()
    await demo_chat.payload("g|meters|")
    assert "— доступ от" in api.last_text() and ", от " in api.last_text()
    assert all(uid > 0 for uid in sent_ids(api))  # демо-людям ничего не отправляли


async def _card(chat, api, label: str) -> str:
    await chat.press(label)
    return api.last_text()


async def test_demo_close_access_remove_and_delete_send_nothing_to_demo_people(demo_router, api, repo):
    other = Chat(demo_router, api, UID2)  # второй проверяющий — его демо-данные не должны пострадать
    await other.text("/demo_profile")
    others = {p["id"] for p in await demo_people(repo)}
    chat = Chat(demo_router, api, UID)
    await chat.text("/demo_profile")
    user = await repo.get_user(UID)
    own0, _, shared = await repo.user_addresses(user["id"])
    first = (await repo.address_members(own0["id"]))[0]
    # закрыть доступ демо-жильцу
    await chat.payload(f"g|acc_list|{own0['id']}")
    await chat.press(next(b for b in labels(last_kb(api)) if b.startswith("Закрыть")))
    await chat.press(IT.BTN_REVOKE_YES)
    assert (await repo.user_address(first["user_id"], own0["id"]))["access"] == "denied"
    # убрать у себя чужой демо-адрес: демо-собственник уходит вместе с ним, адрес и показания остаются
    owner_id = shared["owner_id"]
    await chat.payload(f"g|sh_rm_ok|{shared['id']}")
    assert api.last_text() == IT.REMOVED.format(label=shared["full_text"])
    assert await repo.get_user_by_id(owner_id) is None and await repo.get_address(shared["id"])
    # удалить свои данные: все его демо-люди удалены, чужие — на месте
    await chat.payload("g|prof_del|")
    await chat.press(PT.BTN_DELETE_YES)
    assert await repo.get_user(UID) is None
    assert {p["id"] for p in await demo_people(repo)} == others
    assert len(await repo.user_addresses((await repo.get_user(UID2))["id"])) == 3
    assert all(uid > 0 for uid in sent_ids(api))
    await chat.text("/demo_profile")  # можно создать заново
    await assert_demo_profile(repo)
