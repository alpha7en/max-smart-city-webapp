"""Меню-дашборд через роутер: M1 клавиатура, M2 заглушки поверки/оплаты, M3 «Мои счётчики», M4 текст в IDLE."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app import clock
from app.bot import router as R
from app.bot.flows import menu
from app.bot.session import load_session
from app.bot.states import S
from app.bot.texts import common as C
from app.bot.texts import menu as T
from app.db import ts
from tests import fakes
from tests.conftest import NOW

UID = 5273381
ARBAT = "Арбат 47к1, кв 32"
ADDR = {"full_text": "г Москва, ул Арбат, д 47 к 1, кв 32", "region": "Москва", "locality": "Москва",
        "street": "Арбат", "house": "47", "block": "к1", "flat": "32", "status": "unverified", "source": "local"}


def labeler(rows):
    return [ARBAT, "Ленина 5, кв 1", "Мира 1"][: len(rows)]


async def make_user(repo, uid: int = UID, *, norm_key: str = "k1", now: datetime = NOW) -> tuple[dict, int]:
    """Зарегистрированный пользователь с адресом (owner/granted) и демо-счётом. → (user, address_id)."""
    u = await repo.ensure_user(uid, fakes.chat_of(uid))
    r = await repo.complete_registration(
        u["id"], full_name="Иванова Анна Сергеевна", phone="+79123456789", phone_verified=True,
        address=ADDR, norm_key=norm_key, raw_input="Арбат 47к1 кв 32", now=now, labeler=labeler)
    return await repo.get_user(uid), r["address_id"]


async def add_meter(repo, user: dict, address_id: int, type: str = "cold_water", *,
                    period: str | None = None, t1: int = 123456, verif: date | None = None,
                    source: str | None = None) -> int:
    mid = await repo.create_meter(address_id, type, created_by=user["id"],
                                  verification_due=verif.isoformat() if verif else None,
                                  verification_source=(source or "user") if verif else None)
    if period:
        rid = await repo.add_reading(mid, user["id"], period, {"t1": t1}, "manual")
        # created_at пишет SQLite по реальным часам — ставим время замороженных часов (UTC).
        await repo._exec("UPDATE readings SET created_at=? WHERE id=?", (ts(NOW), rid))
    return mid


def rows(kb: dict) -> list[list[dict]]:
    return kb["payload"]["buttons"]


def labels(kb: dict) -> list[list[str]]:
    return [[b["text"] for b in row] for row in rows(kb)]


async def state(repo, uid: int = UID) -> S:
    user = await repo.get_user(uid)
    return (await load_session(repo, user["id"])).state


@pytest.fixture
def hooks(monkeypatch):
    """Подменяет точки входа подачи: записывает вызовы."""
    called: list[str] = []
    for name in ("submission.start", "submission.add_meter"):
        async def rec(ctx, _name=name, **_):
            called.append(_name)
        monkeypatch.setitem(R.HOOKS, name, rec)
    return called


# --- M1: клавиатура меню ---

async def test_menu_keyboard_with_urgent_verification(chat, api, repo):
    user, aid = await make_user(repo)
    mid = await add_meter(repo, user, aid, verif=NOW.date() + timedelta(days=10))
    await chat.text("/start")
    text, kb = api.outgoing()[-1]
    assert text.startswith("**Запишитесь на поверку: 10 дн.**\n\nПоказания за октябрь — до 25 октября")
    assert text.endswith(T.FOOTER)
    assert labels(kb) == [["Запишитесь на поверку: 10 дн."], ["Подать показания"],
                          ["Мои счётчики", "Профиль"], [C.BTN_MINIAPP]]
    b = rows(kb)
    assert b[0][0]["payload"] == f"g|verify|{mid}" and b[1][0]["payload"] == "g|submit|"
    assert b[2][1]["payload"] == "g|profile|"
    assert b[3][0] == {"type": "open_app", "text": C.BTN_MINIAPP, "web_app": "test_bot"}
    assert await state(repo) == S.IDLE


async def test_urgent_submit_replaces_plain_submit_button(chat, api, repo):
    user, aid = await make_user(repo)
    await add_meter(repo, user, aid)
    clock.set_now(datetime(2026, 10, 23, 12, 0, tzinfo=clock.TZ))
    await chat.text("меню")
    _, kb = api.outgoing()[-1]
    assert labels(kb)[:2] == [["Подайте показания: 2 дн."], ["Мои счётчики", "Профиль"]]
    assert rows(kb)[0][0]["payload"] == "g|submit|"


async def test_open_app_username_from_get_me(chat, api, repo, deps):
    deps.bot_username = ""
    await make_user(repo)
    await chat.text("/start")
    _, kb = api.outgoing()[-1]
    assert rows(kb)[-1][0]["web_app"] == "test_bot" and deps.bot_username == "test_bot"


async def test_menu_button_sends_new_message_and_resets_scenario(chat, api, repo):
    await make_user(repo)
    user = await repo.get_user(UID)
    await repo.save_session(user["id"], S.SUB_AWAIT_PHOTO.value, {}, "abc123", NOW, NOW + timedelta(minutes=30))
    api.clear()
    await chat.payload("g|menu|")
    assert api.named("answer")[0]["message"] is None  # не затираем сообщение с кнопкой
    assert api.named("send")[0]["text"].startswith(C.SUB_CANCELLED)
    assert await state(repo) == S.IDLE


async def test_submit_button_calls_submission_hook(chat, api, repo, hooks, monkeypatch):
    # Действие g|submit принадлежит меню (заглушка S2 временно перекрывает его — фиксируем контракт).
    monkeypatch.setitem(R.GLOBAL_ACTIONS, "submit", menu.submit)
    await make_user(repo)
    await chat.payload("g|submit|")
    assert hooks == ["submission.start"]


# --- M2: честные заглушки поверки и оплаты ---

async def test_verification_and_payment_stubs(chat, api, repo):
    user, aid = await make_user(repo)
    await add_meter(repo, user, aid, verif=NOW.date() + timedelta(days=10))
    await chat.text("/start")
    api.clear()
    await chat.press("Запишитесь на поверку: 10 дн.")
    text, kb = api.outgoing()[-1]
    assert text == T.VERIFICATION_STUB and labels(kb) == [[C.BTN_MENU]]
    assert api.named("answer")[0]["message"] is None  # новым сообщением
    (bill,) = await repo.unpaid_bills(user["id"])
    await chat.payload(f"g|pay|{bill['id']}")
    text, kb = api.outgoing()[-1]
    assert text == T.PAY_STUB and "демо" in text and labels(kb) == [[C.BTN_MENU]]


# --- M3: Мои счётчики ---

async def test_my_meters_list_and_add(chat, api, repo, hooks):
    user, aid = await make_user(repo)
    await add_meter(repo, user, aid, period="2026-10", verif=date(2030, 3, 15))
    await add_meter(repo, user, aid, "electricity")
    await chat.text("/start")
    await chat.press("Мои счётчики")
    text, kb = api.outgoing()[-1]
    assert text.startswith(T.METERS_TITLE)
    assert f"Хол. вода · {ARBAT}\nПоследнее: 123,456 м³ (19.10), поверка до 15.03.2030" in text
    assert f"Свет · {ARBAT}\nПоказаний пока нет" in text
    assert labels(kb) == [["Добавить счётчик", "В меню"]]
    await chat.press("Добавить счётчик")
    assert hooks == ["submission.add_meter"]


async def test_my_meters_empty(chat, api, repo):
    await make_user(repo)
    await chat.payload("g|meters|")
    text, kb = api.outgoing()[-1]
    assert text == T.NO_METERS and labels(kb) == [["Добавить счётчик", "В меню"]]


# --- M4: текст в IDLE → меню без эха ---

async def test_free_text_in_idle_shows_menu(chat, api, repo):
    await make_user(repo)
    api.clear()
    await chat.text("вцвц")
    (text,) = api.texts()
    assert "вцвц" not in text and "принято" not in text.lower()
    assert text.startswith(T.NO_METERS) and text.endswith(T.FOOTER)
    assert await state(repo) == S.IDLE


async def test_menu_header_note(chat, api, repo, monkeypatch):
    """C13: шапка над дашбордом (её передаёт регистрация через header= или ctx.note)."""
    await make_user(repo)

    async def after_registration(ctx):
        await menu.send_menu(ctx, header="Готово, вы зарегистрированы.")

    monkeypatch.setitem(R.GLOBAL_ACTIONS, "menu", after_registration)
    await chat.payload("g|menu|")
    assert api.last_text().startswith("Готово, вы зарегистрированы.\n\n" + T.NO_METERS)
