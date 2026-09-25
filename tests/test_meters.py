"""Карточка счётчика и удаление (flows/meters.py, repo.delete_meter) через роутер и FakeMaxApi."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from app import clock
from app.bot.states import S
from app.bot.texts import common as C
from app.bot.texts import meters as T
from app.bot.texts import menu as MT
from app.bot.texts import submission as TS
from app.domain.access import meter_delete_denial
from app.readings import submit_reading
from app.scheduler import notify_tick
from tests.conftest import NOW, Chat
from tests.test_submission import ARBAT, LABEL, OTHER, FixedRecognizer, buttons, meter, register, state

CARD_ROWS = [[T.BTN_SUBMIT], [T.BTN_DELETE, C.BTN_BACK]]


def rows(api) -> list[list[str]]:
    kb = api.outgoing()[-1][1] or {}
    return [[b["text"] for b in row] for row in kb["payload"]["buttons"]]


async def active(repo, mid: int) -> int:
    return (await repo.get_meter(mid))["active"]


@pytest.fixture
async def user(repo):
    return await register(repo)


@pytest.fixture
async def cold(repo, user):
    """Холодная вода с номером и показанием за сентябрь, поверка до 15.03.2030 из паспорта."""
    mid = await meter(repo, user, serial="18-123456", readings=[("2026-09", 118_200)])
    await repo.set_verification(mid, "2030-03-15", "user")
    return mid


async def open_card(chat: Chat, label: str = LABEL) -> None:
    await chat.payload("g|meters|")
    await chat.press(label)


# --- Карточка ---

async def test_card_from_my_meters(chat, api, repo, cold):
    await chat.payload("g|meters|")
    assert T.LIST_HINT in api.last_text()
    assert rows(api) == [[LABEL], [MT.BTN_ADD_METER, C.BTN_MENU]]
    await chat.press(LABEL)
    text = api.last_text()
    assert text.startswith(f"**{LABEL}**\nАдрес: ")
    assert "Номер: **18-123456**" in text
    assert "Последнее показание: **118,200 м³**, **19.10**" in text
    assert "Поверка: до **15.03.2030**, из паспорта" in text
    assert text.endswith("\n\n> " + T.NOTE_ADDRESS)  # адрес без DaData — не сверен с ФИАС, оговорка последней
    assert rows(api) == CARD_ROWS
    assert api.button(T.BTN_DELETE)["payload"] == f"g|m_del|{cold}"


async def test_card_model_verification_note(chat, api, repo, user):
    mid = await meter(repo, user, serial=None)
    await repo.set_verification(mid, "2031-01-10", "model")
    await open_card(chat)
    text = api.last_text()
    assert "Номер: не указан" in text and "Показаний пока нет" in text
    assert "Поверка: до **10.01.2031**, ориентировочно" in text
    assert text.endswith(f"> {T.NOTE_ADDRESS} {T.NOTE_MODEL}")


async def test_submit_from_card_preselects_meter(chat, api, repo, deps, cold):
    await open_card(chat)
    await chat.press(T.BTN_SUBMIT)
    assert api.last_text() == T.SUBMIT_FOR.format(meter=LABEL) + "\n\n" + TS.INSTRUCTION
    s = await state(repo)
    assert s.state == S.SUB_AWAIT_PHOTO and s.data["meter_id"] == cold
    deps.recognizer = FixedRecognizer({"t1": 119_000})
    await chat.photo()  # без выбора счётчика — сразу распознавание и проверка
    assert (await state(repo)).state == S.SUB_REVIEW and api.last_text().startswith(LABEL)


# --- Удаление ---

async def test_delete_with_confirmation(chat, api, repo, user, cold):
    other = await meter(repo, user, "electricity", serial=None)
    await open_card(chat)
    await chat.press(T.BTN_DELETE)
    assert api.last_text() == T.ASK_DELETE.format(meter=LABEL)
    assert "**Хол. вода · Арбат 47к1, кв 32**" in api.last_text()
    assert rows(api) == [[T.BTN_DELETE_YES, C.BTN_CANCEL]]
    assert await active(repo, cold) == 1  # до подтверждения ничего не удалили
    await chat.press(T.BTN_DELETE_YES)
    text = api.last_text()
    assert text.startswith(T.DELETED.format(meter=LABEL) + "\n\n" + MT.METERS_TITLE)
    assert rows(api) == [["Свет · Арбат 47к1, кв 32"], [MT.BTN_ADD_METER, C.BTN_MENU]]
    row = await repo.get_meter(cold)
    assert (row["active"], row["serial"], row["serial_norm"]) == (0, "18-123456", None)
    assert len(await repo.history(cold)) == 1  # показания остались
    assert await active(repo, other) == 1


async def test_delete_cancel_returns_to_card(chat, api, repo, cold):
    await open_card(chat)
    await chat.press(T.BTN_DELETE)
    await chat.press(C.BTN_CANCEL)
    assert api.last_text().startswith(f"**{LABEL}**") and rows(api) == CARD_ROWS
    assert await active(repo, cold) == 1


async def test_stale_and_repeated_buttons_are_safe(chat, api, repo, cold):
    await open_card(chat)
    await chat.press(T.BTN_DELETE)
    await chat.press(T.BTN_DELETE_YES)
    for payload in (f"g|m_del_ok|{cold}", f"g|m_del|{cold}", f"g|meter|{cold}", f"g|m_sub|{cold}",
                    "g|meter|999", "g|m_del_ok|abc"):
        api.clear()
        await chat.payload(payload)
        assert api.last_text().startswith(T.GONE + "\n\n" + MT.NO_METERS), payload
        assert rows(api) == [[MT.BTN_ADD_METER, C.BTN_MENU]]
    assert (await state(repo)).state == S.IDLE


async def test_tenant_cannot_delete_when_owner_exists(router, api, repo, user, cold):
    tenant = await register(repo, OTHER, ARBAT)  # тот же адрес → tenant/pending
    await repo.set_access(tenant[0], user[1], "granted")
    chat = Chat(router, api, OTHER)
    await open_card(chat)
    await chat.press(T.BTN_DELETE)
    assert api.last_text() == T.NOT_OWNER and rows(api) == [[C.BTN_BACK, C.BTN_MENU]]
    await chat.payload(f"g|m_del_ok|{cold}")  # и в обход вопроса
    assert api.last_text() == T.NOT_OWNER
    assert await active(repo, cold) == 1
    # Собственник при жильце — может.
    assert await repo.delete_meter(user[0], cold) == "ok"


async def test_pending_tenant_has_no_access(repo, user, cold):
    tenant = await register(repo, OTHER, ARBAT)
    assert await repo.delete_meter(tenant[0], cold) == "no_access"
    assert await repo.delete_meter(user[0], 999) == "not_found"


def test_delete_rights_model():
    assert meter_delete_denial("owner", "granted", 3) is None
    assert meter_delete_denial("tenant", "granted", 0) is None  # один на адресе — может
    assert meter_delete_denial("tenant", "granted", 1) == "not_owner"
    assert meter_delete_denial("tenant", "pending", 0) == "no_access"
    assert meter_delete_denial(None, None, 0) == "no_access"


# --- Удалённый счётчик нигде не виден ---

async def test_deleted_meter_hidden_everywhere(chat, api, repo, deps, user, cold):
    await repo.delete_meter(user[0], cold)
    await chat.text("меню")
    assert api.last_text().startswith(MT.NO_METERS)  # дашборд
    await chat.photo()  # подача: счётчиков нет — сразу к типу нового
    assert (await state(repo)).state == S.SUB_NEW_TYPE
    assert await repo.user_meters(user[0]) == [] and await repo.address_meters(user[1]) == []
    assert await repo.find_meter_by_serial(user[1], "18-123456") is None
    assert await repo.arshin_refresh_candidates(NOW, 10) == []
    # Уведомления: окно подачи открылось — напоминать не о чем.
    at = datetime(2026, 11, 15, 12, 0, tzinfo=clock.TZ)
    clock.set_now(at)
    for b in await repo.unpaid_bills(user[0]):
        await repo.set_bill_status(b["id"], "paid")
    api.clear()
    await notify_tick(deps, at)
    assert api.named("send") == []


async def test_deleted_during_submission(chat, api, repo, deps, user, cold):
    deps.recognizer = FixedRecognizer({"t1": 119_000})
    await chat.photo()
    await chat.press(LABEL)
    assert (await state(repo)).state == S.SUB_REVIEW
    await repo.delete_meter(user[0], cold)  # например, из мини-приложения
    await chat.press(TS.BTN_SEND)
    assert api.last_text().startswith(T.SUBMIT_GONE)
    assert (await state(repo)).state == S.IDLE and len(await repo.history(cold)) == 1


async def test_readd_same_serial_creates_new_meter(repo, user, cold):
    """Тот же номер по адресу после удаления — новый счётчик (тип и тарифы — как указали сейчас),
    старый остаётся удалённым вместе со своей историей."""
    await repo.delete_meter(user[0], cold)
    res = await submit_reading(repo, user_id=user[0], meter_id=None,
                               draft={"address_id": user[1], "type": "hot_water", "tariffs": 1},
                               values={"t1": "5,5"}, source="photo", recognized={"serial": "18-123456"},
                               today=date(2026, 10, 19))
    assert res.ok and res.meter_id != cold
    new = await repo.get_meter(res.meter_id)
    assert (new["type"], new["serial"], new["active"]) == ("hot_water", "18-123456", 1)
    assert (await repo.find_meter_by_serial(user[1], "18123456"))["id"] == res.meter_id
    assert await active(repo, cold) == 0 and len(await repo.history(cold)) == 1


async def test_readd_same_serial_in_chat(chat, api, repo, deps, user, cold):
    await repo.delete_meter(user[0], cold)
    deps.recognizer = FixedRecognizer({"t1": 7_000}, serial="18-123456")
    await chat.photo()
    await chat.press("Хол. вода")
    await chat.press("Арбат 47к1, кв 32")
    assert "сохраним" in api.last_text()  # номер свободен: удалённый счётчик его не держит
    await chat.press(TS.BTN_SEND)
    (m,) = await repo.address_meters(user[1])
    assert m["id"] != cold and m["serial"] == "18-123456"
    assert buttons(api)  # после «Готово» есть куда идти дальше
