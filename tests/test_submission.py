"""Сценарии подачи показаний (SPEC §5.6, SPEC_REVIEW: F1–F28) через настоящий роутер и FakeMaxApi."""
from __future__ import annotations

import asyncio
import itertools
import json
from datetime import timedelta
from pathlib import Path

import pytest

from app import clock
from app.bot import photos
from app.bot import router as R
from app.bot.ctx import Deps
from app.bot.flows import submission as flow
from app.bot.router import Router, call_hook
from app.bot.session import load_session, save_session
from app.bot.states import S
from app.bot.texts import common as C
from app.bot.texts import submission as T
from app.domain.addresses import AddressCandidate, norm_key
from app.integrations.address_service import AddressService
from app.integrations.recognizer import Recognition, StubRecognizer
from app.repo import Repo
from tests import fakes
from tests.conftest import NOW, Chat

UID, OTHER = 5273381, 777
ARBAT = "Москва, Арбат 47к1, кв. 32"
LABEL = "Хол. вода · Арбат 47к1, кв 32"


# --- Фикстуры и помощники ---

@pytest.fixture(autouse=True)
async def no_orphan_photos(repo, settings):
    """После каждого теста: файлы фото есть только у фото, на которые ссылается живая сессия."""
    yield
    rows = await repo._all("SELECT id, path FROM photos")
    live = set()
    for s in await repo._all("SELECT data FROM sessions"):
        d = json.loads(s["data"])
        live |= {d.get("photo_id"), d.get("pending_photo_id")}
    files = {str(p) for p in settings.photos_dir.glob("*")} if settings.photos_dir.exists() else set()
    assert files == {r["path"] for r in rows if r["id"] in live}, "orphan photo files"


@pytest.fixture
def no_access(monkeypatch) -> list:
    """Подменяет точку входа S1 «нет прав»: запоминает address_id."""
    calls: list = []

    async def fake(ctx, address_id=None, **_):
        calls.append(address_id)
        await ctx.reply("NO_ACCESS")

    monkeypatch.setitem(R.HOOKS, "access.no_access", fake)
    return calls


@pytest.fixture
def hook_button(monkeypatch):
    """Глобальная кнопка g|t_<hook>|, вызывающая точку входа потока (как это делают меню/регистрация)."""
    def install(name: str, **kw) -> str:
        async def handler(ctx):
            await call_hook(name, ctx, **kw)
        action = "t_" + name.split(".")[1]
        monkeypatch.setitem(R.GLOBAL_ACTIONS, action, handler)
        return f"g|{action}|"
    return install


class FixedRecognizer:
    """Распознаватель с заданным ответом (или исключением / задержкой)."""

    def __init__(self, values=None, serial=None, confidence=0.95, stub=False, exc=None, delay=0.0,
                 issues=(), note=None, brand=None, model=None):
        self.values, self.serial, self.confidence, self.stub = values or {}, serial, confidence, stub
        self.exc, self.delay, self.calls = exc, delay, []
        self.issues, self.note = list(issues), note
        self.brand, self.model = brand, model

    async def recognize(self, image_path, meter_type, tariffs, *, hint=None):
        self.calls.append((meter_type, tariffs, hint))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        vals = {f: self.values.get(f) for f in ("t1", "t2", "t3")}
        return Recognition(vals, self.serial, self.confidence, self.stub, issues=self.issues, note=self.note,
                           brand=self.brand, model=self.model)


def cand(text: str) -> AddressCandidate:
    return AddressService(None).parse_local(text)[0]


async def register(repo, uid: int = UID, address: str = ARBAT) -> tuple[int, int]:
    """Зарегистрированный пользователь с адресом (минуя поток регистрации). → (user_id, address_id)."""
    u = await repo.ensure_user(uid, fakes.chat_of(uid))
    c = cand(address)
    r = await repo.complete_registration(u["id"], full_name="Иванова Анна", phone="+79123456789",
                                         phone_verified=True, address=c.to_dict(), norm_key=norm_key(c),
                                         raw_input=address, now=NOW)
    return u["id"], r["address_id"]


_SERIALS = itertools.count(10_000_001)
AUTO = object()  # у счётчика есть номер (без номера бот спросит его перед отправкой)


async def meter(repo, user, mtype="cold_water", tariffs=1, serial=AUTO, readings=()) -> int:
    uid, aid = user
    serial = str(next(_SERIALS)) if serial is AUTO else serial
    mid = await repo.create_meter(aid, mtype, tariffs, serial, uid)
    for period, vals in readings:
        vals = vals if isinstance(vals, dict) else {"t1": vals}
        await repo.add_reading(mid, uid, period, vals, "manual")
    return mid


async def state(repo, uid: int = UID):
    return await load_session(repo, (await repo.get_user(uid))["id"])


def buttons(api) -> list[str]:
    """Подписи кнопок последнего сообщения."""
    kb = api.outgoing()[-1][1] or {}
    return [b["text"] for row in kb.get("payload", {}).get("buttons", []) for b in row]


def photo_files(settings) -> list[Path]:
    return list(settings.photos_dir.glob("*")) if settings.photos_dir.exists() else []


async def readings(repo, mid: int) -> list[dict]:
    return await repo._all("SELECT t1, t2, t3, source, status FROM readings WHERE meter_id=? ORDER BY id", (mid,))


async def photo_with_caption(chat: Chat, caption: str, url: str = "https://i.oneme.ru/i?r=cap") -> None:
    chat.api.files[url] = b"\xff\xd8caption"
    await chat.feed(fakes.message_created(chat.uid, caption, [fakes.image(url)]))


@pytest.fixture
async def user(repo):
    return await register(repo)


@pytest.fixture
async def cold(repo, user):
    """Счётчик холодной воды с показанием за сентябрь 118,200."""
    return await meter(repo, user, readings=[("2026-09", 118_200)])


async def give_serial(chat: Chat, serial: str = "18-765432") -> None:
    """«Не разобрали серийный номер» → [Ввести номер] → номер."""
    assert T.SERIAL_MISSING in chat.api.last_text()
    await chat.press(T.BTN_SERIAL)
    await chat.text(serial)


async def to_review(chat: Chat, label: str = LABEL, url: str = "https://i.oneme.ru/i?r=photo1") -> None:
    await chat.photo(url)
    await chat.press(label)


# --- F1–F3: основной путь ---

async def test_f1_photo_without_meters_goes_to_new_type(chat, api, repo, settings, user):
    await chat.photo()
    assert api.last_text().startswith(T.PHOTO_RECEIVED) and T.NO_METERS_PHOTO in api.last_text()
    assert (await state(repo)).state == S.SUB_NEW_TYPE
    assert buttons(api) == ["Хол. вода", "Гор. вода", "Свет", "Газ", "Тепло", C.BTN_CANCEL]
    (f,) = photo_files(settings)
    assert f.read_bytes() == b"\xff\xd8jpeg"


async def test_f2_pick_shows_only_granted_meters(chat, api, repo, user, cold):
    owner = await register(repo, OTHER, "Москва, Тверская 1, кв 5")
    await meter(repo, owner, "gas")
    c = cand("Москва, Тверская 1, кв 5")
    await repo.add_user_address(user[0], c.to_dict(), norm_key(c), None, NOW.date())  # tenant/pending
    await chat.photo()
    assert api.last_text() == f"{T.PHOTO_RECEIVED}\n\n{T.PICK_PHOTO}"
    assert buttons(api) == [LABEL, T.BTN_NEW_METER, C.BTN_CANCEL]
    assert (await state(repo)).state == S.SUB_PICK_METER


async def test_f3_photo_recognize_send(chat, api, repo, settings, user, cold):
    await chat.photo()
    api.clear()
    await chat.press(LABEL)
    assert api.named("typing") and api.texts()[0] == T.LOOKING
    assert api.named("delete")  # «Смотрим на фото…» удалено при получении результата
    review = api.last_text()
    assert review.startswith(LABEL + "\n\nПоказание: **") and "В прошлый раз: 118,2 м³ (+" in review
    assert T.STUB_NOTE in review and review.endswith(T.REVIEW_QUESTION)
    assert buttons(api) == [T.BTN_SEND, T.BTN_EDIT, T.BTN_RETAKE, C.BTN_CANCEL]
    assert (await state(repo)).state == S.SUB_REVIEW
    await chat.press(T.BTN_SEND)
    (_, row) = await readings(repo, cold)
    assert (row["source"], row["status"]) == ("photo", "accepted") and row["t1"] > 118_200
    done = api.last_text()
    assert done.startswith("Готово! Записали показание счётчика холодной воды (Арбат 47к1, кв 32) за октябрь: ")
    assert T.UK_MOCK in done and T.STUB_NOTE not in done  # о демо-распознавании сказали на проверке
    assert buttons(api) == [C.BTN_MENU, T.BTN_MORE]
    assert api.button(T.BTN_MORE)["payload"] == "g|submit|"
    assert (await state(repo)).state == S.IDLE and photo_files(settings) == []


async def test_start_hook_instruction_keyboard(chat, api, repo, user):
    await chat.payload("g|submit|")
    assert api.last_text() == T.INSTRUCTION
    kb = api.outgoing()[-1][1]["payload"]["buttons"]
    assert [[b["text"] for b in row] for row in kb] == [[T.BTN_MANUAL, C.BTN_MINIAPP], [C.BTN_MENU]]
    assert kb[0][1] == {"type": "open_app", "text": C.BTN_MINIAPP, "web_app": "test_bot"}
    assert (await state(repo)).state == S.SUB_AWAIT_PHOTO
    await chat.press(C.BTN_MENU)
    assert (await state(repo)).state == S.IDLE


# --- F4–F5: новый счётчик и адрес ---

async def test_f4_new_electricity_two_tariffs(chat, api, repo, user):
    await chat.photo()
    await chat.press("Свет")
    assert (await state(repo)).state == S.SUB_NEW_TARIFF
    await chat.press("День-ночь")
    assert api.last_text() == T.ASK_ADDRESS
    assert buttons(api) == ["Арбат 47к1, кв 32", T.BTN_OTHER_ADDRESS, C.BTN_BACK, C.BTN_CANCEL]
    await chat.press("Арбат 47к1, кв 32")
    # Демо-распознавание номер не читает: без номера к «Отправить» не пускаем.
    assert api.last_text() == f"Свет · Арбат 47к1, кв 32\n\n{T.SERIAL_MISSING}"
    assert buttons(api) == [T.BTN_RETAKE, T.BTN_SERIAL, C.BTN_CANCEL]
    assert (await state(repo)).state == S.SUB_SERIAL_MISSING
    await chat.press(T.BTN_SERIAL)
    assert T.ASK_SERIAL.format(example="01234567") in api.last_text()
    await chat.text("2021")  # год — не номер
    assert api.last_text().startswith("Это не похоже на серийный номер")
    assert (await state(repo)).state == S.SUB_SERIAL_INPUT
    await chat.text("0112 3456 7890")
    review = api.last_text()
    assert "Т1 день: **" in review and "Т2 ночь: **" in review
    assert "Серийный номер: 011234567890 — сохраним" in review
    assert await repo.address_meters(user[1]) == []  # черновик: в БД ещё нет
    await chat.press(T.BTN_SEND)
    (m,) = await repo.address_meters(user[1])
    assert (m["type"], m["tariffs"], m["created_by"]) == ("electricity", 2, user[0])
    assert m["serial"] == "0112 3456 7890"
    (r,) = await readings(repo, m["id"])
    assert r["t1"] and r["t2"] and r["t3"] is None
    assert api.last_text().endswith(T.ASK_VERIF) and buttons(api) == [T.BTN_LATER]
    assert (await state(repo)).state == S.SUB_VERIF_DATE


async def test_f5_other_address_of_another_owner_no_access(chat, api, repo, settings, no_access):
    await register(repo, OTHER)  # собственник Арбата
    await register(repo, UID, "Казань, Баумана 12, кв 3")
    await chat.photo()
    await chat.press("Газ")
    await chat.press(T.BTN_OTHER_ADDRESS)
    assert api.last_text() == T.ASK_NEW_ADDRESS
    await chat.text(ARBAT)
    assert api.last_text().startswith("Мы поняли так:") and T.ADDRESS_LOCAL_NOTE.strip() in api.last_text()
    await chat.press(T.BTN_YES)
    arbat = await repo.find_address(norm_key(cand(ARBAT)))
    assert no_access == [arbat["id"]] and api.last_text() == "NO_ACCESS"
    link = await repo.user_address((await repo.get_user(UID))["id"], arbat["id"])
    assert (link["role"], link["access"]) == ("tenant", "pending")
    assert await repo.address_meters(arbat["id"]) == [] and photo_files(settings) == []
    assert (await state(repo)).state == S.IDLE


async def test_new_address_without_flat_and_new_search(chat, api, repo, user):
    await chat.photo()
    await chat.press("Газ")
    await chat.press(T.BTN_OTHER_ADDRESS)
    await chat.text("Москва, Ленина")
    assert api.last_text() == T.ADDRESS_NOT_FOUND and (await state(repo)).state == S.SUB_ADDR_INPUT
    await chat.text("Москва, Тверская 2, кв 7")
    await chat.text("Москва, Тверская 1")  # B9: текст на шаге выбора — новый поиск
    assert "Тверская, д. 1" in api.last_text()
    await chat.press(T.BTN_YES)
    assert api.last_text() == T.ASK_FLAT.format(address="г. Москва, Тверская, д. 1")
    await chat.text("кв. сорок")
    assert api.last_text() == T.FLAT_ERROR
    await chat.text("кв. 5")
    assert (await state(repo)).state == S.SUB_SERIAL_MISSING  # новый счётчик: номер обязателен
    rows = await repo.user_addresses(user[0])
    assert [r["flat"] for r in rows] == ["32", "5"] and all(r["access"] == "granted" for r in rows)


class FakeAddresses:
    def __init__(self, cands, local=()):
        self.cands, self.local = cands, list(local)

    async def suggest(self, text):
        return list(self.cands)

    def parse_local(self, text):
        return list(self.local)


async def test_several_candidates_private_house_and_save_as_is(chat, api, repo, deps, user):
    a = [AddressCandidate(f"г Москва, ул Садовая, д {n}", "Москва", "г Москва", "ул Садовая", str(n), None, None,
                          status="verified_house", source="dadata") for n in (1, 3)]
    deps.addresses = FakeAddresses(a)
    await chat.photo()
    await chat.press("Газ")
    await chat.press(T.BTN_OTHER_ADDRESS)
    await chat.text("Садовая")
    assert api.last_text() == T.ADDRESS_MANY
    assert buttons(api)[-2:] == [T.BTN_NOT_MINE, C.BTN_BACK] and len(buttons(api)) == 4
    await chat.press(buttons(api)[1])
    await chat.press(T.BTN_PRIVATE_HOUSE)
    assert (await state(repo)).state == S.SUB_SERIAL_MISSING
    assert (await repo.user_addresses(user[0]))[1]["house"] == "3"

    deps.addresses = FakeAddresses([], local=[cand("Москва, Тверская 9, кв 1")])
    await chat.press(C.BTN_CANCEL)
    await chat.photo("https://i/2")
    await chat.press("Тепло")
    await chat.press(T.BTN_OTHER_ADDRESS)
    await chat.text("Москва, Тверская 9, кв 1")
    assert api.last_text().startswith("Не нашли такой адрес в справочнике")
    await chat.press(T.BTN_SAVE_AS_IS)
    assert (await state(repo)).state == S.SUB_SERIAL_MISSING and len(await repo.user_addresses(user[0])) == 3


# --- F6: отмена везде ---

PATHS = {
    S.SUB_PICK_METER: [],
    S.SUB_NEW_TYPE: [("press", T.BTN_NEW_METER)],
    S.SUB_NEW_TARIFF: [("press", T.BTN_NEW_METER), ("press", "Свет")],
    S.SUB_NEW_ADDRESS: [("press", T.BTN_NEW_METER), ("press", "Свет"), ("press", T.TARIFF_BUTTONS[1])],
    S.SUB_ADDR_INPUT: [("press", T.BTN_NEW_METER), ("press", "Газ"), ("press", T.BTN_OTHER_ADDRESS)],
    S.SUB_ADDR_FLAT: [("press", T.BTN_NEW_METER), ("press", "Газ"), ("press", T.BTN_OTHER_ADDRESS),
                      ("text", "Москва, Тверская 1"), ("press", T.BTN_YES)],
    S.SUB_REVIEW: [("press", LABEL)],
    S.SUB_MANUAL: [("press", LABEL), ("press", T.BTN_EDIT)],
    S.SUB_AWAIT_PHOTO: [("press", LABEL), ("press", T.BTN_RETAKE)],
}


@pytest.mark.parametrize("target", list(PATHS))
async def test_f6_cancel_in_every_state(chat, api, repo, settings, cold, target):
    await chat.photo()
    for kind, arg in PATHS[target]:
        await (chat.press(arg) if kind == "press" else chat.text(arg))
    assert (await state(repo)).state == target
    await chat.press(C.BTN_CANCEL)
    assert (await state(repo)).state == S.IDLE
    assert api.last_text().startswith(C.SUB_CANCELLED_BY_USER)
    assert photo_files(settings) == [] and await repo._all("SELECT * FROM photos") == []
    assert len(await readings(repo, cold)) == 1


async def test_f6_cancel_on_address_pick_by_text(chat, api, repo, cold):
    await chat.photo()
    for step in PATHS[S.SUB_ADDR_INPUT]:
        await chat.press(step[1])
    await chat.text("Москва, Тверская 1, кв 5")
    assert (await state(repo)).state == S.SUB_ADDR_PICK
    await chat.text("отмена")
    assert (await state(repo)).state == S.IDLE


# --- F7–F10: исправление, пересъёмка, ошибки распознавания ---

async def test_f7_edit_with_errors_then_photo_edited(chat, api, repo, cold):
    await to_review(chat)
    await chat.press(T.BTN_EDIT)
    assert (await state(repo)).state == S.SUB_MANUAL and "На фото разобрали: " in api.last_text()
    for bad, needle in [("12,34,5", "Не похоже на показание"), ("123,4567", "не больше 3 цифр"),
                        ("-5", "Не похоже"), ("abc", "Не похоже"), ("1234567", "не больше 5")]:
        await chat.text(bad)
        assert needle in api.last_text() and "123,456" in api.last_text()
        assert buttons(api) == [C.BTN_BACK, C.BTN_CANCEL]
    await chat.press(C.BTN_BACK)  # C4: первое поле → проверка
    assert (await state(repo)).state == S.SUB_REVIEW and T.STUB_NOTE in api.last_text()
    await chat.press(T.BTN_EDIT)
    await chat.text("125,5")
    review = api.last_text()
    assert "Показание: **125,5 м³**" in review and T.STUB_NOTE not in review
    await chat.press(T.BTN_SEND)
    assert (await readings(repo, cold))[-1] == {"t1": 125_500, "t2": None, "t3": None,
                                                "source": "photo_edited", "status": "accepted"}
    assert api.last_text().startswith("Готово! Записали показание счётчика холодной воды")


async def test_f8_retake_then_new_photo_recognized_at_once(chat, api, repo, settings, cold):
    await to_review(chat)
    (first,) = photo_files(settings)
    await chat.press(T.BTN_RETAKE)
    assert (await state(repo)).state == S.SUB_AWAIT_PHOTO and api.last_text() == T.RETAKE
    assert photo_files(settings) == []
    api.clear()
    await chat.photo("https://i/new", b"\xff\xd8new")
    assert api.texts()[0] == f"{T.PHOTO_RECEIVED}\n\n{T.LOOKING}" and api.named("typing")
    assert (await state(repo)).state == S.SUB_REVIEW
    assert [f.read_bytes() for f in photo_files(settings)] == [b"\xff\xd8new"] and first.exists() is False


async def test_f9_recognition_error_caption(chat, api, repo, settings, cold):
    await photo_with_caption(chat, "ошибка")
    await chat.press(LABEL)
    assert api.last_text() == T.recognize_failed(["digits_not_visible"], None, "cold_water")
    assert buttons(api) == [T.BTN_RETAKE, T.BTN_MANUAL, C.BTN_CANCEL]
    assert photo_files(settings) == [] and (await state(repo)).state == S.SUB_AWAIT_PHOTO
    await chat.press(T.BTN_RETAKE)
    assert api.last_text() == T.RETAKE
    await chat.press(T.BTN_MANUAL)
    assert (await state(repo)).state == S.SUB_MANUAL
    await chat.text("120")
    await chat.press(T.BTN_SEND)
    assert (await readings(repo, cold))[-1]["source"] == "manual"


async def test_f10_low_confidence_warns(chat, api, repo, deps, cold):
    deps.recognizer = FixedRecognizer({"t1": 120_000}, confidence=0.6)
    await to_review(chat)
    assert T.CHECK_DIGITS in api.last_text() and T.STUB_NOTE not in api.last_text()
    assert "Показание: **120,000 м³**" in api.last_text()
    assert deps.recognizer.calls[0][2]["prev"] == {"t1": 118_200, "t2": None, "t3": None}


SERVICE_DOWN = T.recognize_failed(["service"], None, "cold_water")


@pytest.mark.parametrize("rec, text", [
    (FixedRecognizer(exc=RuntimeError("boom")), SERVICE_DOWN), (FixedRecognizer({"t1": 1}, confidence=0.3), None),
    (FixedRecognizer({}, confidence=0.9), None), (FixedRecognizer({"t1": 1}, delay=1), SERVICE_DOWN)])
async def test_f10_recognizer_failures_like_f9(chat, api, repo, deps, settings, cold, monkeypatch, rec, text):
    monkeypatch.setattr(flow, "RECOGNIZE_TIMEOUT", 0.05)
    deps.recognizer = rec
    await to_review(chat)
    assert api.last_text() == (text or T.RECOGNIZE_FAILED) and photo_files(settings) == []
    assert buttons(api) == [T.BTN_RETAKE, T.BTN_MANUAL, C.BTN_CANCEL]



async def test_multi_tariff_one_tariff_on_photo_rest_manual(chat, api, repo, deps, user):
    """Сервис читает с табло один тариф (здесь Т2): показываем его и спрашиваем только недостающий Т1."""
    mid = await meter(repo, user, "electricity", 2)
    deps.recognizer = FixedRecognizer({"t2": 505_500})
    await to_review(chat, "Свет · Арбат 47к1, кв 32")
    assert (await state(repo)).state == S.SUB_MANUAL
    text = api.last_text()
    assert "На фото разобрали: Т2 ночь — 505,50 кВт·ч" in text and "Т1 день: напишите показание" in text
    assert "Показание:" not in text and "(1 из 2)" not in text
    await chat.text("1010")
    review = api.last_text()
    assert "Т1 день: **1010 кВт·ч**" in review and "Т2 ночь: **505,50 кВт·ч**" in review
    await chat.press(T.BTN_SEND)
    assert (await readings(repo, mid))[-1] == {"t1": 1_010_000, "t2": 505_500, "t3": None,
                                               "source": "photo_edited", "status": "accepted"}


async def test_three_tariffs_one_recognized_asks_two_missing(chat, api, repo, deps, user):
    await meter(repo, user, "electricity", 3)
    deps.recognizer = FixedRecognizer({"t1": 100_000})
    await to_review(chat, "Свет · Арбат 47к1, кв 32")
    assert "Т1 пик — 100,00 кВт·ч" in api.last_text() and "Т2 ночь (1 из 2)" in api.last_text()
    await chat.text("200")
    assert "Т3 полупик (2 из 2)" in api.last_text()
    await chat.text("300")
    assert (await state(repo)).state == S.SUB_REVIEW and "—" not in api.last_text()


async def test_partial_back_never_shows_empty_review(chat, api, repo, deps, settings, user):
    """«Назад» с недостающего тарифа не ведёт на проверку с пустым Т1 — просим новое фото."""
    await meter(repo, user, "electricity", 2)
    deps.recognizer = FixedRecognizer({"t2": 505_500})
    await to_review(chat, "Свет · Арбат 47к1, кв 32")
    await chat.press(C.BTN_BACK)
    assert (await state(repo)).state == S.SUB_AWAIT_PHOTO and api.last_text() == T.RETAKE
    assert photo_files(settings) == []
    for text in api.texts():
        assert "**—" not in text and "Т1 день: **—" not in text


async def test_review_repeat_with_missing_value_asks_field(chat, api, repo, deps, user):
    """Проверка с пустым полем (например, старая сессия) — не показываем «Показание: —», спрашиваем поле."""
    await meter(repo, user)
    await to_review(chat)
    s = await state(repo)
    s.data["values"] = {"t1": None}
    await save_session(repo, s, NOW)
    api.clear()
    await chat.text("что-то")
    assert (await state(repo)).state == S.SUB_MANUAL
    assert "Показание:" not in api.last_text() and "Напишите показание" in api.last_text()


async def test_edit_after_partial_asks_all_fields(chat, api, repo, deps, user):
    """«Исправить» на проверке — все поля заново, с подсказкой распознанного."""
    await meter(repo, user, "electricity", 2)
    deps.recognizer = FixedRecognizer({"t2": 505_500})
    await to_review(chat, "Свет · Арбат 47к1, кв 32")
    await chat.text("1010")
    await chat.press(T.BTN_EDIT)
    assert "Т1 день (1 из 2)" in api.last_text()
    await chat.press(C.BTN_BACK)
    assert (await state(repo)).state == S.SUB_REVIEW and "Т1 день: **1010 кВт·ч**" in api.last_text()


# --- F11–F12: серийные номера ---

async def test_f11_serial_mismatch_three_ways(chat, api, repo, deps, settings, user):
    mid = await meter(repo, user, serial="18-123456")
    deps.recognizer = FixedRecognizer({"t1": 5_000}, serial="77-000001")
    await to_review(chat)
    assert (await state(repo)).state == S.SUB_SERIAL_MISMATCH
    assert "77-000001" in api.last_text() and "18-123456" in api.last_text()
    assert buttons(api) == [T.BTN_OTHER_METER, T.BTN_SAME_METER, T.BTN_RETAKE, C.BTN_CANCEL]
    await chat.press(T.BTN_OTHER_METER)
    assert (await state(repo)).state == S.SUB_PICK_METER and len(photo_files(settings)) == 1
    await chat.press(LABEL)
    await chat.press(T.BTN_RETAKE)
    assert (await state(repo)).state == S.SUB_AWAIT_PHOTO and photo_files(settings) == []
    await chat.photo("https://i/2")
    assert (await state(repo)).state == S.SUB_SERIAL_MISMATCH
    await chat.press(T.BTN_SAME_METER)
    assert "оставили сохранённый" in api.last_text()
    await chat.press(T.BTN_SEND)
    assert (await repo.get_meter(mid))["serial"] == "18-123456"


async def test_serial_match_and_saved_for_meter_without_serial(chat, api, repo, deps, user):
    await meter(repo, user, serial="18-123456")
    deps.recognizer = FixedRecognizer({"t1": 5_000}, serial="18 123456")
    await to_review(chat)
    assert "Серийный номер: 18 123456 — совпадает" in api.last_text()
    await chat.press(C.BTN_CANCEL)
    gas = await meter(repo, user, "gas", serial=None)
    deps.recognizer = FixedRecognizer({"t1": 5_000}, serial="GZ-1234567")
    await to_review(chat, "Газ · Арбат 47к1, кв 32", "https://i/3")
    assert "GZ-1234567 — сохраним" in api.last_text()
    await chat.press(T.BTN_SEND)
    assert (await repo.get_meter(gas))["serial"] == "GZ-1234567"


async def test_meter_model_saved_internally_never_shown(chat, api, repo, deps, user):
    """Модель и производитель с шильдика — в recognized_json; в сообщения не попадают, серийник отдельно."""
    cold = await meter(repo, user, serial=None, readings=[("2026-09", 118_200)])
    deps.recognizer = FixedRecognizer({"t1": 120_000}, serial="18-4521", brand="Бетар", model="СХВ-15")
    await to_review(chat)
    assert "Серийный номер: 18-4521" in api.last_text()
    await chat.press(T.BTN_SEND)
    assert not [t for t in api.texts() if "СХВ" in t or "Бетар" in t]
    row = (await repo.history(cold))[0]
    rec = json.loads(row["recognized_json"])
    assert (rec["serial"], rec["brand"], rec["model"]) == ("18-4521", "Бетар", "СХВ-15")
    assert (await repo.get_meter(cold))["serial"] == "18-4521"

async def test_f12_new_meter_serial_exists_switches(chat, api, repo, deps, user):
    mid = await meter(repo, user, serial="18-4521", readings=[("2026-09", 100_000)])
    deps.recognizer = FixedRecognizer({"t1": 104_000}, serial="184521")
    await chat.photo()
    await chat.press(T.BTN_NEW_METER)
    await chat.press("Хол. вода")
    await chat.press("Арбат 47к1, кв 32")
    assert api.last_text().startswith(T.SWITCHED_METER.format(label=LABEL))
    await chat.press(T.BTN_SEND)
    assert [m["id"] for m in await repo.address_meters(user[1])] == [mid]
    assert (await readings(repo, mid))[-1]["t1"] == 104_000
    assert buttons(api) == [C.BTN_MENU, T.BTN_MORE]  # не новый — дату поверки не спрашиваем


async def test_new_meter_serial_saved_from_photo(chat, api, repo, deps, user):
    deps.recognizer = FixedRecognizer({"t1": 7_000}, serial="HW-90123456")
    await chat.photo()
    await chat.press("Гор. вода")
    await chat.press("Арбат 47к1, кв 32")
    assert "HW-90123456 — сохраним" in api.last_text()
    await chat.press(T.BTN_SEND)
    (m,) = await repo.address_meters(user[1])
    assert (m["type"], m["serial"]) == ("hot_water", "HW-90123456")


# --- Серийники: номер счётчика только из подтверждённого показания, несовпадение — без записи ---

def mismatch_text(photo: str, saved: str, label: str = LABEL) -> str:
    return T.SERIAL_MISMATCH.format(photo=photo, label=label, saved=saved)


async def test_serial_new_meter_retake_other_serial_no_false_mismatch(chat, api, repo, deps, user):
    """Баг «два одинаковых номера»: номер первого фото писался в черновик, второе фото его перезаписывало."""
    deps.recognizer = FixedRecognizer({"t1": 7_000}, serial="18-111111")
    await chat.photo()
    await chat.press("Гор. вода")
    await chat.press("Арбат 47к1, кв 32")
    assert "18-111111 — сохраним" in api.last_text()
    await chat.press(T.BTN_RETAKE)
    deps.recognizer = FixedRecognizer({"t1": 7_000}, serial="18-222222")
    await chat.photo("https://i/2")
    assert (await state(repo)).state == S.SUB_REVIEW and "18-222222 — сохраним" in api.last_text()
    assert "18-111111" not in api.last_text()
    await chat.press(T.BTN_SEND)
    (m,) = await repo.address_meters(user[1])
    assert m["serial"] == "18-222222"


async def test_serial_mismatch_message_and_nothing_saved_until_decision(chat, api, repo, deps, user):
    mid = await meter(repo, user, serial="18-654321")
    deps.recognizer = FixedRecognizer({"t1": 5_000}, serial="18-123456")
    await to_review(chat)
    assert api.last_text() == mismatch_text("18-123456", "18-654321")
    assert buttons(api) == [T.BTN_OTHER_METER, T.BTN_SAME_METER, T.BTN_RETAKE, C.BTN_CANCEL]
    assert await readings(repo, mid) == [] and (await repo.get_meter(mid))["serial"] == "18-654321"
    await chat.text("что?")  # повтор шага — номера по-прежнему разные
    assert api.last_text() == mismatch_text("18-123456", "18-654321")
    await chat.press(C.BTN_CANCEL)
    assert await readings(repo, mid) == [] and (await repo.get_meter(mid))["serial"] == "18-654321"


async def test_serial_wrong_on_photo_then_next_submission_still_compares_saved(chat, api, repo, deps, user):
    mid = await meter(repo, user, serial="18-654321")
    deps.recognizer = FixedRecognizer({"t1": 5_000}, serial="18-123456")
    await to_review(chat)
    await chat.press(T.BTN_SAME_METER)
    assert "18-123456 — оставили сохранённый" in api.last_text()
    await chat.press(T.BTN_SEND)
    row = await repo._one("SELECT recognized_json FROM readings WHERE meter_id=?", (mid,))
    rec = json.loads(row["recognized_json"])
    assert rec["serial"] is None and rec["photo_serial"] == "18-123456"
    assert (await repo.get_meter(mid))["serial"] == "18-654321"
    await to_review(chat, url="https://i/next")  # следующая подача с тем же фото — снова вопрос, номер не подменён
    assert api.last_text() == mismatch_text("18-123456", "18-654321")


async def test_serial_retake_after_mismatch_matches_after_normalization(chat, api, repo, deps, user):
    mid = await meter(repo, user, serial="18-654321")
    deps.recognizer = FixedRecognizer({"t1": 5_000}, serial="18-123456")
    await to_review(chat)
    await chat.press(T.BTN_RETAKE)
    deps.recognizer = FixedRecognizer({"t1": 5_000}, serial="№ 18 654 321")
    await chat.photo("https://i/2")
    assert (await state(repo)).state == S.SUB_REVIEW
    assert "Серийный номер: 18 654 321 — совпадает" in api.last_text()
    await chat.press(T.BTN_SEND)
    assert (await repo.get_meter(mid))["serial"] == "18-654321"


async def test_serial_other_meter_then_pick_it(chat, api, repo, deps, user):
    cold_id = await meter(repo, user, serial="18-654321")
    hot = await meter(repo, user, "hot_water", serial="18-123456")
    deps.recognizer = FixedRecognizer({"t1": 5_000}, serial="18-123456")
    await to_review(chat)
    await chat.press(T.BTN_OTHER_METER)
    assert (await state(repo)).state == S.SUB_PICK_METER
    await chat.press("Гор. вода · Арбат 47к1, кв 32")
    assert "18-123456 — совпадает" in api.last_text()
    await chat.press(T.BTN_SEND)
    assert [r["t1"] for r in await readings(repo, hot)] == [5_000] and await readings(repo, cold_id) == []
    assert (await repo.get_meter(cold_id))["serial"] == "18-654321"


async def test_serial_of_another_meter_for_meter_without_serial(chat, api, repo, deps, user):
    cold_id = await meter(repo, user, serial=None)
    hot = await meter(repo, user, "hot_water", serial="18-123456")
    deps.recognizer = FixedRecognizer({"t1": 5_000}, serial="18 123456")
    await to_review(chat)
    assert (await state(repo)).state == S.SUB_SERIAL_MISMATCH
    assert api.last_text() == T.SERIAL_OF_OTHER.format(photo="18 123456", other="Гор. вода · Арбат 47к1, кв 32",
                                                       label=LABEL)
    await chat.press(T.BTN_SAME_METER)  # номер с фото не берём, а своего у счётчика нет — спросим
    assert (await state(repo)).state == S.SUB_SERIAL_INPUT and T.SERIAL_MISSING not in api.last_text()
    await chat.text("18-123456")  # тот же чужой номер
    assert api.last_text().startswith("Номер 18-123456 уже записан у другого вашего счётчика")
    await chat.text("18-777777")
    assert "Серийный номер: 18-777777 — сохраним" in api.last_text()
    await chat.press(T.BTN_SEND)
    assert (await repo.get_meter(cold_id))["serial"] == "18-777777"
    assert (await repo.get_meter(hot))["serial"] == "18-123456" and len(await readings(repo, cold_id)) == 1


async def test_serial_cyrillic_lookalike_matches(chat, api, repo, deps, user):
    await meter(repo, user, serial="АВ-7712345")  # кириллица
    deps.recognizer = FixedRecognizer({"t1": 5_000}, serial="AB7712345")
    await to_review(chat)
    assert (await state(repo)).state == S.SUB_REVIEW and "AB7712345 — совпадает" in api.last_text()


# --- Почему не распознали: коды проблем от сервиса ---

async def test_issues_two_reasons_and_model_note(chat, api, repo, deps, settings, cold):
    deps.recognizer = FixedRecognizer(confidence=0.0, issues=["glare", "angle", "too_dark"],
                                      note="на табло отражается лампа")
    await to_review(chat)
    text = api.last_text()
    assert text.startswith(T.FAILED_HEAD)
    assert T.ISSUE_TEXTS["glare"] in text and T.ISSUE_TEXTS["angle"] in text and T.ISSUE_TEXTS["too_dark"] not in text
    assert "На табло отражается лампа." in text and text.endswith(T.FAILED_TAIL)
    assert buttons(api) == [T.BTN_RETAKE, T.BTN_MANUAL, C.BTN_CANCEL]
    assert (await state(repo)).state == S.SUB_AWAIT_PHOTO and photo_files(settings) == []


async def test_issues_duplicate_note_hidden_and_serial_code_not_shown(chat, api, deps, cold):
    deps.recognizer = FixedRecognizer(confidence=0.0, issues=["serial_not_visible", "digits_not_visible"],
                                      note="Цифры не видны")
    await to_review(chat)
    assert api.last_text() == "\n".join([T.FAILED_HEAD, "", T.ISSUE_TEXTS["digits_not_visible"], "", T.FAILED_TAIL])


async def test_issue_no_meter_other_uses_note(chat, api, deps, cold):
    deps.recognizer = FixedRecognizer(confidence=0.0, issues=["no_meter"])
    await to_review(chat)
    assert "Похоже, на фото нет счётчика" in api.last_text()
    deps.recognizer = FixedRecognizer(confidence=0.0, issues=["other"], note="на фото кошка")
    await chat.photo("https://i/2")
    assert "На фото кошка." in api.last_text() and T.ISSUE_TEXTS["other"] not in api.last_text()


async def test_issue_wrong_type_unreadable_pick_other_meter_keeps_photo(chat, api, repo, deps, settings, user, cold):
    gas = await meter(repo, user, "gas")
    deps.recognizer = FixedRecognizer(confidence=0.0, issues=["wrong_type"])
    await to_review(chat)
    assert "Похоже, это не счётчик холодной воды — выберите другой счётчик." in api.last_text()
    assert buttons(api) == [T.BTN_RETAKE, T.BTN_MANUAL, T.BTN_PICK_OTHER, C.BTN_CANCEL]
    assert len(photo_files(settings)) == 1
    await chat.press(T.BTN_PICK_OTHER)
    assert (await state(repo)).state == S.SUB_PICK_METER
    deps.recognizer = FixedRecognizer({"t1": 5_000})
    await chat.press("Газ · Арбат 47к1, кв 32")
    assert (await state(repo)).state == S.SUB_REVIEW and deps.recognizer.calls[0][0] == "gas"
    await chat.press(T.BTN_SEND)
    assert (await readings(repo, gas))[-1]["t1"] == 5_000


async def test_issue_wrong_type_manual_drops_kept_photo(chat, api, repo, deps, settings, cold):
    deps.recognizer = FixedRecognizer(confidence=0.0, issues=["wrong_type"])
    await to_review(chat)
    await chat.press(T.BTN_MANUAL)
    assert (await state(repo)).state == S.SUB_MANUAL and photo_files(settings) == []


async def test_issue_wrong_type_readable_warns_on_review(chat, api, repo, deps, user, cold):
    await meter(repo, user, "hot_water")
    deps.recognizer = FixedRecognizer({"t1": 120_000}, confidence=0.6, issues=["wrong_type"])
    await to_review(chat)
    assert T.WRONG_TYPE_WARN in api.last_text() and "Показание: **120,000 м³**" in api.last_text()
    assert buttons(api) == [T.BTN_SEND, T.BTN_EDIT, T.BTN_RETAKE, T.BTN_PICK_OTHER, C.BTN_CANCEL]
    await chat.press(T.BTN_PICK_OTHER)
    assert (await state(repo)).state == S.SUB_PICK_METER
    deps.recognizer = FixedRecognizer({"t1": 7_000})
    await chat.press("Гор. вода · Арбат 47к1, кв 32")
    assert T.WRONG_TYPE_WARN not in api.last_text()


def test_issue_lines_limits():
    lines = T.issue_lines(["glare", "angle", "blurry"], "  ", "heat")
    assert lines == [T.ISSUE_TEXTS["glare"], T.ISSUE_TEXTS["angle"]]
    assert T.issue_lines(["wrong_type"], None, "heat") == ["Похоже, это не счётчик отопления — выберите другой счётчик."]
    assert T.issue_lines([], "x" * 300, "gas")[0].endswith("…")
    assert T.issue_lines(["glare"], "Сильные блики на стекле", "gas") == [T.ISSUE_TEXTS["glare"],
                                                                        "Сильные блики на стекле."]
    assert T.issue_lines(["glare"], "*жирный*", "gas", markup=False)[-1] == "*жирный*."
    assert T.recognize_failed([], None, "gas") == T.RECOGNIZE_FAILED


# --- F13–F16: проверки при отправке ---

async def test_f13_less_than_previous_photo_path(chat, api, repo, deps, cold):
    deps.recognizer = FixedRecognizer({"t1": 100_000})
    await to_review(chat)
    await chat.press(T.BTN_SEND)
    assert (await state(repo)).state == S.SUB_PLAUSIBILITY
    assert "было 118,200 м³, сейчас 100,000 м³" in api.last_text()
    assert buttons(api) == [T.BTN_EDIT, T.BTN_RETAKE, C.BTN_CANCEL]
    await chat.text("да")  # «да» здесь не подтверждает — вопрос заново
    assert (await state(repo)).state == S.SUB_PLAUSIBILITY
    await chat.press(T.BTN_EDIT)
    await chat.text("119")
    await chat.press(T.BTN_SEND)
    assert (await readings(repo, cold))[-1]["t1"] == 119_000


async def test_f13_less_than_previous_manual_path_has_no_retake(chat, api, repo, cold):
    await chat.payload("g|submit|")
    await chat.press(T.BTN_MANUAL)
    assert api.last_text() == T.PICK_MANUAL
    await chat.press(LABEL)
    assert "В прошлый раз: 118,2 м³" in api.last_text()
    await chat.text("100")
    assert T.BTN_RETAKE not in buttons(api)
    await chat.press(T.BTN_SEND)
    assert buttons(api) == [T.BTN_EDIT, C.BTN_CANCEL]


async def test_f14_big_growth_confirm_flagged(chat, api, repo, deps, cold):
    deps.recognizer = FixedRecognizer({"t1": 149_200})  # +31
    await to_review(chat)
    await chat.press(T.BTN_SEND)
    text = api.last_text()
    assert "Большой прирост: +31,000 м³ за 1 месяц. Обычно не больше 30,000 м³." in text
    assert buttons(api) == [T.BTN_CONFIRM_BIG, T.BTN_EDIT, C.BTN_CANCEL]
    await chat.press(T.BTN_CONFIRM_BIG)
    assert (await readings(repo, cold))[-1]["status"] == "flagged"
    assert T.FLAGGED_DONE in api.last_text()


async def test_f14_threshold_times_months(chat, api, repo, deps, user):
    mid = await meter(repo, user, readings=[("2026-07", 100_000)])
    deps.recognizer = FixedRecognizer({"t1": 185_000})  # +85 за 3 мес. < 90
    await to_review(chat)
    await chat.press(T.BTN_SEND)
    assert api.last_text().startswith("Готово!") and (await readings(repo, mid))[-1]["status"] == "accepted"


async def test_f15_already_submitted_replace_or_keep(chat, api, repo, cold):
    await repo.add_reading(cold, None, "2026-10", {"t1": 130_000}, "manual")
    await to_review(chat)
    await chat.press(T.BTN_SEND)
    assert (await state(repo)).state == S.SUB_REPLACE_CONFIRM
    assert api.last_text().startswith("За октябрь уже передано: 130,000 м³.")
    assert buttons(api) == [T.BTN_REPLACE, T.BTN_KEEP_OLD]
    await chat.press(T.BTN_KEEP_OLD)
    assert api.last_text().startswith(T.KEPT_OLD) and len(await readings(repo, cold)) == 2
    await to_review(chat, url="https://i/2")
    await chat.press(T.BTN_SEND)
    await chat.press(T.BTN_REPLACE)  # новое меньше октябрьского, но больше сентябрьского — ок
    assert [r["status"] for r in await readings(repo, cold)] == ["accepted", "replaced", "accepted"]


async def test_f16_double_send_one_reading(chat, api, repo, cold):
    await to_review(chat)
    payload = api.button(T.BTN_SEND)["payload"]
    await chat.payload(payload)
    api.clear()
    await chat.payload(payload)
    assert api.named("answer")[0]["notification"] == C.STALE_BUTTON
    assert len(await readings(repo, cold)) == 2


# --- F17–F21: фото посреди сценария, альбом, выход, TTL, ошибка скачивания ---

async def test_f17_new_photo_in_review_and_in_pick(chat, api, repo, settings, cold):
    await to_review(chat)
    api.clear()
    await chat.photo("https://i/2", b"\xff\xd8two")
    assert api.texts()[0] == f"{T.PHOTO_REPLACED}\n\n{T.LOOKING}" and (await state(repo)).state == S.SUB_REVIEW
    assert [f.read_bytes() for f in photo_files(settings)] == [b"\xff\xd8two"]
    await chat.press(C.BTN_CANCEL)
    await chat.photo("https://i/3", b"\xff\xd8three")
    await chat.photo("https://i/4", b"\xff\xd8four")
    assert api.last_text() == f"{T.PHOTO_REPLACED}\n\n{T.PICK_PHOTO}"
    assert [f.read_bytes() for f in photo_files(settings)] == [b"\xff\xd8four"]


async def test_new_photo_while_building_draft_keeps_step(chat, api, repo, settings, user):
    await chat.photo()
    await chat.press("Свет")
    await chat.photo("https://i/2", b"\xff\xd8two")
    assert api.last_text().startswith(T.PHOTO_REPLACED) and (await state(repo)).state == S.SUB_NEW_TARIFF
    assert len(photo_files(settings)) == 1


async def test_f18_album_takes_first(chat, api, repo, settings, cold):
    api.files.update({"https://i/a": b"\xff\xd8a", "https://i/b": b"\xff\xd8b"})
    await chat.feed(fakes.message_created(UID, None, [fakes.image("https://i/a"), fakes.image("https://i/b")]))
    assert api.last_text().startswith(f"{C.FIRST_PHOTO_ONLY}\n{T.PHOTO_RECEIVED}")
    assert [d["url"] for d in api.named("download")] == ["https://i/a"]
    assert [f.read_bytes() for f in photo_files(settings)] == [b"\xff\xd8a"]


@pytest.mark.parametrize("exit_", ["/start", "g|menu|"])
async def test_f19_start_and_menu_mid_submission(chat, api, repo, settings, cold, exit_):
    await to_review(chat)
    await (chat.text(exit_) if exit_.startswith("/") else chat.payload(exit_))
    assert (await state(repo)).state == S.IDLE and photo_files(settings) == []
    assert C.SUB_CANCELLED in api.last_text()


async def test_f20_expired_session_and_sweep(chat, api, repo, settings, cold, frozen_clock):
    await to_review(chat)
    clock.set_now(frozen_clock + timedelta(minutes=31))
    assert await photos.sweep(repo, clock.now()) == 1 and photo_files(settings) == []
    await chat.text("что-нибудь")
    assert api.last_text().startswith(C.SUB_EXPIRED) and (await state(repo)).state == S.IDLE


async def test_f20_expired_session_photo_removed_on_next_event(chat, api, repo, settings, cold, frozen_clock):
    await to_review(chat)
    clock.set_now(frozen_clock + timedelta(minutes=29))
    await chat.text("что-нибудь")  # активность продлевает сессию
    clock.set_now(frozen_clock + timedelta(minutes=59))
    await chat.press(T.BTN_SEND)
    assert (await state(repo)).state == S.IDLE and photo_files(settings) == []


async def test_f21_download_error(chat, api, repo, settings, cold):
    await chat.feed(fakes.message_created(UID, None, [fakes.image("https://i/missing")]))
    assert api.last_text() == T.PHOTO_FAILED and buttons(api) == [T.BTN_MANUAL, C.BTN_CANCEL]
    assert (await state(repo)).state == S.SUB_AWAIT_PHOTO and photo_files(settings) == []
    await to_review(chat)
    await chat.feed(fakes.message_created(UID, None, [fakes.image("https://i/missing")]))
    assert api.last_text().startswith(T.PHOTO_FAILED) and (await state(repo)).state == S.SUB_REVIEW
    assert len(photo_files(settings)) == 1  # прежнее фото на месте


# --- F22–F24: ручной ввод, добавление счётчика, дата поверки ---

async def test_f22_manual_three_tariffs_with_back(chat, api, repo, user):
    mid = await meter(repo, user, "electricity", 3,
                      readings=[("2026-09", {"t1": 1_000_000, "t2": 500_000, "t3": 300_000})])
    await chat.payload("g|submit|")
    await chat.press(T.BTN_MANUAL)
    await chat.press("Свет · Арбат 47к1, кв 32")
    assert "Т1 пик (1 из 3)" in api.last_text() and "В прошлый раз: 1000 кВт·ч" in api.last_text()
    await chat.text("1010")
    assert "Т2 ночь (2 из 3)" in api.last_text()
    await chat.press(C.BTN_BACK)
    assert "Т1 пик (1 из 3)" in api.last_text()
    await chat.text("1010")
    await chat.text("505,5")
    await chat.text("303")
    review = api.last_text()
    assert "Т1 пик: **1010 кВт·ч**" in review and "Т3 полупик: **303 кВт·ч**" in review
    assert "В прошлый раз: 1000 / 500 / 300 кВт·ч" in review
    assert buttons(api) == [T.BTN_SEND, T.BTN_EDIT, C.BTN_CANCEL]
    await chat.press(T.BTN_SEND)
    assert (await readings(repo, mid))[-1] == {"t1": 1_010_000, "t2": 505_500, "t3": 303_000,
                                               "source": "manual", "status": "accepted"}


async def test_f22_back_on_first_field_returns_to_pick(chat, api, repo, cold):
    await chat.payload("g|submit|")
    await chat.press(T.BTN_MANUAL)
    await chat.press(LABEL)
    await chat.press(C.BTN_BACK)
    assert (await state(repo)).state == S.SUB_PICK_METER


async def test_f23_add_meter_manual_and_photo(chat, api, repo, user, hook_button):
    add = hook_button("submission.add_meter")
    await chat.payload(add)
    assert api.last_text() == T.ASK_TYPE and C.BTN_BACK not in buttons(api)
    await chat.press("Газ")
    await chat.press("Арбат 47к1, кв 32")
    assert api.last_text() == T.ADD_PROMPT and buttons(api) == [T.BTN_MANUAL, C.BTN_CANCEL]
    assert await repo.address_meters(user[1]) == []
    await chat.press(T.BTN_MANUAL)
    await chat.press(C.BTN_BACK)  # C4: первое поле → снова просьба прислать фото
    assert api.last_text() == T.ADD_PROMPT
    await chat.press(T.BTN_MANUAL)
    await chat.text("1234,5")
    # Без фото номер тоже нужен: спрашиваем его тем же шагом ввода.
    assert (await state(repo)).state == S.SUB_SERIAL_INPUT and T.ASK_SERIAL.format(example="1234567") in api.last_text()
    await chat.press(C.BTN_BACK)
    assert (await state(repo)).state == S.SUB_MANUAL
    await chat.text("1234,5")
    await chat.text("GZ-7654321")
    assert "Показание: **1234,5 м³**" in api.last_text() and "GZ-7654321 — сохраним" in api.last_text()
    await chat.press(T.BTN_SEND)
    (gas,) = await repo.address_meters(user[1])
    assert gas["serial"] == "GZ-7654321"
    assert gas["type"] == "gas" and (await readings(repo, gas["id"]))[0]["t1"] == 1_234_500

    await chat.press(T.BTN_LATER)
    await chat.payload(add)
    await chat.press("Тепло")
    await chat.press("Арбат 47к1, кв 32")
    await chat.photo()
    await give_serial(chat, "12345678")
    assert (await state(repo)).state == S.SUB_REVIEW
    await chat.press(T.BTN_SEND)
    assert {m["type"] for m in await repo.address_meters(user[1])} == {"gas", "heat"}


async def test_f24_verification_date(chat, api, repo, user):
    await chat.photo()
    await chat.press("Газ")
    await chat.press("Арбат 47к1, кв 32")
    await give_serial(chat, "1234567")
    await chat.press(T.BTN_SEND)
    assert (await state(repo)).state == S.SUB_VERIF_DATE
    for bad, err in [("31.02.2030", "no_such_date"), ("01.01.2020", "past"), ("скоро", "format")]:
        await chat.text(bad)
        assert api.last_text() == T.VERIF_ERRORS[err] and buttons(api) == [T.BTN_LATER]
    await chat.text("15.03.2030")
    assert api.last_text() == "Записали: поверка до 15 марта 2030. Напомним заранее."
    (m,) = await repo.address_meters(user[1])
    assert (m["verification_due"], m["verification_source"]) == ("2030-03-15", "user")
    assert (await state(repo)).state == S.IDLE and buttons(api) == [C.BTN_MENU, T.BTN_MORE]


async def test_f24_later_and_photo_starts_new_submission(chat, api, repo, user):
    for i in range(2):
        await chat.photo()
        if (await state(repo)).state == S.SUB_PICK_METER:
            await chat.press(T.BTN_NEW_METER)
        await chat.press("Тепло")
        await chat.press("Арбат 47к1, кв 32")
        await give_serial(chat, f"1234567{i}")
        await chat.press(T.BTN_SEND)
        assert (await state(repo)).state == S.SUB_VERIF_DATE
    await chat.press(T.BTN_LATER)
    assert api.last_text() == T.VERIF_LATER and (await state(repo)).state == S.IDLE
    await chat.photo()  # не отвечая на вопрос о дате — новая подача (B8)
    assert (await state(repo)).state == S.SUB_PICK_METER


@pytest.mark.parametrize("exit_", ["/start", "отмена", "g|menu|", "expired"])
async def test_f24_leaving_verification_date_is_not_a_cancel(chat, api, repo, user, frozen_clock, exit_):
    """Показание уже сохранено: выход из вопроса о дате поверки не пишет «подачу отменили/прервалась»."""
    await chat.photo()
    await chat.press("Газ")
    await chat.press("Арбат 47к1, кв 32")
    await give_serial(chat, "1234567")
    await chat.press(T.BTN_SEND)
    assert (await state(repo)).state == S.SUB_VERIF_DATE
    if exit_ == "expired":
        clock.set_now(frozen_clock + timedelta(minutes=31))
        await chat.text("что-нибудь")
    elif exit_.startswith("g|"):
        await chat.payload(exit_)
    else:
        await chat.text(exit_)
    text = api.last_text()
    assert (await state(repo)).state == S.IDLE and text.startswith("Показания за октябрь")
    for line in (C.SUB_CANCELLED, C.SUB_CANCELLED_BY_USER, C.SUB_EXPIRED):
        assert line not in text
    assert len(await repo.history((await repo.address_meters(user[1]))[0]["id"])) == 1


# --- F25–F28 ---

async def test_f25_two_meters_same_type_distinct_labels(chat, api, repo, user):
    await meter(repo, user, serial="18-12 4521")
    await meter(repo, user, serial="99-0007")
    await chat.photo()
    assert buttons(api)[:2] == [f"{LABEL} …4521", f"{LABEL} …0007"]
    assert all(len(b) <= 40 for b in buttons(api))


async def test_f26_only_pending_addresses_no_access(chat, api, repo, settings, no_access, hook_button):
    _, aid = await register(repo, OTHER)
    await register(repo, UID)  # тот же адрес → tenant/pending
    await chat.photo()
    assert no_access == [aid] and api.named("download") == [] and photo_files(settings) == []
    for entry in ("g|submit|", hook_button("submission.manual"), hook_button("submission.add_meter")):
        await chat.payload(entry)
    assert no_access == [aid] * 4 and (await state(repo)).state == S.IDLE


async def test_f26_real_no_access_screen_from_s1(chat, api, repo, settings):
    await register(repo, OTHER)
    await register(repo, UID)
    await chat.photo()
    assert "уже зарегистрирован собственник" in api.last_text() and "Запросить доступ" in buttons(api)
    assert photo_files(settings) == [] and (await state(repo)).state == S.IDLE


async def test_f27_restart_in_review(chat, api, repo, settings, cold):
    await to_review(chat)
    repo2 = await Repo.open(settings.db_path)
    try:
        chat2 = Chat(Router(Deps(api, repo2, settings, StubRecognizer(), "test_bot")), api)
        await chat2.press(T.BTN_SEND)
    finally:
        await repo2.close()
    assert len(await readings(repo, cold)) == 2 and photo_files(settings) == []


async def test_f28_number_instead_of_photo(chat, api, repo, cold):
    await chat.payload("g|submit|")
    await chat.text("125,5")
    assert api.last_text() == T.PICK_MANUAL
    await chat.press(LABEL)
    assert "Показание: **125,5 м³**" in api.last_text() and (await state(repo)).state == S.SUB_REVIEW
    await chat.text("126")  # число на проверке — исправление
    assert "Показание: **126 м³**" in api.last_text()  # как ввели, без дописанных нулей
    await chat.text("да")
    assert (await readings(repo, cold))[-1] == {"t1": 126_000, "t2": None, "t3": None,
                                                "source": "manual", "status": "accepted"}


async def test_with_photo_hook_after_registration(chat, api, repo, settings, cold, hook_button):
    uid = (await repo.get_user(UID))["id"]
    api.files["https://i/p"] = b"\xff\xd8pending"
    pid = await photos.download_to_tmp(api, repo, settings.photos_dir, uid, "https://i/p", NOW,
                                       photos.PENDING_PHOTO_TTL)
    await chat.payload(hook_button("submission.with_photo", photo_id=pid))
    assert api.last_text() == f"{T.PENDING_PHOTO}\n\n{T.PICK_PHOTO}"
    await chat.press(LABEL)
    assert (await state(repo)).state == S.SUB_REVIEW
    await photos.delete_photo(repo, pid)
    await chat.payload(hook_button("submission.with_photo", photo_id=pid))
    assert api.last_text().endswith(T.PHOTO_GONE) and (await state(repo)).state == S.SUB_AWAIT_PHOTO


def test_button_texts_fit():
    short = [v for k, v in vars(T).items() if k.startswith("BTN_")] + list(T.TARIFF_BUTTONS.values())
    assert all(len(b) <= 24 for b in short), [b for b in short if len(b) > 24]


# --- Серийники: «не номер» отбрасываем, необычный формат — мягкое предупреждение ---

async def test_serial_not_a_serial_is_dropped(chat, api, repo, deps, user):
    mid = await meter(repo, user, serial=None)
    deps.recognizer = FixedRecognizer({"t1": 5_000}, serial="2018")  # год выпуска
    await to_review(chat)
    assert (await state(repo)).state == S.SUB_SERIAL_MISSING and "2018" not in api.last_text()
    await give_serial(chat, "Серийный номер 18-246810")
    assert (await state(repo)).state == S.SUB_REVIEW and "Серийный номер: 18-246810 — сохраним" in api.last_text()
    await chat.press(T.BTN_SEND)
    assert (await repo.get_meter(mid))["serial"] == "18-246810"


async def test_serial_unusual_length_warns_but_saves(chat, api, repo, deps, user):
    mid = await meter(repo, user, serial=None)
    deps.recognizer = FixedRecognizer({"t1": 5_000}, serial="№ 12345")
    await to_review(chat)
    text = api.last_text()
    assert "Серийный номер: 12345 — сохраним" in text and "обычно 8 цифр" in text and "№" not in text
    await chat.press(T.BTN_SEND)
    assert (await repo.get_meter(mid))["serial"] == "12345"


async def test_serial_display_formatted_by_type(chat, api, repo, deps, user):
    await meter(repo, user, "electricity", serial="0112 3456 7890")
    deps.recognizer = FixedRecognizer({"t1": 5_000}, serial="№ 011234567890")
    await to_review(chat, "Свет · Арбат 47к1, кв 32")
    assert "Серийный номер: 011234567890 — совпадает" in api.last_text()


async def test_tariff_question_has_where_to_look_hint(chat, api, repo, user):
    await chat.payload("g|submit|")
    await chat.press(T.BTN_MANUAL)
    await chat.press("Свет")
    text = api.last_text()
    assert text.startswith("Сколько тарифов у счётчика?") and "Где посмотреть:" in text
    assert "Т1, Т2" in text and "«день» и «ночь»" in text and len(text.splitlines()) <= 3
