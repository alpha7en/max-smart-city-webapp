"""Подача показаний через настоящий роутер и FakeMaxApi."""
from __future__ import annotations

import asyncio
import itertools
import json
from pathlib import Path

import pytest

from app.bot import router as R
from app.bot.router import call_hook
from app.bot.session import load_session
from app.bot.states import S
from app.bot.texts import common as C
from app.bot.texts import submission as T
from app.domain.addresses import AddressCandidate, norm_key
from app.integrations.address_service import AddressService
from app.integrations.recognizer import Recognition
from tests import fakes
from tests.conftest import NOW, Chat

UID, OTHER = 5273381, 777
ARBAT = "Москва, Арбат 47к1, кв. 32"
LABEL = "Хол. вода · Арбат 47к1, кв 32"  # кнопка
ARBAT_FULL = "г. Москва, Арбат, д. 47, корп. 1, кв. 32"
FULL = f"Хол. вода · {ARBAT_FULL}"  # в тексте — полный адрес


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


async def test_f3_photo_recognize_send(chat, api, repo, settings, user, cold):
    await chat.photo()
    api.clear()
    await chat.press(LABEL)
    assert api.named("typing") and api.texts()[0] == T.LOOKING
    assert api.named("delete")  # «Смотрим на фото…» удалено при получении результата
    review = api.last_text()
    assert review.startswith(FULL + "\n\nПоказание: **") and "В прошлый раз: 118,2 м³ (+" in review
    assert review.endswith(f"{T.REVIEW_QUESTION}\n\n> {T.STUB_NOTE}")  # демо — последней цитатой
    assert buttons(api) == [T.BTN_SEND, T.BTN_EDIT, T.BTN_RETAKE, C.BTN_CANCEL]
    assert (await state(repo)).state == S.SUB_REVIEW
    await chat.press(T.BTN_SEND)
    (_, row) = await readings(repo, cold)
    assert (row["source"], row["status"]) == ("photo", "accepted") and row["t1"] > 118_200
    done = api.last_text()
    assert done.startswith(T.DONE.split("\n")[0].format(month="октябрь")) and "Холодная вода · г. Москва, Арбат, д. 47, корп. 1, кв. 32" in done
    assert done.endswith(f"\n\n> {T.UK_MOCK}") and T.STUB_NOTE not in done  # о демо-распознавании сказали на проверке
    assert buttons(api) == [C.BTN_MENU, T.BTN_MORE]
    assert api.button(T.BTN_MORE)["payload"] == "g|submit|"
    assert (await state(repo)).state == S.IDLE and photo_files(settings) == []


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


async def test_f13_less_than_previous_photo_path(chat, api, repo, deps, cold):
    deps.recognizer = FixedRecognizer({"t1": 100_000})
    await to_review(chat)
    await chat.press(T.BTN_SEND)
    assert (await state(repo)).state == S.SUB_PLAUSIBILITY
    assert "было **118,200 м³**, сейчас **100,000 м³**" in api.last_text()
    assert buttons(api) == [T.BTN_EDIT, T.BTN_RETAKE, C.BTN_CANCEL]
    await chat.text("да")  # «да» здесь не подтверждает — вопрос заново
    assert (await state(repo)).state == S.SUB_PLAUSIBILITY
    await chat.press(T.BTN_EDIT)
    await chat.text("119")
    await chat.press(T.BTN_SEND)
    assert (await readings(repo, cold))[-1]["t1"] == 119_000


async def test_f15_already_submitted_replace_or_keep(chat, api, repo, cold):
    await repo.add_reading(cold, None, "2026-10", {"t1": 130_000}, "manual")
    await to_review(chat)
    await chat.press(T.BTN_SEND)
    assert (await state(repo)).state == S.SUB_REPLACE_CONFIRM
    assert api.last_text().startswith(T.ALREADY_SUBMITTED.split("\n")[0].format(month="октябрь", old="130,000 м³"))
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
    assert "Показание: **1234,5 м³**" in api.last_text() and "GZ-7654321** — сохраним" in api.last_text()
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
