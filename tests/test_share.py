"""«Поделиться доступом»: ссылка на адреса собственника, получатель, «Общий доступ», удаление аккаунта (SH1–SH20)."""
from __future__ import annotations

import re
from datetime import timedelta

import aiosqlite
import pytest

from app import clock
from app import sharing as SH
from app.bot.router import start_prefix
from app.bot.states import S
from app.bot.texts import common as C
from app.bot.texts import invite as IT
from app.bot.texts import menu as MT
from app.bot.texts import meters as MeT
from app.bot.texts import profile as PT
from app.bot.texts import registration as RT
from app.db import MIGRATIONS, SCHEMA_FILE, SCHEMA_VERSION
from app.domain.addresses import norm_key
from app.domain.people import genitive, is_female, past, short_name, short_name_gen
from app.integrations.address_service import AddressService
from app.repo import Repo
from tests import fakes
from tests.conftest import NOW, Chat
from tests.test_registration import ADDRESS, UID, UID2, labels, last_kb, register, session

UID3 = 7000003
MARIA = "Смирнова Мария Павловна"
PETR = "Петров Пётр Иванович"
ARBAT = "г. Москва, Арбат, д. 47, корп. 1, кв. 32"
TVER = "Москва, Тверская 1, кв 5"


def sent_to(api, uid: int) -> list[str]:
    return [kw["text"] for kw in api.named("send") if kw["user_id"] == uid]


async def ids(repo, uid: int = UID) -> tuple[int, list[int]]:
    u = await repo.get_user(uid)
    return u["id"], [a["id"] for a in await repo.user_addresses(u["id"])]


async def add_address(repo, uid: int, text: str) -> int:
    """Ещё один адрес пользователю (как «Добавить адрес» в профиле)."""
    (c,) = AddressService().parse_local(text)
    u = await repo.get_user(uid)
    return (await repo.add_user_address(u["id"], c.to_dict(), norm_key(c), text, NOW.date()))["address_id"]


def token_of(api) -> str:
    link = next(kw for kw in reversed(api.named("send")) if "start=inv_" in kw["text"])
    return re.search(r"start=inv_([a-z0-9]+)", link["text"]).group(1)


async def share_one(chat: Chat, api) -> str:
    """Собственник с одним адресом жмёт «Поделиться доступом» → токен из ссылки."""
    await chat.payload("g|share|")
    return token_of(api)


async def start(chat: Chat, payload: str) -> None:
    await chat.feed(fakes.bot_started(chat.uid, payload))


@pytest.fixture
async def owner(router, api) -> Chat:
    chat = Chat(router, api, UID)
    await register(chat)
    return chat


@pytest.fixture
async def petr(router, api) -> Chat:
    """Зарегистрирован со своим адресом (Тверская — собственник)."""
    chat = Chat(router, api, UID2)
    await register(chat, name=PETR, phone="+7 900 111-22-33", address=TVER)
    return chat


# --- SH1–SH3: собственник создаёт ссылку ---

async def test_sh1_one_address_link_right_away(owner, api, repo):
    api.clear()
    await owner.payload("g|profile|")
    assert "Доступ: только вы" in api.last_text()
    assert IT.BTN_SHARE in labels(last_kb(api)) and IT.BTN_SHARED not in labels(last_kb(api))
    await owner.press(IT.BTN_SHARE)
    link, created = api.named("send")[-2:]
    assert link["keyboard"] is None and link["user_id"] == UID
    assert re.search(r"https://max\.ru/test_bot\?start=inv_[a-z0-9]{20}\b", link["text"])
    assert link["text"].startswith("Анна И. открывает вам доступ к адресу Арбат 47к1, кв 32 в боте ЖКХ в MAX")
    assert "сработает один раз, до 26 октября" in link["text"]
    assert created["text"] == IT.CREATED.format(until="26 октября")
    assert labels(created["keyboard"]) == [IT.BTN_SHARED, C.BTN_MENU]
    (inv,) = await repo._all("SELECT * FROM invites")
    uid, (aid,) = await ids(repo)
    assert (inv["owner_user_id"], inv["used_at"], inv["expires_at"]) == (uid, None, "2026-10-26 09:00:00")
    assert await repo._all("SELECT invite_id, address_id FROM invite_addresses") == [
        {"invite_id": inv["id"], "address_id": aid}]
    assert len("inv_" + inv["token"]) <= 128 and re.fullmatch(r"[A-Za-z0-9_-]+", "inv_" + inv["token"])
    await owner.payload("g|profile|")  # есть действующая ссылка — появился «Общий доступ»
    assert IT.BTN_SHARED in labels(last_kb(api))
    await owner.press(IT.BTN_SHARED)
    text = api.last_text()
    assert "Арбат 47к1, кв 32 — только вы" in text and "1. Арбат 47к1, кв 32 — до 26 октября" in text
    assert labels(last_kb(api)) == [IT.BTN_SHARE, IT.BTN_CANCEL_INVITE.format(n=1), PT.BTN_PROFILE, C.BTN_MENU]


async def test_sh2_multi_address_picker_toggles_in_place(owner, api, repo):
    second = await add_address(repo, UID, TVER)
    await owner.payload("g|share|")
    assert api.last_text() == IT.PICK
    assert labels(last_kb(api)) == ["Арбат 47к1, кв 32", "Тверская 1, кв 5", IT.BTN_ALL, IT.BTN_DONE, C.BTN_CANCEL]
    await owner.press(IT.BTN_DONE)  # ничего не отмечено
    assert api.named("edit")[-1]["text"] == IT.PICK_EMPTY + "\n\n" + IT.PICK
    await owner.press("Тверская 1, кв 5")
    edit = api.named("edit")[-1]
    assert edit["mid"] == "mid.bot.x" and edit["text"] == IT.PICK
    assert labels(edit["keyboard"]) == ["Арбат 47к1, кв 32", "✓ Тверская 1, кв 5", IT.BTN_ALL, IT.BTN_DONE,
                                        C.BTN_CANCEL]
    await owner.press("✓ Тверская 1, кв 5")  # снять отметку
    assert "✓" not in str(labels(api.named("edit")[-1]["keyboard"]))
    await owner.press(IT.BTN_ALL)
    assert labels(api.named("edit")[-1]["keyboard"]) == ["✓ Арбат 47к1, кв 32", "✓ Тверская 1, кв 5",
                                                         IT.BTN_DONE, C.BTN_CANCEL]
    api.clear()
    await owner.press(IT.BTN_DONE)
    assert api.named("edit")[-1] == {"mid": "mid.bot.x", "text": None, "keyboard": None}  # выбор без кнопок
    link = api.named("send")[-2]["text"]
    assert "к адресам: Арбат 47к1, кв 32; Тверская 1, кв 5 в боте" in link
    (inv,) = await repo.owner_invites((await repo.get_user(UID))["id"], clock.now())
    assert sorted(inv["address_ids"]) == sorted([(await ids(repo))[1][0], second])
    # из карточки адреса — этот адрес уже отмечен
    await owner.payload(f"g|share|{second}")
    assert labels(last_kb(api))[:2] == ["Арбат 47к1, кв 32", "✓ Тверская 1, кв 5"]


async def test_sh3_limit_five_and_cancel(owner, api, repo):
    for _ in range(SH.INVITE_LIMIT):
        await share_one(owner, api)
    await owner.payload("g|share|")
    assert api.last_text() == IT.LIMIT.format(count=5, until="26 октября")
    assert len(await repo._all("SELECT * FROM invites")) == 5
    await owner.payload("g|sh_list|")
    await owner.press(IT.BTN_CANCEL_INVITE.format(n=2))
    assert api.last_text().startswith(IT.INVITE_CANCELLED.format(labels="Арбат 47к1, кв 32"))
    assert IT.BTN_CANCEL_INVITE.format(n=5) not in labels(last_kb(api))
    await owner.payload("g|share|")
    assert api.last_text() == IT.CREATED.format(until="26 октября")
    clock.set_now(NOW + timedelta(days=8))  # старые истекли
    await owner.payload("g|sh_list|")
    assert IT.SHARED_INVITES not in api.last_text()


# --- SH4–SH7: незарегистрированный по ссылке ---

async def test_sh4_unregistered_without_own_address(owner, router, api, repo):
    token = await share_one(owner, api)
    maria = Chat(router, api, UID3)
    api.clear()
    await start(maria, "inv_" + token)
    first = api.texts()[0]
    assert first.startswith("Анна И. хочет открыть вам доступ к адресу Арбат 47к1, кв 32. Сначала коротко")
    assert C.WELCOME in first and "Откроем доступ к квартире" in C.WELCOME
    assert (await session(repo, UID3)).data["invite"] == token
    await maria.text(MARIA)
    await maria.text("+7 900 555-11-22")
    assert api.last_text() == IT.ASK_INVITE_ADDRESS.format(addresses="адресу Арбат 47к1, кв 32")
    assert labels(last_kb(api)) == [IT.BTN_NO_OWN_ADDRESS, C.BTN_BACK]
    await maria.press(IT.BTN_NO_OWN_ADDRESS)
    assert (await session(repo, UID3)).state == S.REG_CONFIRM
    assert "Доступ от Анны И.: Арбат 47к1, кв 32" in api.last_text() and "ФИАС" not in api.last_text()
    api.clear()
    await maria.press(RT.BTN_ALL_OK)
    u = await repo.get_user(UID3)
    assert u["registered_at"] and u["full_name"] == MARIA
    (a,) = await repo.user_addresses(u["id"])
    uid, (aid,) = await ids(repo)
    assert (a["id"], a["role"], a["access"], a["granted_by"]) == (aid, "tenant", "granted", uid)
    mine = sent_to(api, UID3)
    assert any(t.startswith(RT.DONE + "\n" + IT.REG_ACCEPTED.format(by="Анны И.", addresses="адресу Арбат 47к1, кв 32"))
               for t in mine)
    assert MT.SHARED.format(label="Арбат 47к1, кв 32", by="Анны И.") in mine[-1]  # дашборд
    assert not any("уже зарегистрирован собственник" in t for t in mine)
    assert sent_to(api, UID) == [IT.OWNER_ACCEPTED.format(name="Мария С.", addresses="адресу Арбат 47к1, кв 32")]
    assert (await repo.get_invite(token))["used_by"] == u["id"]
    await start(maria, "inv_" + token)  # повторно — уже использована
    assert api.last_text() == IT.INVALID["used"]


async def test_sh5_unregistered_with_own_new_address(owner, router, api, repo):
    """B1: свой новый адрес (стал собственником) — ссылка всё равно применяется."""
    await add_address(repo, UID, "Москва, Ленина 5, кв 1")
    await owner.payload("g|share|")
    await owner.press(IT.BTN_ALL)
    await owner.press(IT.BTN_DONE)
    token = token_of(api)
    maria = Chat(router, api, UID3)
    await start(maria, "inv_" + token)
    await maria.text(MARIA)
    await maria.text("+7 900 555-11-22")
    assert "к адресам: Арбат 47к1, кв 32; Ленина 5, кв 1" in api.last_text()
    await maria.text(TVER)
    await maria.press(RT.BTN_YES)
    text = api.last_text()
    assert "Тверская" in text and "Доступ от Анны И.: Арбат 47к1, кв 32; Ленина 5, кв 1" in text
    await maria.press(RT.BTN_ALL_OK)
    u = await repo.get_user(UID3)
    got = sorted((a["label"], a["role"], a["access"]) for a in await repo.user_addresses(u["id"]))
    assert got == [("Арбат 47к1, кв 32", "tenant", "granted"), ("Ленина 5, кв 1", "tenant", "granted"),
                   ("Тверская 1, кв 5", "owner", "granted")]


async def test_sh6_unregistered_types_invite_address_no_duplicate(owner, router, api, repo):
    token = await share_one(owner, api)
    maria = Chat(router, api, UID3)
    await start(maria, "inv_" + token)
    await maria.text(MARIA)
    await maria.text("+7 900 555-11-22")
    await maria.text(ADDRESS)  # живёт там же — адрес из ссылки вписала сама
    await maria.press(RT.BTN_YES)
    api.clear()
    await maria.press(RT.BTN_ALL_OK)
    u = await repo.get_user(UID3)
    assert [(a["role"], a["access"]) for a in await repo.user_addresses(u["id"])] == [("tenant", "granted")]
    assert not any("уже зарегистрирован собственник" in t for t in api.texts())


async def test_sh7_link_expired_during_registration(owner, router, api, repo):
    token = await share_one(owner, api)
    maria = Chat(router, api, UID3)
    await start(maria, "inv_" + token)
    await maria.text(MARIA)
    await maria.text("+7 900 555-11-22")
    await maria.press(IT.BTN_NO_OWN_ADDRESS)
    clock.set_now(NOW + timedelta(days=8))
    api.clear()
    await maria.press(RT.BTN_ALL_OK)
    assert api.last_text() == IT.REG_NOT_APPLIED + "\n\n" + RT.ASK_ADDRESS  # адрес теперь нужен
    assert (await session(repo, UID3)).state == S.REG_ADDRESS
    assert not (await repo.get_user(UID3))["registered_at"]
    await maria.text(TVER)
    await maria.press(RT.BTN_YES)
    await maria.press(RT.BTN_ALL_OK)
    u = await repo.get_user(UID3)
    assert [(a["role"], a["access"]) for a in await repo.user_addresses(u["id"])] == [("owner", "granted")]


async def test_sh8_restart_keeps_link(owner, router, api, repo):
    token = await share_one(owner, api)
    maria = Chat(router, api, UID3)
    await maria.text("привет")
    await maria.text(MARIA)
    await start(maria, "inv_" + token)  # открыла ссылку посреди регистрации
    assert api.texts()[-2].startswith(IT.REG_KEPT) and C.CONTINUE_REG in api.texts()[-2]
    await maria.press(C.BTN_RESTART)
    assert (await session(repo, UID3)).data["invite"] == token
    await maria.text(MARIA)
    await maria.text("+7 900 555-11-22")
    await maria.press(IT.BTN_NO_OWN_ADDRESS)
    await maria.press(RT.BTN_ALL_OK)
    assert (await repo.user_addresses((await repo.get_user(UID3))["id"]))[0]["access"] == "granted"


# --- SH9–SH11: зарегистрированный ---

async def test_sh9_registered_with_own_address_pending_to_granted(owner, router, api, repo):
    petr = Chat(router, api, UID2)
    await register(petr, name=PETR, phone="+7 900 111-22-33")  # Арбат — tenant/pending
    await add_address(repo, UID2, TVER)  # и свой адрес
    lenina = await add_address(repo, UID, "Москва, Ленина 5, кв 1")
    await owner.payload("g|share|")
    await owner.press(IT.BTN_ALL)
    await owner.press(IT.BTN_DONE)
    token = token_of(api)
    api.clear()
    await start(petr, "inv_" + token)
    assert api.last_text() == IT.OFFER.format(owner="Анна И.",
                                              addresses="адресам: Арбат 47к1, кв 32; Ленина 5, кв 1")
    assert labels(last_kb(api)) == [IT.BTN_ACCEPT, IT.BTN_DECLINE]
    await petr.press(IT.BTN_ACCEPT)
    text = api.last_text()
    assert text.startswith("Готово, доступ от Анны И. к адресам:") and "Ленина 5, кв 1" in text
    assert labels(last_kb(api)) == [PT.BTN_SUBMIT, C.BTN_MENU]
    tid = (await repo.get_user(UID2))["id"]
    uid, (arbat, _) = await ids(repo)
    for aid in (arbat, lenina):
        row = await repo.user_address(tid, aid)
        assert (row["role"], row["access"], row["granted_by"]) == ("tenant", "granted", uid)
    assert IT.OWNER_ACCEPTED.format(name="Пётр П.", addresses="адресам: Арбат 47к1, кв 32; Ленина 5, кв 1") in \
        sent_to(api, UID)
    # старый запрос доступа: «Разрешить» — уже решено, второго сообщения Петру нет
    before = len(sent_to(api, UID2))
    await owner.payload(f"g|acc_ok|{tid}.{arbat}")
    assert api.last_text() == PT.DECIDED["granted"] and len(sent_to(api, UID2)) == before
    # Пётр сам делится своим адресом
    await petr.payload("g|share|")
    assert "к адресу Тверская 1, кв 5" in api.named("send")[-2]["text"]


async def test_sh10_decline_then_accept_and_already(owner, petr, api, repo):
    token = await share_one(owner, api)
    await start(petr, "inv_" + token)
    await petr.press(IT.BTN_DECLINE)
    assert api.last_text() == IT.DECLINED and (await repo.get_invite(token))["used_at"] is None
    await start(petr, "inv_" + token)
    await petr.press(IT.BTN_ACCEPT)
    await petr.press(IT.BTN_ACCEPT)  # повторное нажатие
    assert api.last_text() == IT.INVALID["used"]
    token2 = await share_one(owner, api)
    await start(petr, "inv_" + token2)  # доступ уже есть — ссылку не тратим
    assert api.last_text() == IT.ALREADY.format(addresses="адресу Арбат 47к1, кв 32")
    assert (await repo.get_invite(token2))["used_at"] is None


async def test_sh11_self_link(owner, api, repo):
    token = await share_one(owner, api)
    await start(owner, "inv_" + token)
    assert api.last_text() == IT.SELF and labels(last_kb(api)) == [IT.BTN_SHARED, C.BTN_MENU]
    await owner.payload(f"g|inv_ok|{token}")
    assert api.last_text() == IT.SELF
    assert (await repo.get_invite(token))["used_at"] is None


@pytest.mark.parametrize("case", ["unknown", "expired", "used", "cancelled"])
async def test_sh12_bad_links(owner, petr, router, api, repo, case):
    token = await share_one(owner, api)
    if case == "unknown":
        token = "zzz" + token[3:]
    elif case == "expired":
        clock.set_now(NOW + timedelta(days=7, minutes=1))
    elif case == "used":
        other = await repo.ensure_user(7000009)
        assert (await repo.use_invite(token, other["id"], clock.now()))["granted"]
    else:
        await owner.payload("g|sh_list|")
        await owner.press(IT.BTN_CANCEL_INVITE.format(n=1))
    await start(petr, "inv_" + token)
    assert api.last_text() == IT.INVALID[case] and labels(last_kb(api)) == [C.BTN_MENU]
    await petr.payload(f"g|inv_ok|{token}")
    assert api.last_text() == IT.INVALID[case]
    maria = Chat(router, api, UID3)  # незарегистрированный: понятный текст и обычная регистрация
    api.clear()
    await start(maria, "inv_" + token)
    assert api.texts()[0].startswith(IT.INVALID[case]) and api.last_text() == RT.ASK_NAME
    assert "invite" not in (await session(repo, UID3)).data


def test_sh12_no_bad_link_text_is_missing():
    assert set(IT.INVALID) == {"unknown", "expired", "used", "cancelled", "gone"}


# --- SH13–SH15: «Общий доступ», закрыть доступ, выйти из общего доступа ---

async def _petr_granted(owner, petr, api) -> None:
    token = await share_one(owner, api)
    await start(petr, "inv_" + token)
    await petr.press(IT.BTN_ACCEPT)


async def test_sh13_owner_list_statuses_and_close(owner, petr, router, api, repo):
    await _petr_granted(owner, petr, api)
    maria = Chat(router, api, UID3)
    await register(maria, name=MARIA, phone="+7 900 555-11-22")  # тот же адрес — запросила доступ (pending)
    uid, (aid,) = await ids(repo)
    tid = (await repo.get_user(UID2))["id"]
    meter = await repo.create_meter(aid, "cold_water", 1, None)
    await repo.add_reading(meter, tid, "2026-10", {"t1": 1000, "t2": None, "t3": None}, "manual")
    await owner.payload("g|sh_list|")
    assert IT.SHARED_OWNED_LINE.format(label="Арбат 47к1, кв 32", names="Пётр П., Мария С. (ждёт)") in api.last_text()
    await owner.press("Арбат 47к1, кв 32")
    text = api.last_text()
    assert "1. Пётр П. — показания 19.10" in text and "2. Мария С. — ждёт одобрения" in text
    assert labels(last_kb(api)) == [IT.BTN_REVOKE.format(name="Пётр П."), IT.BTN_ALLOW.format(name="Мария С."),
                                    IT.BTN_SHARE, C.BTN_BACK, C.BTN_MENU]
    clock.set_now(NOW + timedelta(days=13))  # 1 ноября: за ноябрь Пётр ещё не подавал
    await owner.press("Арбат 47к1, кв 32")
    assert "1. Пётр П. — за ноябрь показаний нет" in api.last_text()
    await owner.press(IT.BTN_REVOKE.format(name="Пётр П."))
    assert api.last_text() == IT.REVOKE_ASK.format(name="Пётр П.", label=ARBAT)
    assert (await repo.user_address(tid, aid))["access"] == "granted"  # пока не подтвердили
    api.clear()
    await owner.press(IT.BTN_REVOKE_YES)
    assert (await repo.user_address(tid, aid))["access"] == "denied"
    assert sent_to(api, UID2) == [IT.TENANT_REVOKED.format(label=ARBAT)]
    assert api.last_text() == IT.REVOKED.format(name="Пётр П.", label=ARBAT)
    assert len(await repo.history(meter)) == 1  # поданное остаётся
    await owner.press(IT.BTN_REVOKE_YES)
    assert api.last_text() == IT.REVOKE_ALREADY.format(name="Пётр П.")
    # у Петра адрес помечен, можно выйти из общего доступа — собственнику не пишем (доступ уже закрыт)
    await petr.payload("g|sh_list|")
    assert f"Арбат 47к1, кв 32 — {PT.ROLE[('tenant', 'denied')]}" in api.last_text()
    await petr.payload(f"g|sh_rm|{aid}")
    assert api.last_text() == (f"Выйти из общего доступа к адресу {ARBAT}?\n\n"
                               "Адрес пропадёт из вашего списка. Переданные показания сохранятся.")
    # новая ссылка возвращает доступ (denied → granted)
    token = await share_one(owner, api)
    await start(petr, "inv_" + token)
    await petr.press(IT.BTN_ACCEPT)
    assert (await repo.user_address(tid, aid))["access"] == "granted"


async def test_sh14_remove_at_self_notifies_owner(owner, petr, api, repo):
    await _petr_granted(owner, petr, api)
    uid, (aid,) = await ids(repo)
    tid = (await repo.get_user(UID2))["id"]
    meter = await repo.create_meter(aid, "cold_water", 1, None)
    await repo.add_reading(meter, tid, "2026-10", {"t1": 1000, "t2": None, "t3": None}, "manual")
    await petr.payload("g|profile|")
    assert f"{ARBAT} — доступ от Анны И." in api.last_text() and IT.BTN_SHARED in labels(last_kb(api))
    await petr.press(IT.BTN_SHARED)
    assert "Вам открыли доступ:\nАрбат 47к1, кв 32 — доступ от Анны И." in api.last_text()
    await petr.press("Арбат 47к1, кв 32")
    assert api.last_text().startswith(f"**{ARBAT}** — доступ от Анны И.")
    assert "Изменить адрес может только собственник" in api.last_text()
    assert labels(last_kb(api)) == [IT.BTN_REMOVE, C.BTN_BACK, C.BTN_MENU]
    await petr.press(IT.BTN_REMOVE)
    assert api.last_text() == (f"Выйти из общего доступа к адресу {ARBAT}?\n\nАдрес пропадёт только у вас, "
                               "Анна И. получит уведомление. Переданные показания сохранятся.")
    assert labels(last_kb(api)) == ["Выйти", "Отмена"]
    api.clear()
    await petr.press(IT.BTN_REMOVE_YES)
    assert api.last_text() == f"Вы вышли из общего доступа к адресу {ARBAT}. Анна И. получит уведомление."
    assert await repo.user_address(tid, aid) is None
    assert sent_to(api, UID) == [f"Пётр П. вышел из общего доступа к адресу {ARBAT}. Переданные показания остались."]
    assert (await repo.history(meter))[0]["user_id"] == tid  # показания остались за адресом
    await owner.payload(f"g|acc_list|{aid}")
    assert api.last_text() == IT.MEMBERS_EMPTY.format(label=f"**{ARBAT}**")
    await petr.press(IT.BTN_REMOVE_YES)  # повторно — адреса уже нет
    assert api.last_text().startswith(IT.ADDRESS_GONE)


async def test_sh15_owner_cannot_remove_own_or_close_self(owner, petr, api, repo):
    uid, (aid,) = await ids(repo)
    await owner.payload(f"g|sh_rm|{aid}")
    assert api.last_text() == IT.REMOVE_OWN
    await owner.payload(f"g|sh_rm_ok|{aid}")
    assert api.last_text() == IT.REMOVE_OWN and await repo.user_address(uid, aid)
    await owner.payload(f"g|acc_rev_ok|{uid}.{aid}")
    assert api.last_text() == IT.REVOKE_SELF
    tid = (await repo.get_user(UID2))["id"]
    for payload in (f"g|acc_rev|{uid}.{aid}", f"g|acc_rev_ok|{tid}.{aid}", f"g|share|{aid}"):
        await petr.payload(payload)  # чужой адрес
        assert api.last_text() == IT.OWNER_ONLY, payload
    await petr.payload(f"g|acc_list|{aid}")  # чужого адреса нет в профиле
    assert api.last_text().startswith(IT.ADDRESS_GONE)
    assert await repo._all("SELECT * FROM invites") == []


# --- SH16–SH17: удаление аккаунта ---

async def test_sh16_recipient_deletes_account(owner, petr, api, repo):
    await _petr_granted(owner, petr, api)
    uid, (aid,) = await ids(repo)
    meter = await repo.create_meter(aid, "cold_water", 1, None)
    await repo.add_reading(meter, (await repo.get_user(UID2))["id"], "2026-10", {"t1": 1, "t2": None, "t3": None},
                           "manual")
    api.clear()
    await petr.payload("g|prof_del|")
    await petr.press(PT.BTN_DELETE_YES)
    assert await repo.get_user(UID2) is None
    assert sent_to(api, UID) == [IT.MEMBER_DELETED.format(name="Пётр П.", addresses="адресу Арбат 47к1, кв 32")]
    assert await repo.user_address(uid, aid) and len(await repo.history(meter)) == 1  # у собственника всё на месте
    await owner.payload(f"g|acc_list|{aid}")
    assert api.last_text() == IT.MEMBERS_EMPTY.format(label=f"**{ARBAT}**")


async def test_sh17_owner_deletes_account_address_goes_to_recipient(owner, petr, router, api, repo):
    await _petr_granted(owner, petr, api)
    await share_one(owner, api)  # ещё одна ссылка — удалится вместе с собственником
    maria = Chat(router, api, UID3)
    await register(maria, name=MARIA, phone="+7 900 555-11-22")  # запросила доступ (pending)
    _, (aid,) = await ids(repo)
    api.clear()
    await owner.payload("g|prof_del|")
    assert "останутся у тех, кому вы открыли доступ" in api.last_text()
    await owner.press(PT.BTN_DELETE_YES)
    tid = (await repo.get_user(UID2))["id"]
    row = await repo.user_address(tid, aid)
    assert (row["role"], row["access"], row["granted_by"]) == ("owner", "granted", None)
    (promoted,) = sent_to(api, UID2)
    assert promoted.startswith(IT.PROMOTED.format(label=ARBAT)) and PT.RIGHTS_MODEL in promoted
    assert sent_to(api, UID3) == [] and await repo._all("SELECT * FROM invites") == []
    await petr.payload("g|profile|")
    assert f"{ARBAT} — собственник" in api.last_text()
    await petr.payload(f"g|acc_list|{aid}")
    assert "1. Мария С. — ждёт одобрения" in api.last_text()  # запрос Марии теперь решает Пётр


# --- SH18: роль в интерфейсе и «кто подавал» ---

async def test_sh18_shared_address_shown_as_access_from(owner, petr, api, repo):
    await _petr_granted(owner, petr, api)
    uid, (aid,) = await ids(repo)
    tid = (await repo.get_user(UID2))["id"]
    meter = await repo.create_meter(aid, "cold_water", 1, None)
    await repo.add_reading(meter, uid, "2026-10", {"t1": 118_200, "t2": None, "t3": None}, "manual")
    await petr.payload("g|menu|")
    assert MT.SHARED.format(label="Арбат 47к1, кв 32", by="Анны И.") in api.last_text()
    await petr.payload("g|meters|")
    text = api.last_text()
    assert "Последнее: **118,200 м³** (19.10, от Анны И.)" in text
    assert MT.SHARED.format(label="Арбат 47к1, кв 32", by="Анны И.") in text
    assert IT.BTN_SHARE in labels(last_kb(api))  # у Петра свой адрес Тверская
    await petr.payload(f"g|meter|{meter}")
    text = api.last_text()
    assert MeT.CARD_ADDRESS_SHARED.format(address=ARBAT, by="Анны И.") in text
    assert "Последнее показание: **118,200 м³**, **19.10**, от Анны И." in text
    await repo.add_reading(meter, tid, "2026-10", {"t1": 119_000, "t2": None, "t3": None}, "manual", replace=True)
    await owner.payload(f"g|meter|{meter}")
    text = api.last_text()
    assert "от Петра П." in text and "доступ от" not in text  # собственнику — свой адрес
    await owner.payload("g|profile|")
    assert f"{ARBAT} — собственник\nДоступ: Пётр П." in api.last_text()


# --- SH19: диплинки, помощь, устаревшие кнопки ---

async def test_sh19_deeplinks_help_and_stale(owner, router, api, repo):
    _, (aid,) = await ids(repo)
    await owner.payload("g|prof_phone|")  # открытый сценарий профиля отменяется
    await start(owner, f"inv_new_{aid}")
    assert api.last_text().startswith(C.PROFILE_CANCELLED)
    assert "start=inv_" in api.texts()[-2] and (await session(repo)).state == S.IDLE
    await start(owner, f"inv_acc_{aid}")
    assert api.last_text() == IT.MEMBERS_EMPTY.format(label=f"**{ARBAT}**")
    await start(owner, "inv_new_999")
    assert api.last_text() == IT.OWNER_ONLY
    await owner.payload("g|inv_new|")  # старая кнопка «Пригласить жильца»
    assert api.last_text() == IT.CREATED.format(until="26 октября")
    await owner.payload("g|acc_list|")  # старая «Управлять доступом» без адреса
    assert api.last_text().startswith(IT.SHARED_TITLE)
    await owner.payload("g|sh_sel|999.abc")  # чужие/битые id в переключателе
    await owner.payload("g|inv_cancel|999")
    assert api.last_text().startswith(IT.INVITE_CANCEL_GONE)
    # помощь: кнопка в меню, /help, start=help
    await owner.payload("g|menu|")
    assert MT.BTN_HELP in labels(last_kb(api))
    await owner.press(MT.BTN_HELP)
    assert api.last_text() == C.WELCOME and labels(last_kb(api)) == [MT.BTN_SUBMIT, C.BTN_MENU]
    await owner.text("/help")
    assert api.last_text() == C.WELCOME
    await start(owner, "help")
    assert api.last_text() == C.WELCOME
    maria = Chat(router, api, UID3)
    await start(maria, f"inv_new_{aid}")  # незарегистрированный — обычная регистрация
    assert api.last_text() == RT.ASK_NAME


async def test_sh20_no_owned_addresses(owner, router, api, repo):
    petr = Chat(router, api, UID2)
    await register(petr, name=PETR, phone="+7 900 111-22-33")  # только запрос доступа к чужому адресу
    await petr.payload("g|share|")
    assert api.last_text().startswith(IT.NO_OWNED) and PT.RIGHTS_MODEL in api.last_text()
    await petr.payload("g|profile|")
    assert IT.BTN_SHARE not in labels(last_kb(api)) and "Доступ:" not in api.last_text()


def test_start_prefix_and_names():
    assert start_prefix("inv_abc") == ("invite.start", "abc")
    assert start_prefix("INV_new_5") == ("invite.start", "new_5")
    assert start_prefix("inv_") is None and start_prefix("profile") is None and start_prefix(None) is None
    assert short_name("Иванова Анна Сергеевна") == "Анна И." and short_name_gen("Иванова Анна Сергеевна") == "Анны И."
    cases = {"Анна": "Анны", "Ольга": "Ольги", "Мария": "Марии", "Илья": "Ильи", "Сергей": "Сергея",
             "Игорь": "Игоря", "Олег": "Олега", "Пётр": "Петра", "Павел": "Павла", "Любовь": "Любови",
             "Наталья": "Натальи", "Никита": "Никиты", "Нелли": "Нелли"}
    assert {k: genitive(k) for k in cases} == cases
    # род для «вышел/вышла», «открыл/открыла»: отчество главнее имени; не угадать — «(а)»
    people = {"Смирнова Мария Павловна": "вышла", "Петров Пётр Иванович": "вышел", "Кузьмин Никита": "вышел",
              "Орлова Анна": "вышла", "Козлов Игорь": "вышел", "Иванова Любовь": "вышла", "Белых Саша": "вышел(а)",
              "Ким Женя Сергеевна": "вышла", "Аскаров Ильдар Тимур оглы": "вышел", "Пётр": "вышел"}
    assert {k: past(k, "вышел") for k in people} == people
    assert past("Смирнова Мария Павловна", "открыл") == "открыла" and is_female(None) is None


# --- repo: принятие, свой адрес, адрес уже не отправителя ---

async def test_use_invite_skips_own_and_gone(owner, petr, api, repo):
    lenina = await add_address(repo, UID, "Москва, Ленина 5, кв 1")
    uid, (arbat, _) = await ids(repo)
    tid, (tver,) = await ids(repo, UID2)
    inv, err = await SH.create_invite(repo, uid, [arbat, lenina], clock.now())
    assert err is None
    await repo._exec("INSERT INTO user_addresses(user_id, address_id, role, access, label) "
                     "VALUES(?, ?, 'owner', 'granted', 'x')", (tid, lenina))  # Пётр — будто собственник Ленина
    res = await repo.use_invite(inv["token"], tid, clock.now())
    assert (res["granted"], res["own"]) == ([arbat], [lenina])
    assert await SH.create_invite(repo, uid, [], clock.now()) == (None, "empty")
    assert await SH.create_invite(repo, uid, [tver], clock.now()) == (None, "not_owner")


# --- Миграция v3 → v4 ---

async def test_migration_v3_to_v4_moves_invites_and_backfills_granted_by(tmp_path):
    path = tmp_path / "v3.db"
    async with aiosqlite.connect(path) as db:
        await db.executescript(SCHEMA_FILE.read_text("utf-8") + MIGRATIONS[2] + MIGRATIONS[3])
        await db.executescript(
            "INSERT INTO users(id, max_user_id, full_name) VALUES(1, 1, 'Иванова Анна'), (2, 2, 'Петров Пётр');"
            "INSERT INTO addresses(id, norm_key, status, source, full_text) VALUES(1, 'k', 'unverified', 'local', 'A');"
            "INSERT INTO user_addresses(user_id, address_id, role, access, label, created_at) VALUES"
            " (1, 1, 'owner', 'granted', 'A', '2026-10-01 10:00:00'),"
            " (2, 1, 'tenant', 'granted', 'A', '2026-10-02 10:00:00');"
            "INSERT INTO invites(token, address_id, owner_user_id, created_at, expires_at) VALUES"
            " ('tok1', 1, 1, '2026-10-18 10:00:00', '2026-10-25 10:00:00'),"
            " ('tok2', 1, 1, '2026-10-19 10:00:00', '2026-10-26 10:00:00');"
            "PRAGMA user_version=3;")
        await db.commit()
    repo = await Repo.open(path)
    try:
        async with repo.db.execute("PRAGMA user_version") as cur:
            assert (await cur.fetchone())[0] == SCHEMA_VERSION == 4
        inv = await repo.get_invite("tok2")
        assert (inv["id"], inv["address_ids"], inv["cancelled_at"]) == (2, [1], None)
        assert [i["token"] for i in await repo.owner_invites(1, NOW)] == ["tok1", "tok2"]
        t = await repo.user_address(2, 1)
        assert (t["granted_by"], t["granted_at"], t["granted_by_name"]) == (1, "2026-10-02 10:00:00", "Иванова Анна")
        assert (await repo.user_address(1, 1))["granted_by"] is None
        with pytest.raises(Exception):  # внешние ключи снова включены
            await repo._exec("INSERT INTO invite_addresses(invite_id, address_id) VALUES(999, 1)")
    finally:
        await repo.close()
    repo = await Repo.open(path)  # повторное открытие — без ошибок
    await repo.close()
