"""Фундамент: события MAX, кнопки, роутер, сессии, домен, initData, poller, клиент MAX, БД."""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

from app.bot import keyboards as K
from app.bot.ctx import Deps
from app.bot.events import parse_update
from app.bot.poller import MARKER_KEY, Poller
from app.bot.router import STATE_HANDLERS, Router
from app.bot.session import load_session
from app.bot.states import S
from app.bot.texts import common as C
from app.bot.texts import menu as MT
from app.bot.texts import registration as RT
from app.config import Settings
from app.domain import meters as M
from app.domain.people import normalize_phone, validate_name
from app.integrations.max_api import MaxApi, MaxApiError
from app.integrations.recognizer import StubRecognizer
from app.repo import ReadingExists, Repo
from app.web.auth import InitDataError, sign_init_data, validate_init_data
from tests import fakes
from tests.conftest import TOKEN, Chat

UID = 5273381


# --- События MAX ---

def test_parse_text_and_start():
    ev = parse_update(fakes.message_created(UID, "Привет"))
    assert (ev.kind, ev.user_id, ev.chat_id, ev.text) == ("text", UID, fakes.chat_of(UID), "Привет")
    assert ev.update_id.startswith("mid.")
    ev = parse_update(fakes.message_created(UID, "/start meter_12"))
    assert (ev.kind, ev.start_payload) == ("start", "meter_12")
    ev = parse_update(fakes.bot_started(UID, "ref"))
    assert (ev.kind, ev.user_id, ev.chat_id, ev.start_payload) == ("start", UID, fakes.chat_of(UID), "ref")


def test_parse_callback_user_is_presser_not_bot():
    ev = parse_update(fakes.message_callback(UID, "abc123|yes|", mid="mid.bot.7", callback_id="cb1"))
    assert ev.kind == "callback"
    assert ev.user_id == UID != fakes.BOT_ID
    assert (ev.callback_id, ev.payload, ev.message_mid, ev.update_id) == ("cb1", "abc123|yes|", "mid.bot.7", "cb1")


def test_parse_attachments():
    ev = parse_update(fakes.message_created(UID, "подпись", [fakes.image("https://x/1"), fakes.image("https://x/2")]))
    assert (ev.kind, ev.photo_url, ev.photo_count, ev.text) == ("photo", "https://x/1", 2, "подпись")
    ev = parse_update(fakes.message_created(UID, None, [fakes.file("https://x/f", "IMG_1.JPG")]))
    assert (ev.kind, ev.photo_url) == ("photo", "https://x/f")
    ev = parse_update(fakes.message_created(UID, None, [fakes.file("https://x/f", "doc.pdf")]))
    assert ev.kind == "other"
    ev = parse_update(fakes.message_created(UID, None, [fakes.contact(UID)]))
    assert (ev.kind, ev.contact_phone, ev.contact_owner_id) == ("contact", "+79123456789", UID)
    ev = parse_update(fakes.message_created(UID, None, [fakes.location(55.7, 37.6)]))
    assert (ev.kind, ev.latitude, ev.longitude) == ("location", 55.7, 37.6)
    assert parse_update(fakes.message_created(UID, None, [fakes.sticker()])).kind == "other"


def test_parse_ignores_groups_bots_and_unknown():
    assert parse_update(fakes.message_created(UID, "hi", chat_type="chat")) is None
    upd = fakes.message_created(UID, "hi")
    upd["message"]["sender"] = fakes.BOT_USER
    assert parse_update(upd) is None
    assert parse_update({"update_type": "message_removed", "timestamp": 1}) is None


# --- Кнопки ---

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
        K.open_app("Мини-приложение", "https://alpha7en.github.io/app")
    assert K.open_app("Мини-приложение", "") is None
    assert K.open_app("Счётчик", "test_bot", "meter_12")["payload"] == "meter_12"
    board = K.kb([K.gbtn("В меню", "menu"), None], [], None)
    assert board["payload"]["buttons"] == [[{"type": "callback", "text": "В меню", "payload": "g|menu|"}]]
    with pytest.raises(ValueError):
        K.kb([K.link(f"L{i}", "https://max.ru") for i in range(4)])


# --- Роутер: сквозные правила ---

async def test_first_message_welcome_and_registration(chat, api, repo):
    await chat.text("привет")
    assert api.texts() == [C.WELCOME, RT.ASK_NAME]
    user = await repo.get_user(UID)
    s = await load_session(repo, user["id"])
    assert s.state == S.REG_NAME


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


async def test_callback_from_unregistered_is_answered(chat, api):
    await chat.payload("g|menu|")
    assert api.named("answer")[0]["message"] is None  # сначала ответ на нажатие
    assert api.texts() == [C.WELCOME, RT.ASK_NAME]


async def test_stale_button(chat, api):
    await chat.text("привет")
    await chat.text("Иванова Анна")
    api.clear()
    await chat.payload("zzzzzz|back|", mid="mid.old")
    assert api.named("answer")[0]["notification"] == C.STALE_BUTTON
    assert api.named("edit") == [{"mid": "mid.old", "text": None, "keyboard": None}]
    assert api.named("send")[0]["text"].startswith("Приятно познакомиться")  # шаг заново, новым сообщением


async def test_scenario_button_replaces_message(chat, api):
    await chat.text("привет")
    await chat.text("Иванова Анна")
    api.clear()
    await chat.press(C.BTN_BACK)
    (ans,) = api.named("answer")
    assert ans["message"]["text"] == RT.ASK_NAME_AGAIN.format(name="Иванова Анна") and not api.named("send")


async def test_start_in_registration_continues(chat, api, repo):
    await chat.text("привет")
    await chat.text("Иванова Анна")
    api.clear()
    await chat.text("/start")
    texts = api.texts()
    assert texts[0] == C.CONTINUE_REG and texts[1].startswith("Приятно познакомиться")
    await chat.press(C.BTN_RESTART)
    user = await repo.get_user(UID)
    s = await load_session(repo, user["id"])
    assert s.state == S.REG_NAME and api.last_text() == RT.ASK_NAME


async def register(chat: Chat) -> None:
    await chat.text("привет")
    await chat.text("Иванова Анна")
    await chat.text("+7 912 345-67-89")
    await chat.text("Москва, Арбат 47к1, кв. 32")
    await chat.press(RT.BTN_YES)
    await chat.press(RT.BTN_ALL_OK)


async def test_start_in_idle_and_in_submission(chat, api, repo):
    await register(chat)
    api.clear()
    await chat.text("/start")
    assert api.last_text().endswith(f"{MT.FOOTER}\n\n> {MT.BILL_NOTE}")
    await chat.press("Подать показания")
    user = await repo.get_user(UID)
    assert (await load_session(repo, user["id"])).state == S.SUB_AWAIT_PHOTO
    await chat.photo()
    photo_id = (await load_session(repo, user["id"])).data["photo_id"]
    path = (await repo.get_photo(photo_id))["path"]
    assert Path(path).exists()
    await chat.text("/start")
    assert (await load_session(repo, user["id"])).state == S.IDLE
    assert not Path(path).exists()  # фото сценария удалено
    assert api.last_text().startswith(C.SUB_CANCELLED)


async def test_global_button_in_scenario_cancels_and_sends_new(chat, api, repo):
    await register(chat)
    await chat.press("Подать показания")
    api.clear()
    await chat.press("Профиль")
    assert api.named("answer")[0]["message"] is None  # глобальная — не заменой
    # QA-6: была только инструкция к фото — терять нечего, строки «подачу отменили» нет
    assert not api.named("send")[0]["text"].startswith(C.SUB_CANCELLED)
    await chat.press("В меню")
    await chat.photo()
    api.clear()
    await chat.press("Профиль")
    assert api.named("send")[0]["text"].startswith(C.SUB_CANCELLED)  # фото уже было — сообщаем


async def test_cancel_and_unknown_command(chat, api, repo):
    await chat.text("привет")
    await chat.text("отмена")
    assert api.last_text() == C.REG_CANCEL_HINT
    await chat.press(C.BTN_CONTINUE)
    assert api.last_text() == RT.ASK_NAME
    await chat.text("/foo")
    assert api.last_text().startswith(C.UNKNOWN_COMMAND)


async def test_photo_during_registration_is_kept_and_replaced(chat, api, repo):
    await chat.photo("https://i/1")
    assert api.last_text() == C.PHOTO_SAVED  # приветствие, вопрос, затем строка про фото
    user = await repo.get_user(UID)
    first = (await load_session(repo, user["id"])).data["pending_photo_id"]
    await chat.photo("https://i/2")
    second = (await load_session(repo, user["id"])).data["pending_photo_id"]
    assert first != second and await repo.get_photo(first) is None
    row = await repo.get_photo(second)
    assert oct(Path(row["path"]).stat().st_mode & 0o777) == "0o600"


async def test_unsupported_input_repeats_step(chat, api):
    await chat.text("привет")
    await chat.feed(fakes.message_created(UID, None, [fakes.sticker()]))
    assert api.last_text() == f"{C.UNSUPPORTED}\n\n{RT.ASK_NAME}"


async def test_handler_error_keeps_state(chat, api, repo, monkeypatch):
    await chat.text("привет")

    async def boom(ctx):
        ctx.session.go(S.REG_CONFIRM)
        raise RuntimeError("boom")

    monkeypatch.setitem(STATE_HANDLERS, S.REG_NAME, boom)
    await chat.text("Иванова Анна")
    assert api.last_text() == C.ERROR
    user = await repo.get_user(UID)
    assert (await load_session(repo, user["id"])).state == S.REG_NAME
    monkeypatch.undo()
    await chat.press(C.BTN_RETRY)
    assert api.last_text() == RT.ASK_NAME


async def test_expired_submission_resets_with_note(chat, api, repo, frozen_clock):
    from app import clock

    await register(chat)
    await chat.press("Подать показания")
    clock.set_now(frozen_clock + timedelta(minutes=31))
    await chat.text("что-нибудь")
    assert api.last_text().startswith(C.SUB_EXPIRED)
    user = await repo.get_user(UID)
    assert (await load_session(repo, user["id"])).state == S.IDLE


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


# --- Домен ---

@pytest.mark.parametrize("text,mtype,value", [
    ("123,456", "cold_water", 123456), (" 123.4 ", "cold_water", 123400), ("00012", "gas", 12000),
    ("12345,67", "electricity", 12345670), ("0", "heat", 0),
])
def test_parse_value(text, mtype, value):
    assert M.parse_value(text, mtype) == value


@pytest.mark.parametrize("text,code", [
    ("", "empty"), ("12,3,4", "format"), ("-1", "format"), ("1 234", "format"), ("abc", "format"),
    ("123456,1", "too_many_digits"), ("1,2345", "too_many_decimals"),
])
def test_parse_value_errors(text, code):
    with pytest.raises(M.ValueParseError) as e:
        M.parse_value(text, "cold_water")
    assert e.value.code == code


def test_format_value():
    assert M.format_value(123456, "cold_water") == "123,456"
    assert M.format_value(123456, "cold_water", unit=True) == "123,456 м³"
    assert M.format_value(12345670, "electricity") == "12345,67"
    assert M.format_value(5256) == "5,256"
    assert M.format_value(-5256) == "-5,256"


def test_plausibility_and_periods():
    prev = {"t1": 100_000}
    assert M.check_plausibility("cold_water", {"t1": 99_000}, prev) == "less"
    assert M.check_plausibility("cold_water", {"t1": 131_000}, prev) == "too_big"
    assert M.check_plausibility("cold_water", {"t1": 131_000}, prev, months=2) == "ok"
    assert M.check_plausibility("electricity", {"t1": 900_000, "t2": 900_000}, {"t1": 0, "t2": 0}) == "too_big"
    assert M.months_between("2025-11", "2026-02") == 3
    assert M.shift_period("2026-01", -1) == "2025-12"
    w = M.submission_window(date(2026, 10, 19))
    assert w.is_open and w.days_left == 6 and w.end == date(2026, 10, 25)
    w = M.submission_window(date(2026, 12, 28))
    assert not w.is_open and w.start == date(2027, 1, 15)
    period, amount, due = M.demo_bill(7, date(2026, 10, 19))
    assert period == "2026-09" and 200_000 <= amount <= 600_000 and due == date(2026, 11, 10)


def test_people():
    assert validate_name("иванова  анна-мария сергеевна").value == "Иванова Анна-Мария Сергеевна"
    assert validate_name("Ivanova Anna").error == "latin"
    assert validate_name("Анна").error == "too_few"
    assert normalize_phone("8 (912) 345-67-89") == "+79123456789"
    assert normalize_phone("9123456789") == "+79123456789"
    assert normalize_phone("+1 212 555 0100") is None


# --- initData ---

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


# --- Poller ---

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


# --- Клиент MAX ---

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
    # safe_delete suppresses MaxApiError
    assert await api.safe_delete("mid.1") is False
    assert await api.safe_delete("") is False
    assert await api.safe_edit("mid.1", "hello") is False
    assert await api.safe_edit("", "hello") is False
    assert await api.delete_messages(["", "mid.1"]) == []
    await api.close()


async def test_max_api_delete_and_edit_success():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/messages":
            if request.method in ("DELETE", "PUT"):
                return httpx.Response(200, json={"success": True})
        return httpx.Response(400, json={"code": "bad"})

    api = MaxApi("TKN", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await api.safe_delete("mid.ok") is True
    assert seen[-1].method == "DELETE" and seen[-1].url.params["message_id"] == "mid.ok"

    assert await api.safe_edit("mid.ok", "new text") is True
    assert seen[-1].method == "PUT" and seen[-1].url.params["message_id"] == "mid.ok"

    deleted = await api.delete_messages(["mid.1", "mid.2"])
    assert deleted == ["mid.1", "mid.2"]
    await api.close()


# --- Репозиторий ---

def _labeler(rows):
    return [f"Адрес {i + 1}" for i in range(len(rows))]


ADDR = {"full_text": "г Москва, ул Арбат, д 47, кв 32", "region": "Москва", "locality": "Москва",
        "street": "Арбат", "house": "47", "block": None, "flat": "32", "status": "unverified", "source": "local"}


async def test_repo_registration_access_model_and_readings(repo, frozen_clock):
    a = await repo.ensure_user(1, 11)
    b = await repo.ensure_user(2, 22)
    ra = await repo.complete_registration(a["id"], full_name="Иванова Анна", phone="+79123456789",
                                          phone_verified=True, address=ADDR, norm_key="k1", raw_input="Арбат 47-32",
                                          now=frozen_clock, labeler=_labeler)
    assert (ra["role"], ra["access"], ra["label"]) == ("owner", "granted", "Адрес 1")
    rb = await repo.add_user_address(b["id"], ADDR, "k1", None, frozen_clock.date(), labeler=_labeler)
    assert (rb["address_id"], rb["role"], rb["access"]) == (ra["address_id"], "tenant", "pending")
    assert (await repo.address_owner(ra["address_id"]))["id"] == a["id"]
    assert len(await repo.unpaid_bills(a["id"])) == 1 and await repo.unpaid_bills(b["id"]) == []

    meter_id, _ = await repo.submit_reading(
        user_id=a["id"], period="2026-10", values={"t1": 100_000}, source="manual",
        draft={"address_id": ra["address_id"], "type": "cold_water", "serial": "18-123 456"})
    assert (await repo.find_meter_by_serial(ra["address_id"], "18123456"))["id"] == meter_id
    with pytest.raises(ReadingExists):
        await repo.add_reading(meter_id, a["id"], "2026-10", {"t1": 101_000}, "manual")
    await repo.add_reading(meter_id, a["id"], "2026-10", {"t1": 101_000}, "photo", replace=True)
    assert [r["t1"] for r in await repo.history(meter_id)] == [101_000]
    (m,) = await repo.user_meters(a["id"])
    assert m["last_t1"] == 101_000 and m["address_label"] == "Адрес 1"
    assert await repo.user_meters(b["id"]) == []  # pending — счётчики не видны

    assert await repo.try_mark_sent(a["id"], "deadline", "2026-10", frozen_clock) is True
    assert await repo.try_mark_sent(a["id"], "deadline", "2026-10", frozen_clock) is False

    await repo.delete_user_data(a["id"])
    assert await repo.get_user(1) is None
    assert (await repo.history(meter_id))[0]["user_id"] is None  # показания остались за адресом


async def test_repo_transaction_rollback(repo):
    u = await repo.ensure_user(1)
    with pytest.raises(RuntimeError):
        async with repo.tx():
            await repo.update_user(u["id"], full_name="Тест Тестов")
            raise RuntimeError
    assert (await repo.get_user(1))["full_name"] is None


# --- Веб ---

def test_health_without_bot_token(tmp_path):
    from app.main import create_app

    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        assert client.get("/api/health").json() == {"ok": True}
        assert client.get("/", follow_redirects=False).headers["location"] == "/app/"
