"""Фундамент: события MAX, кнопки, регистрация через роутер, initData, poller, клиент MAX, БД."""
from __future__ import annotations

import json
import time
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

from app.bot import keyboards as K
from app.bot.ctx import Deps
from app.bot.events import parse_update
from app.bot.poller import MARKER_KEY, Poller
from app.bot.router import Router
from app.bot.texts import common as C
from app.bot.texts import menu as MT
from app.bot.texts import registration as RT
from app.config import Settings
from app.domain import meters as M
from app.integrations.max_api import MaxApi, MaxApiError
from app.integrations.recognizer import StubRecognizer
from app.repo import Repo
from app.web.auth import InitDataError, sign_init_data, validate_init_data
from tests import fakes
from tests.conftest import TOKEN, Chat

UID = 5273381


def test_parse_callback_user_is_presser_not_bot():
    ev = parse_update(fakes.message_callback(UID, "abc123|yes|", mid="mid.bot.7", callback_id="cb1"))
    assert ev.kind == "callback"
    assert ev.user_id == UID != fakes.BOT_ID
    assert (ev.callback_id, ev.payload, ev.message_mid, ev.update_id) == ("cb1", "abc123|yes|", "mid.bot.7", "cb1")


def test_payload_codec():
    s = K.encode("a1b2c3", "pick", 42)
    assert s == "a1b2c3|pick|42"
    assert K.decode(s) == K.Payload("a1b2c3", "pick", "42")
    assert K.decode("g|menu|") == K.Payload("g", "menu", "")
    assert K.decode("junk") is None and K.decode(None) is None
    with pytest.raises(ValueError):
        K.encode("a1b2c3", "x", "й" * 40)  # > 64 байт


def test_buttons_validated_not_truncated():
    long_text = "Хол. вода · Арбат 47к1, кв 32 (дом 2)"  # 37 символов — допустимо, не режем
    assert K.callback(long_text, "f", "a")["text"] == long_text
    with pytest.raises(ValueError):
        K.callback("x" * 129, "f", "a")
    with pytest.raises(ValueError):
        K.link("Открыть", "http://localhost:8080/app")
    with pytest.raises(ValueError):
        K.link("Открыть", "https://192.168.1.5/")
    assert K.link("Сайт", "https://max.ru")["url"] == "https://max.ru"
    with pytest.raises(ValueError):
        K.open_app("Мини-приложение", "https://maxsmartcity.ru/")
    assert K.open_app("Мини-приложение", "") is None
    assert K.open_app("Счётчик", "test_bot", "meter_12")["payload"] == "meter_12"
    board = K.kb([K.gbtn("В меню", "menu"), None], [], None)
    assert board["payload"]["buttons"] == [[{"type": "callback", "text": "В меню", "payload": "g|menu|"}]]
    with pytest.raises(ValueError):
        K.kb([K.link(f"L{i}", "https://max.ru") for i in range(4)])


async def test_registration_with_contact_to_menu(chat, api, repo):
    await chat.text("привет")
    await chat.text("иванова анна")
    assert "Анна" in api.last_text()
    await chat.feed(fakes.message_created(UID, None, [fakes.contact(UID)]))
    await chat.text("Москва, Арбат 47к1, кв. 32")
    await chat.press(RT.BTN_YES)
    await chat.press(RT.BTN_ALL_OK)
    user = await repo.get_user(UID)
    assert user["registered_at"] and user["phone"] == "+79123456789" and user["phone_verified"] == 1
    assert api.last_text().startswith(RT.DONE) and api.last_text().endswith(f"{MT.FOOTER}\n\n> {MT.BILL_NOTE}")


async def test_foreign_contact_rejected(chat, api):
    await chat.text("привет")
    await chat.text("Иванова Анна")
    await chat.feed(fakes.message_created(UID, None, [fakes.contact(999)]))
    assert api.last_text() == RT.NOT_YOUR_CONTACT


async def test_stale_button(chat, api):
    await chat.text("привет")
    await chat.text("Иванова Анна")
    api.clear()
    await chat.payload("zzzzzz|back|", mid="mid.old")
    assert api.named("answer")[0]["notification"] == C.STALE_BUTTON
    assert api.named("edit") == [{"mid": "mid.old", "text": None, "keyboard": None}]
    assert api.named("send")[0]["text"].startswith("Приятно познакомиться")  # шаг заново, новым сообщением


async def register(chat: Chat) -> None:
    await chat.text("привет")
    await chat.text("Иванова Анна")
    await chat.text("+7 912 345-67-89")
    await chat.text("Москва, Арбат 47к1, кв. 32")
    await chat.press(RT.BTN_YES)
    await chat.press(RT.BTN_ALL_OK)


async def test_duplicate_update_ignored(chat, api):
    upd = fakes.message_created(UID, "привет", mid="mid.same")
    await chat.feed(upd)
    n = len(api.calls)
    await chat.feed(upd)
    assert len(api.calls) == n


async def test_restart_keeps_state(settings, api):
    """Две инстанции репозитория на одном файле БД: состояние переживает «рестарт»."""
    repo1 = await Repo.open(settings.db_path)
    c1 = Chat(Router(Deps(api, repo1, settings, StubRecognizer(), bot_username="test_bot")), api)
    await c1.text("привет")
    await c1.text("Иванова Анна")
    await repo1.close()

    repo2 = await Repo.open(settings.db_path)
    c2 = Chat(Router(Deps(api, repo2, settings, StubRecognizer(), bot_username="test_bot")), api)
    api.clear()
    await c2.text("89123456789")
    await c2.text("Москва, Арбат 47к1, кв. 32")
    await c2.press(RT.BTN_YES)
    await c2.press(RT.BTN_ALL_OK)
    user = await repo2.get_user(UID)
    assert user["registered_at"] and user["full_name"] == "Иванова Анна"
    await repo2.close()


@pytest.mark.parametrize("text,mtype,value", [
    ("123,456", "cold_water", 123456), (" 123.4 ", "cold_water", 123400), ("00012", "gas", 12000),
    ("12345,67", "electricity", 12345670), ("0", "heat", 0),
])
def test_parse_value(text, mtype, value):
    assert M.parse_value(text, mtype) == value


def _init_data(uid: int = UID, auth_date: int | None = None) -> str:
    fields = {"auth_date": str(auth_date or int(time.time())), "query_id": "q1",
              "user": json.dumps({"id": uid, "first_name": "Анна", "last_name": "Иванова"}, ensure_ascii=False),
              "start_param": "meter_12"}
    return sign_init_data(fields, TOKEN)


def test_init_data_valid_invalid_expired():
    raw = _init_data()
    data = validate_init_data(raw, TOKEN)
    assert data.max_user_id == UID and data.start_param == "meter_12" and data.user["first_name"] == "Анна"
    assert validate_init_data(quote(raw), TOKEN).max_user_id == UID  # дважды закодированная строка
    with pytest.raises(InitDataError):
        validate_init_data(raw, "other-token")
    with pytest.raises(InitDataError):
        validate_init_data(raw.replace("q1", "q2"), TOKEN)
    with pytest.raises(InitDataError):
        validate_init_data(_init_data(auth_date=int(time.time()) - 25 * 3600), TOKEN)


async def test_poller_marker(router, api, repo):
    api.update_batches = [{"updates": [fakes.message_created(UID, "привет")], "marker": 5}]
    poller = Poller(api, repo, router)
    await poller.load_marker()
    assert await poller.poll_once() == 1
    await poller.drain()
    assert await repo.kv_get(MARKER_KEY) == "5"
    assert api.texts() == [C.WELCOME, RT.ASK_NAME]
    await poller.poll_once()
    assert api.named("get_updates")[-1] == {"marker": 5, "timeout": 25}
    restarted = Poller(api, repo, router)
    await restarted.load_marker()
    assert restarted.marker == 5


async def test_max_api_client():
    seen: list[httpx.Request] = []
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/answers":
            return httpx.Response(200, json={"success": True})
        if request.url.path == "/messages" and request.method == "POST":
            state["n"] += 1
            if state["n"] == 1:
                return httpx.Response(429, json={"code": "too.many.requests", "message": "slow down"})
            return httpx.Response(200, json={"message": {"body": {"mid": "mid.1"}}})
        return httpx.Response(400, json={"code": "proto.payload", "message": "bad"})

    async def no_sleep(_):
        return None

    api = MaxApi("TKN", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), sleep=no_sleep)
    msg = await api.send("hi", user_id=1, keyboard=K.kb([K.gbtn("В меню", "menu")]))
    assert msg["body"]["mid"] == "mid.1" and state["n"] == 2
    assert seen[-1].headers["Authorization"] == "TKN" and seen[-1].url.params["user_id"] == "1"
    await api.answer("cb 1", notification="ок")
    assert seen[-1].url.params["callback_id"] == "cb 1" and b"callback_id" not in seen[-1].content
    with pytest.raises(MaxApiError) as e:
        await api.delete("mid.1")
    assert (e.value.status, e.value.code) == (400, "proto.payload")
    await api.close()


ADDR = {"full_text": "г Москва, ул Арбат, д 47, кв 32", "region": "Москва", "locality": "Москва",
        "street": "Арбат", "house": "47", "block": None, "flat": "32", "status": "unverified", "source": "local"}


async def test_repo_transaction_rollback(repo):
    u = await repo.ensure_user(1)
    with pytest.raises(RuntimeError):
        async with repo.tx():
            await repo.update_user(u["id"], full_name="Тест Тестов")
            raise RuntimeError
    assert (await repo.get_user(1))["full_name"] is None


def test_health_without_bot_token(tmp_path):
    from app.main import create_app

    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        assert client.get("/api/health").json() == {"ok": True}
        assert client.get("/", follow_redirects=False).headers["location"] == "/app/"
