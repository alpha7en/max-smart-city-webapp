"""Приглашение жильца собственником, список доступа и отзыв (сценарии I1–I14)."""
from __future__ import annotations

import re
from datetime import timedelta

import aiosqlite
import pytest

from app import clock
from app.bot.router import start_prefix
from app.bot.states import S
from app.bot.texts import common as C
from app.bot.texts import invite as IT
from app.bot.texts import profile as PT
from app.bot.texts import registration as RT
from app.db import SCHEMA_FILE, SCHEMA_VERSION
from app.domain.people import short_name
from app.repo import Repo
from tests import fakes
from tests.conftest import NOW, Chat
from tests.test_registration import ADDRESS, UID, UID2, labels, last_kb, register, session

UID3 = 7000003
MARIA = "Смирнова Мария Павловна"


def sent_to(api, uid: int) -> list[str]:
    return [kw["text"] for kw in api.named("send") if kw["user_id"] == uid]


async def owner_ids(repo) -> tuple[int, int]:
    u = await repo.get_user(UID)
    (a,) = await repo.user_addresses(u["id"])
    return u["id"], a["id"]


async def make_invite(chat: Chat, api) -> str:
    """Собственник жмёт «Пригласить жильца» → токен из ссылки."""
    await chat.payload("g|profile|")
    await chat.press(IT.BTN_INVITE)
    link = next(kw for kw in reversed(api.named("send")) if "start=inv_" in kw["text"])
    return re.search(r"start=inv_([a-z0-9]+)", link["text"]).group(1)


async def start(chat: Chat, payload: str) -> None:
    await chat.feed(fakes.bot_started(chat.uid, payload))


@pytest.fixture
async def owner(router, api) -> Chat:
    chat = Chat(router, api, UID)
    await register(chat)
    return chat


# --- I1: создание приглашения ---

async def test_i1_create_invite(owner, api, repo):
    api.clear()
    await owner.payload("g|profile|")
    assert "Доступ: только вы" in api.last_text()
    assert IT.BTN_INVITE in labels(last_kb(api)) and IT.BTN_MANAGE not in labels(last_kb(api))
    await owner.press(IT.BTN_INVITE)
    link, created = api.named("send")[-2:]
    assert link["keyboard"] is None and link["user_id"] == UID
    assert re.search(r"https://max\.ru/test_bot\?start=inv_[a-z0-9]{20}\b", link["text"])
    assert "до 26 октября" in link["text"] and "Арбат" in link["text"]
    assert created["text"] == IT.INVITE_CREATED.format(label="Арбат 47к1, кв 32")
    assert labels(created["keyboard"]) == [IT.BTN_MANAGE, C.BTN_MENU]
    ((inv),) = [dict(r) for r in await repo._all("SELECT * FROM invites")]
    uid, aid = await owner_ids(repo)
    assert (inv["address_id"], inv["owner_user_id"], inv["used_at"]) == (aid, uid, None)
    assert inv["expires_at"] == "2026-10-26 09:00:00"  # +7 дней, UTC
    payload = "inv_" + inv["token"]
    assert len(payload) <= 128 and re.fullmatch(r"[A-Za-z0-9_-]+", payload)


async def test_i2_limit_three_active(owner, api, repo):
    for _ in range(3):
        await make_invite(owner, api)
    await owner.payload("g|inv_new|")
    assert api.last_text() == IT.INVITE_LIMIT.format(label="Арбат 47к1, кв 32", count=3, until="26 октября")
    assert len(await repo._all("SELECT * FROM invites")) == 3
    clock.set_now(NOW + timedelta(days=8))  # старые истекли — можно снова
    await owner.payload("g|inv_new|")
    assert api.last_text().startswith("Перешлите сообщение выше")


# --- I3–I5: незарегистрированный по ссылке ---

async def test_i3_unregistered_registers_with_invite_address(owner, router, api, repo):
    token = await make_invite(owner, api)
    maria = Chat(router, api, UID3)
    api.clear()
    await start(maria, "inv_" + token)
    first = api.texts()[0]
    assert first.startswith("Вас пригласили передавать показания по адресу") and C.WELCOME in first
    assert (await session(repo, UID3)).data["invite"] == token
    await maria.text(MARIA)
    await maria.text("+7 900 555-11-22")
    assert api.last_text().startswith("Адрес из приглашения:")
    assert labels(last_kb(api)) == [IT.BTN_THIS_ADDRESS, IT.BTN_OTHER_ADDRESS, C.BTN_BACK]
    await maria.press(IT.BTN_THIS_ADDRESS)
    assert (await session(repo, UID3)).state == S.REG_CONFIRM and "Арбат" in api.last_text()
    api.clear()
    await maria.press(RT.BTN_ALL_OK)
    u = await repo.get_user(UID3)
    (a,) = await repo.user_addresses(u["id"])
    _, aid = await owner_ids(repo)
    assert (a["id"], a["role"], a["access"]) == (aid, "tenant", "granted")
    mine = sent_to(api, UID3)
    assert any(IT.REG_ACCEPTED.format(label=a["label"]) in t for t in mine)
    assert not any("уже зарегистрирован собственник" in t for t in mine)  # без «нет прав»
    assert sent_to(api, UID) == [IT.OWNER_ACCEPTED.format(name="Мария С.", label="Арбат 47к1, кв 32")]
    inv = await repo.get_invite(token)
    assert inv["used_by"] == u["id"] and inv["used_at"]
    # повторное открытие ссылки — уже использована
    await start(maria, "inv_" + token)
    assert api.last_text() == IT.INVALID["used"]


async def test_i4_other_address_then_same_typed(owner, router, api, repo):
    token = await make_invite(owner, api)
    maria = Chat(router, api, UID3)
    await start(maria, "inv_" + token)
    await maria.text(MARIA)
    await maria.text("+7 900 555-11-22")
    await maria.press(IT.BTN_OTHER_ADDRESS)
    assert api.last_text() == RT.ASK_ADDRESS
    await maria.text(ADDRESS)  # тот же адрес вручную — приглашение всё равно подходит
    await maria.press(RT.BTN_YES)
    await maria.press(RT.BTN_ALL_OK)
    u = await repo.get_user(UID3)
    assert [(a["role"], a["access"]) for a in await repo.user_addresses(u["id"])] == [("tenant", "granted")]


async def test_i5_restart_and_repeated_link_keep_invite(owner, router, api, repo):
    token = await make_invite(owner, api)
    maria = Chat(router, api, UID3)
    await maria.text("привет")  # начал регистрацию без ссылки
    await maria.text(MARIA)
    await start(maria, "inv_" + token)  # открыл ссылку посреди регистрации
    assert api.texts()[-2].startswith(IT.REG_KEPT) and C.CONTINUE_REG in api.texts()[-2]
    await maria.press(C.BTN_RESTART)
    assert (await session(repo, UID3)).data["invite"] == token
    await maria.text(MARIA)
    await maria.text("+7 900 555-11-22")
    assert api.last_text().startswith("Адрес из приглашения:")
    await maria.text("да")
    await maria.press(RT.BTN_ALL_OK)
    u = await repo.get_user(UID3)
    assert (await repo.user_addresses(u["id"]))[0]["access"] == "granted"


async def test_i6_invite_expired_during_registration(owner, router, api, repo):
    token = await make_invite(owner, api)
    maria = Chat(router, api, UID3)
    await start(maria, "inv_" + token)
    await maria.text(MARIA)
    await maria.text("+7 900 555-11-22")
    await maria.press(IT.BTN_THIS_ADDRESS)
    clock.set_now(NOW + timedelta(days=8))
    api.clear()
    await maria.press(RT.BTN_ALL_OK)
    u = await repo.get_user(UID3)
    assert (await repo.user_addresses(u["id"]))[0]["access"] == "pending"
    assert any(IT.REG_NOT_APPLIED in t for t in api.texts())
    assert any("уже зарегистрирован собственник" in t for t in api.texts())


# --- I7–I9: зарегистрированный ---

async def test_i7_registered_pending_accepts(owner, router, api, repo):
    petr = Chat(router, api, UID2)
    await register(petr, name="Петров Пётр", phone="+7 900 111-22-33")  # tenant/pending
    token = await make_invite(owner, api)
    api.clear()
    await start(petr, "inv_" + token)
    assert api.last_text() == IT.OFFER.format(owner="Анна И.", address=(await repo.get_address(
        (await owner_ids(repo))[1]))["full_text"])
    assert labels(last_kb(api)) == [IT.BTN_ACCEPT, IT.BTN_DECLINE]
    await petr.press(IT.BTN_ACCEPT)
    assert api.last_text() == IT.ACCEPTED.format(label="Арбат 47к1, кв 32")
    assert labels(last_kb(api)) == [PT.BTN_SUBMIT, C.BTN_MENU]
    tid = (await repo.get_user(UID2))["id"]
    assert (await repo.user_address(tid, (await owner_ids(repo))[1]))["access"] == "granted"
    assert IT.OWNER_ACCEPTED.format(name="Пётр П.", label="Арбат 47к1, кв 32") in sent_to(api, UID)
    # «Разрешить» на старый запрос — уже решено, второго сообщения жильцу нет
    await petr.press(IT.BTN_ACCEPT)
    assert api.last_text() == IT.INVALID["used"]


async def test_i8_registered_other_address_declines_then_accepts(owner, router, api, repo):
    petr = Chat(router, api, UID2)
    await register(petr, name="Петров Пётр", phone="+7 900 111-22-33", address="Москва, Тверская 1, кв 5")
    token = await make_invite(owner, api)
    await start(petr, "inv_" + token)
    await petr.press(IT.BTN_DECLINE)
    assert api.last_text() == IT.DECLINED
    assert (await repo.get_invite(token))["used_at"] is None
    await start(petr, "inv_" + token)
    await petr.press(IT.BTN_ACCEPT)
    tid = (await repo.get_user(UID2))["id"]
    addrs = await repo.user_addresses(tid)
    assert sorted((a["role"], a["access"]) for a in addrs) == [("owner", "granted"), ("tenant", "granted")]
    assert all(a["label"] for a in addrs)


async def test_i9_self_invite(owner, api, repo):
    token = await make_invite(owner, api)
    await start(owner, "inv_" + token)
    assert api.last_text() == IT.SELF.format(label="Арбат 47к1, кв 32")
    assert labels(last_kb(api)) == [IT.BTN_MANAGE, C.BTN_MENU]
    await owner.payload(f"g|inv_ok|{token}")
    assert api.last_text() == IT.SELF.format(label="Арбат 47к1, кв 32")
    assert (await repo.get_invite(token))["used_at"] is None


@pytest.mark.parametrize("case", ["unknown", "expired", "used"])
async def test_i10_bad_tokens(owner, router, api, repo, case):
    token = await make_invite(owner, api)
    if case == "unknown":
        token = "zzz" + token[3:]
    elif case == "expired":
        clock.set_now(NOW + timedelta(days=7, minutes=1))
    else:
        other = await repo.ensure_user(7000009)
        assert await repo.use_invite(token, other["id"], clock.now())
    petr = Chat(router, api, UID2)
    await register(petr, name="Петров Пётр", phone="+7 900 111-22-33", address="Москва, Тверская 1, кв 5")
    await start(petr, "inv_" + token)
    assert api.last_text() == IT.INVALID[case] and labels(last_kb(api)) == [C.BTN_MENU]
    await petr.payload(f"g|inv_ok|{token}")
    assert api.last_text() == IT.INVALID[case]
    # незарегистрированный: понятный текст и обычная регистрация
    maria = Chat(router, api, UID3)
    api.clear()
    await start(maria, "inv_" + token)
    assert api.texts()[0].startswith(IT.INVALID[case]) and api.last_text() == RT.ASK_NAME
    assert "invite" not in (await session(repo, UID3)).data


# --- I11–I13: список доступа и отзыв ---

async def test_i11_members_and_revoke_with_confirmation(owner, router, api, repo):
    petr = Chat(router, api, UID2)
    await register(petr, name="Петров Пётр", phone="+7 900 111-22-33")
    await petr.payload("g|acc_demo|" + str((await owner_ids(repo))[1]))  # демо-кнопка работает как раньше
    uid, aid = await owner_ids(repo)
    tid = (await repo.get_user(UID2))["id"]
    meter = await repo.create_meter(aid, "cold_water", 1, None)
    await repo.add_reading(meter, tid, "2026-10", {"t1": 1000, "t2": None, "t3": None}, "manual")
    await owner.payload("g|profile|")
    assert "Доступ: Пётр П." in api.last_text()
    assert labels(last_kb(api))[2:4] == [IT.BTN_INVITE, IT.BTN_MANAGE]
    await owner.press(IT.BTN_MANAGE)
    assert "1. Пётр П. — есть доступ" in api.last_text()
    assert labels(last_kb(api)) == ["Отозвать: Пётр П.", IT.BTN_INVITE, C.BTN_MENU]
    await owner.press("Отозвать: Пётр П.")
    assert api.last_text() == IT.REVOKE_ASK.format(name="Пётр П.", label="Арбат 47к1, кв 32")
    assert (await repo.user_address(tid, aid))["access"] == "granted"  # пока не подтвердили
    api.clear()
    await owner.press(IT.BTN_REVOKE_YES)
    assert (await repo.user_address(tid, aid))["access"] == "denied"
    assert sent_to(api, UID2) == [IT.TENANT_REVOKED.format(label="Арбат 47к1, кв 32")]
    assert api.last_text() == IT.REVOKED.format(name="Пётр П.", label="Арбат 47к1, кв 32")
    assert len(await repo.history(meter)) == 1  # поданное остаётся
    await owner.press(IT.BTN_REVOKE_YES)  # повторно — уже закрыт
    assert api.last_text() == IT.REVOKE_ALREADY.format(name="Пётр П.")
    # отозванный больше не подаёт: «нет прав» с отказом
    await petr.payload(f"g|acc_req|{aid}")
    assert api.last_text().startswith("Собственник не открыл вам доступ")
    # новое приглашение возвращает доступ
    token = await make_invite(owner, api)
    await start(petr, "inv_" + token)
    await petr.press(IT.BTN_ACCEPT)
    assert (await repo.user_address(tid, aid))["access"] == "granted"


async def test_i12_owner_cannot_revoke_self_and_pending_allow(owner, router, api, repo):
    uid, aid = await owner_ids(repo)
    await owner.payload(f"g|acc_rev|{uid}.{aid}")
    assert api.last_text() == IT.REVOKE_SELF
    await owner.payload(f"g|acc_rev_ok|{uid}.{aid}")
    assert api.last_text() == IT.REVOKE_SELF
    assert (await repo.user_address(uid, aid))["access"] == "granted"
    petr = Chat(router, api, UID2)
    await register(petr, name="Петров Пётр", phone="+7 900 111-22-33")
    await owner.payload(f"g|acc_list|{aid}")
    assert "1. Пётр П. — ждёт одобрения" in api.last_text()
    await owner.press("Разрешить: Пётр П.")
    tid = (await repo.get_user(UID2))["id"]
    assert (await repo.user_address(tid, aid))["access"] == "granted"


async def test_i13_non_owner_has_no_management(owner, router, api, repo):
    petr = Chat(router, api, UID2)
    await register(petr, name="Петров Пётр", phone="+7 900 111-22-33")
    _, aid = await owner_ids(repo)
    await petr.payload("g|profile|")
    assert IT.BTN_INVITE not in labels(last_kb(api)) and "Доступ:" not in api.last_text()
    for payload in (f"g|inv_new|{aid}", f"g|acc_list|{aid}", "g|inv_new|", f"g|acc_rev|1.{aid}"):
        await petr.payload(payload)
        assert api.last_text() == IT.OWNER_ONLY
    assert await repo._all("SELECT * FROM invites") == []


# --- I14: диплинки из мини-приложения ---

async def test_i14_miniapp_deeplinks(owner, router, api, repo):
    _, aid = await owner_ids(repo)
    await owner.payload("g|prof_phone|")  # открытый сценарий профиля отменяется
    await start(owner, f"inv_new_{aid}")
    assert api.last_text().startswith("Изменение профиля отменили.")
    assert "start=inv_" in api.texts()[-2]
    assert (await session(repo)).state == S.IDLE
    await start(owner, f"inv_acc_{aid}")
    assert api.last_text() == IT.MEMBERS_EMPTY.format(label="Арбат 47к1, кв 32")
    await start(owner, "inv_new_999")
    assert api.last_text() == IT.OWNER_ONLY
    maria = Chat(router, api, UID3)
    await start(maria, f"inv_new_{aid}")  # незарегистрированный — обычная регистрация
    assert api.last_text() == RT.ASK_NAME


def test_start_prefix_and_short_name():
    assert start_prefix("inv_abc") == ("invite.start", "abc")
    assert start_prefix("INV_new_5") == ("invite.start", "new_5")
    assert start_prefix("inv_") is None and start_prefix("profile") is None and start_prefix(None) is None
    assert short_name("Иванова Анна Сергеевна") == "Анна И." and short_name("Анна") == "Анна"


# --- Миграция существующей БД ---

async def test_migration_v1_to_v2_keeps_data(tmp_path):
    path = tmp_path / "old.db"
    async with aiosqlite.connect(path) as db:  # БД первой версии с пользователем
        await db.executescript(SCHEMA_FILE.read_text("utf-8"))
        await db.execute("INSERT INTO users(max_user_id, full_name) VALUES(1, 'Иванова Анна')")
        await db.execute("PRAGMA user_version=1")
        await db.commit()
    repo = await Repo.open(path)
    try:
        async with repo.db.execute("PRAGMA user_version") as cur:
            assert (await cur.fetchone())[0] == SCHEMA_VERSION
        assert (await repo.get_user(1))["full_name"] == "Иванова Анна"
        assert await repo.get_invite("x") is None  # таблица есть
    finally:
        await repo.close()
    repo = await Repo.open(path)  # повторное открытие — без ошибок
    await repo.close()
