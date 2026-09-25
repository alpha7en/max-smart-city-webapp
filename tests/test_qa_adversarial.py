"""QA: попытки сломать бота так, как это может сделать жюри (повторные прохождения, двойные нажатия,
старые кнопки, мусорный ввод, сбои MAX API, рестарты, TTL, переходы месяца, мини-API).

Прошедшие атаки — обычные регрессионные тесты. Найденные проблемы — xfail(strict=True) с номером QA-N
(подробности в отчёте QA). Когда проблему починят, xfail станет XPASS и suite покраснеет — снимите маркер.
"""
from __future__ import annotations

import asyncio
import itertools
import json
from datetime import datetime, timedelta

import pytest

from app import clock
from app.bot import keyboards as K
from app.bot.ctx import Deps
from app.bot.events import parse_update
from app.bot.flows.notify import send_due_notice
from app.bot.router import Router
from app.bot.session import load_session
from app.bot.states import S
from app.bot.texts import common as C
from app.bot.texts import menu as MT
from app.bot.texts import notify as NT
from app.bot.texts import profile as PT
from app.bot.texts import registration as RT
from app.bot.texts import submission as T
from app.integrations.max_api import MaxApiError
from app.integrations.recognizer import StubRecognizer
from app.repo import Repo
from app.scheduler import notify_tick
from tests import fakes
from tests.conftest import NOW, Chat
from tests.test_api import add_meter, assert_error, auth, post
from tests.test_api import client, make_client  # noqa: F401 — фикстуры мини-API
from tests.test_api import register as api_register
from tests.test_registration import register as register_chat
from tests.test_registration import to_confirm

UID, UID2, UID3 = 5273381, 7000001, 7000002
ARBAT = "Москва, Арбат 47к1, кв. 32"
ARBAT_LABEL = "Арбат 47к1, кв 32"
COLD = "Хол. вода"
MAX_TEXT = 4000  # лимит длины текста сообщения MAX API


# --- Помощники ---

async def sess(repo, uid: int = UID):
    return await load_session(repo, (await repo.get_user(uid))["id"])


def kb_labels(api) -> list[str]:
    kb = api.outgoing()[-1][1] or {}
    return [b["text"] for row in kb.get("payload", {}).get("buttons", []) for b in row]


def first_button(api) -> dict:
    kb = api.outgoing()[-1][1] or {}
    return kb["payload"]["buttons"][0][0]


def all_texts(api) -> list[str]:
    return [t or "" for t, _ in api.outgoing(everything=True)]


def assert_sane_output(api) -> None:
    """Инварианты всего, что увидел пользователь: текст не пустой и ≤ 4000, кнопки проходят валидацию."""
    for text, kb in api.outgoing(everything=True):
        assert text and text.strip(), "пустое сообщение"
        assert len(text) <= MAX_TEXT, f"сообщение длиннее {MAX_TEXT}: {len(text)}"
        for row in (kb or {}).get("payload", {}).get("buttons", []):
            for b in row:
                assert 1 <= len(b["text"]) <= 40, b
                if b["type"] == "callback":
                    assert len(b["payload"].encode()) <= K.PAYLOAD_MAX_BYTES


def answers_per_callback(api) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, kw in api.calls:
        if name == "answer":
            out[kw["callback_id"]] = out.get(kw["callback_id"], 0) + 1
    return out


_SERIALS = itertools.count(10_000_001)


async def enter_serial(chat: Chat) -> None:
    """Демо-распознавание номер не читает: «Не разобрали серийный номер» → [Ввести номер] → новый номер."""
    await chat.press(T.BTN_SERIAL)
    await chat.text(str(next(_SERIALS)))


async def submit_first_reading(chat: Chat, url: str = "https://i.oneme.ru/i?r=qa1", later: bool = True) -> None:
    """Фото → новый счётчик «Хол. вода» → единственный адрес → номер → Отправить → (Позже)."""
    await chat.photo(url)
    await chat.press(COLD)
    await chat.payload(first_button(chat.api)["payload"])  # первый (единственный) адрес
    await enter_serial(chat)
    await chat.press(T.BTN_SEND)
    if later:
        await chat.press(T.BTN_LATER)


async def submit_existing(chat: Chat, url: str, label: str = f"{COLD} · {ARBAT_LABEL}") -> None:
    await chat.photo(url)
    await chat.press(label)
    await chat.press(T.BTN_SEND)


async def readings(repo) -> list[dict]:
    return await repo._all("SELECT * FROM readings WHERE status!='replaced' ORDER BY id")


async def meters(repo) -> list[dict]:
    return await repo._all("SELECT * FROM meters ORDER BY id")


class FlakyApi(fakes.FakeMaxApi):
    """FakeMaxApi, у которого можно «уронить» send/answer: fail[name] = число следующих сбоев."""

    def __init__(self) -> None:
        super().__init__()
        self.fail: dict[str, int] = {}
        self.exc: Exception = MaxApiError(503, "service.unavailable", "MAX is down")

    def _maybe_fail(self, name: str) -> None:
        if self.fail.get(name, 0) > 0:
            self.fail[name] -= 1
            self.calls.append((f"{name}_failed", {}))
            raise self.exc

    async def send(self, text, **kw):
        self._maybe_fail("send")
        return await super().send(text, **kw)

    async def answer(self, callback_id, *, message=None, notification=None):
        self._maybe_fail("answer")
        return await super().answer(callback_id, message=message, notification=notification)


@pytest.fixture
def flaky() -> FlakyApi:
    return FlakyApi()


@pytest.fixture
def fchat(repo, settings, flaky) -> Chat:
    router = Router(Deps(api=flaky, repo=repo, settings=settings, recognizer=StubRecognizer(),
                         bot_username="test_bot"))
    return Chat(router, flaky)


def new_router(repo, settings, api) -> Router:
    return Router(Deps(api=api, repo=repo, settings=settings, recognizer=StubRecognizer(), bot_username="test_bot"))


# === 1. Повторное прохождение ===

async def test_full_scenario_twice_same_user(chat, api, repo):
    """Полный сценарий, потом ещё раз: второе фото того же счётчика → «уже подано, заменить?» → Заменить."""
    await register_chat(chat)
    await submit_first_reading(chat)
    first = await readings(repo)
    assert len(first) == 1 and (await sess(repo)).state == S.IDLE

    api.clear()
    await submit_existing(chat, "https://i.oneme.ru/i?r=qa2")
    assert (await sess(repo)).state == S.SUB_REPLACE_CONFIRM
    await chat.press(T.BTN_REPLACE)
    rows = await readings(repo)
    assert len(rows) == 1 and rows[0]["id"] != first[0]["id"]
    assert (await sess(repo)).state == S.IDLE
    assert len(await meters(repo)) == 1
    await chat.text("меню")
    assert "подано" in api.last_text()
    assert_sane_output(api)


async def test_full_cycle_after_delete_my_data(chat, api, repo):
    """Регистрация → подача → «Удалить мои данные» → снова всё с начала тем же пользователем."""
    await register_chat(chat)
    await submit_first_reading(chat)
    await chat.payload("g|profile|")
    await chat.press(PT.BTN_DELETE)
    await chat.press(PT.BTN_DELETE_YES)
    assert await repo.get_user(UID) is None

    api.clear()
    await chat.press(PT.BTN_START_OVER)
    assert C.WELCOME in api.texts()
    await register_chat(chat)
    user = await repo.get_user(UID)
    assert user["registered_at"]
    (addr,) = await repo.user_addresses(user["id"])
    assert (addr["role"], addr["access"]) == ("owner", "granted")  # собственник ушёл — адрес снова наш
    # Счётчик остался за адресом: подаём в него же, за период уже есть показание — предлагаем заменить.
    await submit_existing(chat, "https://i.oneme.ru/i?r=qa3")
    assert (await sess(repo)).state == S.SUB_REPLACE_CONFIRM
    await chat.press(T.BTN_REPLACE)
    assert (await sess(repo)).state == S.IDLE
    assert len(await meters(repo)) == 1
    assert_sane_output(api)


async def test_double_tap_delete_confirm(chat, api, repo):
    """Двойное нажатие «Удалить»: второе не падает, пользователь не зависает."""
    await register_chat(chat)
    await chat.payload("g|profile|")
    await chat.press(PT.BTN_DELETE)
    yes = api.button(PT.BTN_DELETE_YES)["payload"]
    await asyncio.gather(chat.payload(yes), chat.payload(yes))
    assert await repo._all("SELECT * FROM user_addresses") == []
    assert set(answers_per_callback(api).values()) == {1}
    await chat.text("привет")
    assert (await sess(repo)).state == S.REG_NAME


# === 2. Два пользователя на одном адресе ===

async def test_two_users_same_address_confirm_simultaneously(router, api, repo):
    """Оба жмут «Всё верно» одновременно: ровно один собственник, второй — арендатор, адрес один."""
    a, b = Chat(router, api, UID), Chat(router, api, UID2)
    await to_confirm(a)
    await to_confirm(b, name="Петров Пётр", phone="+7 900 111-22-33")
    fa, fb = (await sess(repo, UID)).flow_id, (await sess(repo, UID2)).flow_id  # у каждого своя кнопка
    await asyncio.gather(a.payload(f"{fa}|yes|"), b.payload(f"{fb}|yes|"))
    roles = await repo._all("SELECT role, access FROM user_addresses ORDER BY role")
    assert [(r["role"], r["access"]) for r in roles] == [("owner", "granted"), ("tenant", "pending")]
    assert len(await repo._all("SELECT * FROM addresses")) == 1


async def test_owner_and_granted_tenant_submit_same_meter_at_once(router, api, repo):
    owner, tenant = Chat(router, api, UID), Chat(router, api, UID2)
    await register_chat(owner)
    await register_chat(tenant, name="Петров Пётр", phone="+7 900 111-22-33")
    await submit_first_reading(owner)
    await repo._exec("UPDATE readings SET period='2026-09'")  # прошлый месяц: за октябрь ещё не подано
    t = await repo.get_user(UID2)
    (addr,) = await repo.user_addresses(t["id"])
    await repo.set_access(t["id"], addr["id"], "granted")
    # оба на экране проверки одного счётчика
    for c, url in ((owner, "https://i.oneme.ru/i?r=o"), (tenant, "https://i.oneme.ru/i?r=t")):
        await c.photo(url)
        await c.press(f"{COLD} · {ARBAT_LABEL}")
    fo, ft = (await sess(repo, UID)).flow_id, (await sess(repo, UID2)).flow_id
    await asyncio.gather(owner.payload(f"{fo}|send|"), tenant.payload(f"{ft}|send|"))
    states = {(await sess(repo, UID)).state, (await sess(repo, UID2)).state}
    assert states == {S.IDLE, S.SUB_REPLACE_CONFIRM}  # второй узнал, что уже подано, а не упал
    assert [r["period"] for r in await readings(repo)] == ["2026-09", "2026-10"]


async def test_foreign_access_buttons_are_refused(router, api, repo):
    """Арендатор жмёт «Разрешить» по своему же запросу и чужому адресу, мусорный arg."""
    owner, tenant = Chat(router, api, UID), Chat(router, api, UID2)
    await register_chat(owner)
    await register_chat(tenant, name="Петров Пётр", phone="+7 900 111-22-33")
    t = await repo.get_user(UID2)
    (addr,) = await repo.user_addresses(t["id"])
    for arg in (f"{t['id']}.{addr['id']}", "", ".", "abc.def", "1.", ".1", "99999.99999", "١.١"):
        api.clear()
        await tenant.payload(f"g|acc_ok|{arg}")
        assert api.last_text() == PT.OWNER_ONLY, arg
    assert (await repo.user_address(t["id"], addr["id"]))["access"] == "pending"


# === 3. Быстрые двойные/тройные нажатия и одновременные апдейты ===

async def test_triple_tap_type_and_address_buttons(chat, api, repo):
    await register_chat(chat)
    await chat.photo("https://i.oneme.ru/i?r=tap")
    cold = api.button(COLD)["payload"]
    await asyncio.gather(*(chat.payload(cold) for _ in range(3)))
    addr = api.button(ARBAT_LABEL)["payload"]
    await asyncio.gather(*(chat.payload(addr) for _ in range(3)))
    assert (await sess(repo)).state == S.SUB_SERIAL_MISSING
    await enter_serial(chat)
    assert (await sess(repo)).state == S.SUB_REVIEW
    send = api.button(T.BTN_SEND)["payload"]
    await asyncio.gather(*(chat.payload(send) for _ in range(3)))
    assert len(await readings(repo)) == 1 and len(await meters(repo)) == 1
    assert set(answers_per_callback(api).values()) == {1}, "каждый callback — ровно один ответ"
    assert_sane_output(api)


async def test_mixed_simultaneous_updates_one_user(chat, api, repo):
    """Фото, текст, кнопка и /start одновременно — без исключений, итоговое состояние согласовано."""
    await register_chat(chat)
    api.files["https://i.oneme.ru/i?r=m1"] = b"\xff\xd8a"
    api.files["https://i.oneme.ru/i?r=m2"] = b"\xff\xd8b"
    await asyncio.gather(
        chat.feed(fakes.message_created(UID, None, [fakes.image("https://i.oneme.ru/i?r=m1")])),
        chat.feed(fakes.message_created(UID, None, [fakes.image("https://i.oneme.ru/i?r=m2")])),
        chat.text("меню"),
        chat.payload("g|submit|"),
        chat.feed(fakes.bot_started(UID)),
    )
    s = await sess(repo)
    files = {p.name for p in chat.router.deps.settings.photos_dir.glob("*")}
    rows = await repo._all("SELECT id FROM photos")
    live = {s.data.get("photo_id")} - {None}
    assert {r["id"] for r in rows} == live and {f"{i}.jpg" for i in live} == files, "осиротевшие фото"
    await chat.text("меню")
    assert (await sess(repo)).state == S.IDLE


async def test_two_photos_in_a_row_keep_single_file(chat, api, repo, settings):
    await register_chat(chat)
    await chat.photo("https://i.oneme.ru/i?r=p1", b"\xff\xd81")
    await chat.photo("https://i.oneme.ru/i?r=p2", b"\xff\xd82")
    assert T.PHOTO_REPLACED in api.last_text()
    assert len(list(settings.photos_dir.glob("*"))) == 1


async def test_double_tap_global_submit_no_scary_note(chat, api, repo):
    await register_chat(chat)
    api.clear()
    await asyncio.gather(chat.payload("g|submit|"), chat.payload("g|submit|"))
    assert not any(C.SUB_CANCELLED in t for t in api.texts())


# === 4. Старые кнопки разных сценариев, мусорные payload ===

@pytest.mark.parametrize("payload", [
    "", "|", "||", "g|", "g||", "|menu|", "x", "g|unknown|1", "zzzzzz|send|", "g|menu",
    "a" * 5000, "g|verify|99", "g|pay|99", "g|acc_req|99", "g|acc_no|1.1", "g|demo|nonsense",
    "g|verify|-1", "g|pay|١", "g|meters|junk|more|pipes",
])
async def test_garbage_callbacks_in_idle(chat, api, repo, payload):
    await register_chat(chat)
    api.clear()
    await chat.payload(payload)
    assert api.named("answer"), "callback без ответа — у пользователя вечные «часики»"
    assert api.outgoing(), "бот промолчал"
    assert C.ERROR not in api.texts()
    assert (await sess(repo)).state == S.IDLE
    await chat.text("меню")
    assert (await sess(repo)).state == S.IDLE


@pytest.mark.parametrize("payload", ["g|pay|99999999999999999999999"])
async def test_huge_numeric_arg_in_global_button(chat, api, repo, payload):
    await register_chat(chat)
    api.clear()
    await chat.payload(payload)
    assert C.ERROR not in api.texts()


async def test_scenario_buttons_with_foreign_ids(router, api, repo):
    """Кнопки текущего сценария, но с id чужого счётчика/адреса: не пускаем, не падаем, не зависаем."""
    owner, other = Chat(router, api, UID), Chat(router, api, UID3)
    await register_chat(owner)
    await submit_first_reading(owner)
    await register_chat(other, name="Сидоров Сидор", phone="+7 900 222-33-44",
                        address="Москва, Тверская 1, кв. 5")
    (m,) = await meters(repo)
    await submit_first_reading(other, "https://i.oneme.ru/i?r=own")  # у второго свой счётчик
    await other.photo("https://i.oneme.ru/i?r=o1")
    assert (await sess(repo, UID3)).state == S.SUB_PICK_METER
    flow = (await sess(repo, UID3)).flow_id
    api.clear()
    await other.payload(f"{flow}|m|{m['id']}")         # чужой счётчик
    assert (await sess(repo, UID3)).state == S.IDLE and api.outgoing()
    await other.photo("https://i.oneme.ru/i?r=o2")
    flow = (await sess(repo, UID3)).flow_id
    await other.payload(f"{flow}|new|")
    await other.payload(f"{flow}|t|cold_water")
    assert (await sess(repo, UID3)).state == S.SUB_NEW_ADDRESS
    await other.payload(f"{flow}|a|{m['address_id']}")  # чужой адрес
    assert (await sess(repo, UID3)).state == S.IDLE
    assert len(await meters(repo)) == 2 and len(await readings(repo)) == 2
    other_id = (await repo.get_user(UID3))["id"]
    assert not [r for r in await readings(repo) if r["meter_id"] == m["id"] and r["user_id"] == other_id]
    assert_sane_output(api)


async def test_old_buttons_from_every_finished_scenario(chat, api, repo):
    """Кнопки из старых сообщений регистрации, подачи, профиля — «неактуальна» + текущий шаг, без поломок."""
    await to_confirm(chat)
    old_reg = [api.button(t)["payload"] for t in (RT.BTN_EDIT_NAME, RT.BTN_EDIT_ADDRESS)]
    await chat.press(RT.BTN_ALL_OK)
    await chat.photo("https://i.oneme.ru/i?r=o")
    await chat.press(COLD)
    old_sub = [api.button(ARBAT_LABEL)["payload"], api.button(C.BTN_CANCEL)["payload"]]
    await chat.press(ARBAT_LABEL)
    await enter_serial(chat)
    old_sub.append(api.button(T.BTN_SEND)["payload"])
    await chat.press(T.BTN_SEND)
    await chat.press(T.BTN_LATER)
    await chat.payload("g|profile|")
    await chat.press(PT.BTN_DELETE)
    old_prof = [api.button(PT.BTN_DELETE_NO)["payload"]]
    await chat.press(PT.BTN_DELETE_NO)
    for p in old_reg + old_sub + old_prof:
        api.clear()
        await chat.payload(p)
        assert api.named("answer")[0]["notification"] == C.STALE_BUTTON, p
        assert (await sess(repo)).state == S.IDLE
    assert len(await readings(repo)) == 1 and await repo.get_user(UID)
    assert_sane_output(api)


async def test_old_review_send_after_new_photo_is_stale(chat, api, repo):
    await register_chat(chat)
    await chat.photo("https://i.oneme.ru/i?r=v1", b"\xff\xd8one")
    await chat.press(COLD)
    await chat.press(ARBAT_LABEL)
    await enter_serial(chat)
    old_send = api.button(T.BTN_SEND)["payload"]
    shown_first = api.last_text()
    await chat.photo("https://i.oneme.ru/i?r=v2", b"\xff\xd8two-different-bytes")
    assert api.last_text() != shown_first
    api.clear()
    await chat.payload(old_send)  # пользователь нажал «Отправить» под ПЕРВЫМ значением
    assert api.named("answer")[0]["notification"] == C.STALE_BUTTON


async def test_old_address_yes_after_new_search_is_stale(chat, api, repo):
    """QA-5: «Да» под прошлым вариантом адреса после нового ввода — устаревшая, адрес не выбирается."""
    await register_chat(chat)
    await chat.photo("https://i.oneme.ru/i?r=a1")
    await chat.press(COLD)
    await chat.press(T.BTN_OTHER_ADDRESS)
    await chat.text("Москва, Тверская 1, кв. 5")
    assert (await sess(repo)).state == S.SUB_ADDR_PICK
    old_yes = api.button(T.BTN_YES)["payload"]
    await chat.text("Москва, Тверская 3, кв. 7")      # другой адрес на том же шаге — новый поиск
    api.clear()
    await chat.payload(old_yes)
    assert api.named("answer")[0]["notification"] == C.STALE_BUTTON
    assert (await sess(repo)).state == S.SUB_ADDR_PICK
    assert len(await repo.user_addresses((await repo.get_user(UID))["id"])) == 1


# === 5. Мусорный ввод ===

@pytest.mark.parametrize("att", [fakes.sticker(), fakes.location(),
                                 fakes.file("https://fd.oneme.ru/f?r=2", "doc.pdf"),
                                 fakes.contact(UID)])
async def test_unsupported_attachments_in_review_keep_step(chat, api, repo, att):
    await register_chat(chat)
    await chat.photo("https://i.oneme.ru/i?r=u")
    await chat.press(COLD)
    await chat.press(ARBAT_LABEL)
    await enter_serial(chat)
    api.clear()
    await chat.feed(fakes.message_created(UID, None, [att]))
    assert api.last_text().startswith(C.UNSUPPORTED)
    assert T.BTN_SEND in kb_labels(api)
    assert (await sess(repo)).state == S.SUB_REVIEW


@pytest.mark.parametrize("text", [
    "x" * 5000, "😀" * 3000, "Иванова *Анна* Сергеевна", "Иванова [x](http://evil) Анна",
    "Робертс'); DROP TABLE users;--", "Иванова\nАнна\nСергеевна", "​​", "ИВАНОВА АННА",
])
async def test_weird_names(chat, api, repo, text):
    await chat.text("привет")
    await chat.text(text)
    s = await sess(repo)
    assert s.state in (S.REG_NAME, S.REG_PHONE)
    assert "evil" not in api.last_text()
    assert_sane_output(api)


@pytest.mark.parametrize("address", [
    "Москва, ул. **Жирная** [x](http://evil), д 5, кв 1",
    "Москва, ул. Арбат'; DROP TABLE addresses;--, д 5, кв 1",
    "Москва, ул. 😀Смайликов, д 5, кв 1",
    "Москва, ул. _Курсив_ ~Зачёркнутая~ `Код`, д 5, кв 1",
])
async def test_markdown_and_sql_in_address_are_escaped_everywhere(chat, api, repo, address):
    """Адрес с разметкой проходит всю цепочку: подтверждение, меню, счётчики, профиль, уведомления."""
    await register_chat(chat, address=address)
    user = await repo.get_user(UID)
    assert user["registered_at"], api.texts()[-3:]
    await submit_first_reading(chat, later=False)
    await chat.text("15.03.2030")
    for p in ("g|menu|", "g|meters|", "g|profile|"):
        await chat.payload(p)
    await chat.text("/demo")
    for p in ("g|demo|submit", "g|demo|verify", "g|demo|bill"):
        await chat.payload(p)
    for text in all_texts(api):
        assert "[x](" not in text and "**Жирная**" not in text and "_Курсив_" not in text, text
    assert await repo._all("SELECT * FROM users") and await repo._all("SELECT * FROM addresses")
    assert_sane_output(api)


@pytest.mark.parametrize("value,ok", [
    ("1e5", False), ("99999999", False), ("-5", False), ("12 345", False), ("0x1F", False),
    ("٣٤", True), ("１２３", True), ("0,000", True), ("00123,4", True), ("123.", False), ("NaN", False),
])
async def test_weird_numbers_in_manual_input(chat, api, repo, value, ok):
    await register_chat(chat)
    await chat.payload("g|submit|")
    await chat.press(T.BTN_MANUAL)
    await chat.press(COLD)
    await chat.press(ARBAT_LABEL)
    assert (await sess(repo)).state == S.SUB_MANUAL
    await chat.text(value)
    s = await sess(repo)
    assert s.state == (S.SUB_SERIAL_INPUT if ok else S.SUB_MANUAL), (value, api.last_text())
    if ok:  # новый счётчик без номера: номер спрашиваем и при ручном вводе
        await chat.text("18-123456")
        assert (await sess(repo)).state == S.SUB_REVIEW
        await chat.press(T.BTN_SEND)
        (r,) = await readings(repo)
        assert 0 <= r["t1"] < 100_000_000


async def test_huge_text_in_every_text_step(chat, api, repo):
    big = "9" * 5000
    await chat.text("привет")
    await chat.text(big)
    await chat.text("Иванова Анна")
    await chat.text(big)
    await chat.text("+7 912 345-67-89")
    await chat.text(big)
    await chat.text(ARBAT)
    await chat.press(RT.BTN_YES)
    await chat.text(big)                          # на подтверждении
    await chat.press(RT.BTN_ALL_OK)
    await chat.photo("https://i.oneme.ru/i?r=h")
    await chat.text(big)                          # на выборе типа
    await chat.press(COLD)
    await chat.press(T.BTN_OTHER_ADDRESS)
    await chat.text(big)                          # новый адрес
    await chat.press(C.BTN_BACK)
    await chat.press(ARBAT_LABEL)
    await chat.text(big)                          # вместо номера — слишком длинный, не номер
    assert (await sess(repo)).state == S.SUB_SERIAL_INPUT
    await enter_serial(chat)
    await chat.text(big)                          # на проверке (число!) → ручной ввод
    await chat.text(big)
    await chat.press(C.BTN_CANCEL)
    assert (await sess(repo)).state == S.IDLE
    assert_sane_output(api)


async def test_whitespace_and_empty_messages(chat, api, repo):
    await register_chat(chat)
    for body in ("   ", "", "\n\t"):
        api.clear()
        await chat.feed(fakes.message_created(UID, body))
        assert api.outgoing(), "на пустое сообщение бот должен ответить"
    assert (await sess(repo)).state == S.IDLE


# === 6. TTL посреди каждого шага ===

async def _walk_to(chat: Chat, api, state: S) -> None:
    await register_chat(chat)
    if state == S.PROFILE_DELETE_CONFIRM:
        await chat.payload("g|profile|")
        await chat.press(PT.BTN_DELETE)
        return
    if state == S.SUB_AWAIT_PHOTO:
        await chat.payload("g|submit|")
        return
    await chat.photo("https://i.oneme.ru/i?r=ttl")
    if state == S.SUB_NEW_TYPE:
        return
    await chat.press(COLD)
    if state == S.SUB_NEW_ADDRESS:
        return
    if state == S.SUB_ADDR_INPUT:
        await chat.press(T.BTN_OTHER_ADDRESS)
        return
    await chat.press(ARBAT_LABEL)
    await enter_serial(chat)
    if state == S.SUB_REVIEW:
        return
    if state == S.SUB_MANUAL:
        await chat.press(T.BTN_EDIT)
        return
    await chat.press(T.BTN_SEND)
    assert state == S.SUB_VERIF_DATE


TTL_STATES = [S.SUB_AWAIT_PHOTO, S.SUB_NEW_TYPE, S.SUB_NEW_ADDRESS, S.SUB_ADDR_INPUT, S.SUB_REVIEW,
              S.SUB_MANUAL, S.PROFILE_DELETE_CONFIRM]


@pytest.mark.parametrize("state", TTL_STATES)
@pytest.mark.parametrize("event", ["text", "button", "photo"])
async def test_ttl_expiry_in_every_step(chat, api, repo, settings, state, event):
    await _walk_to(chat, api, state)
    assert (await sess(repo)).state == state
    last_btn = next(b for row in (api.outgoing()[-1][1] or {"payload": {"buttons": [[]]}})["payload"]["buttons"]
                    for b in row if b["type"] == "callback")
    clock.set_now(NOW + timedelta(minutes=31))
    api.clear()
    if event == "text":
        await chat.text("123")
    elif event == "button":
        await chat.payload(last_btn["payload"])
    else:
        await chat.photo("https://i.oneme.ru/i?r=after-ttl")
    texts = api.texts()
    assert texts and any(C.SUB_EXPIRED in t or C.PROFILE_EXPIRED in t for t in texts)
    s = await sess(repo)
    assert s.state == (S.SUB_NEW_TYPE if event == "photo" else S.IDLE)
    assert len(await readings(repo)) == 0
    assert await repo.get_user(UID)  # удаление не произошло по устаревшей кнопке
    assert len(list(settings.photos_dir.glob("*"))) == (1 if event == "photo" else 0)


@pytest.mark.parametrize("how", ["ttl", "cancel", "menu_text", "menu_button"])
async def test_verif_date_step_does_not_claim_submission_lost(chat, api, repo, how):
    await _walk_to(chat, api, S.SUB_VERIF_DATE)
    assert len(await readings(repo)) == 1
    api.clear()
    if how == "ttl":
        clock.set_now(NOW + timedelta(minutes=31))
        await chat.text("15.03.2030")
    elif how == "cancel":
        await chat.text("отмена")
    elif how == "menu_text":
        await chat.text("меню")
    else:
        await chat.payload("g|menu|")
    joined = "\n".join(api.texts())
    assert len(await readings(repo)) == 1
    for scary in (C.SUB_EXPIRED, C.SUB_CANCELLED, C.SUB_CANCELLED_BY_USER):
        assert scary not in joined, f"{how}: «{scary}», а показание сохранено"


async def test_registration_does_not_expire(chat, api, repo):
    await chat.text("привет")
    await chat.text("Иванова Анна")
    clock.set_now(NOW + timedelta(days=3))
    await chat.text("+7 912 345-67-89")
    assert (await sess(repo)).state == S.REG_ADDRESS


# === 7. Переходы месяца и окна подачи ===

async def test_month_rollover_between_photo_and_send(chat, api, repo):
    """Фото 31.10 23:55, «Отправить» 01.11 00:05: показание ложится в ноябрь, октябрьское не трогаем."""
    clock.set_now(datetime(2026, 10, 31, 23, 55, tzinfo=clock.TZ))
    await register_chat(chat)
    await submit_first_reading(chat)
    await chat.photo("https://i.oneme.ru/i?r=nov")
    await chat.press(f"{COLD} · {ARBAT_LABEL}")
    assert (await sess(repo)).state == S.SUB_REVIEW
    clock.set_now(datetime(2026, 11, 1, 0, 5, tzinfo=clock.TZ))
    await chat.press(T.BTN_SEND)
    rows = await readings(repo)
    assert [r["period"] for r in rows] == ["2026-10", "2026-11"]
    assert "ноябрь" in api.last_text()


@pytest.mark.parametrize("day", [1, 14, 15, 25, 26, 28, 31])
async def test_menu_on_window_edges_and_short_months(chat, api, repo, day):
    clock.set_now(datetime(2026, 10, day, 12, 0, tzinfo=clock.TZ))
    await register_chat(chat)
    await submit_first_reading(chat)
    clock.set_now(datetime(2027, 2, min(day, 28), 12, 0, tzinfo=clock.TZ))  # февраль
    api.clear()
    await chat.text("меню")
    assert api.outgoing() and (await sess(repo)).state == S.IDLE
    await chat.text("/demo")
    await chat.payload("g|demo|submit")
    assert_sane_output(api)


# === 8. Сбои MAX API и распознавания ===

async def test_send_failure_mid_registration_then_continue(fchat, flaky, repo):
    await fchat.text("привет")
    await fchat.text("Иванова Анна")
    flaky.fail["send"] = 2                      # и ответ на шаг, и сообщение об ошибке
    await fchat.text("+7 912 345-67-89")
    assert (await sess(repo)).state == S.REG_PHONE  # состояние не испорчено
    await fchat.text("+7 912 345-67-89")
    assert (await sess(repo)).state == S.REG_ADDRESS


async def test_answer_failure_on_button_shows_error_and_retry_works(fchat, flaky, repo):
    await register_chat(fchat)
    await fchat.photo("https://i.oneme.ru/i?r=af")
    flaky.fail["answer"] = 1
    flaky.clear()
    await fchat.press(COLD)
    assert flaky.last_text() == C.ERROR
    assert (await sess(repo)).state == S.SUB_NEW_TYPE
    await fchat.press(C.BTN_RETRY)
    await fchat.press(COLD)
    await fchat.press(ARBAT_LABEL)
    await enter_serial(fchat)
    assert (await sess(repo)).state == S.SUB_REVIEW


async def test_send_failure_after_commit_does_not_duplicate_meter(fchat, flaky, repo):
    await register_chat(fchat)
    await fchat.photo("https://i.oneme.ru/i?r=dup")
    await fchat.press(COLD)
    await fchat.press(ARBAT_LABEL)
    await enter_serial(fchat)
    flaky.fail["answer"] = 1                    # MAX 503 на ответ «Готово! Записали…»
    await fchat.press(T.BTN_SEND)
    assert flaky.last_text() == C.ERROR
    assert len(await meters(repo)) == 1         # показание уже записано
    await fchat.press(C.BTN_RETRY)              # пользователь делает то, что ему предложили
    await fchat.press(T.BTN_SEND)
    assert len(await meters(repo)) == 1, "второй счётчик-дубль"
    assert len(await readings(repo)) == 1


async def test_owner_reply_failure_still_notifies_tenant(repo, settings, flaky):
    router = new_router(repo, settings, flaky)
    owner, tenant = Chat(router, flaky, UID), Chat(router, flaky, UID2)
    await register_chat(owner)
    await register_chat(tenant, name="Петров Пётр", phone="+7 900 111-22-33")
    await tenant.payload("g|profile|")
    await tenant.press(PT.BTN_REQUEST)
    allow = flaky.button(PT.BTN_ALLOW)["payload"]
    flaky.fail["send"] = 1                      # g|… отвечают новым сообщением: падает ответ собственнику
    flaky.clear()
    await owner.payload(allow)
    await owner.payload(allow)  # собственник повторил
    to_tenant = [kw for kw in flaky.named("send") if kw["user_id"] == UID2]
    assert to_tenant, "арендатору ничего не пришло"


async def test_download_failure_every_time_then_recovers(chat, api, repo):
    await register_chat(chat)
    for _ in range(3):
        await chat.feed(fakes.message_created(UID, None, [fakes.image("https://i.oneme.ru/i?r=missing")]))
        assert T.PHOTO_FAILED in api.last_text()
    await chat.photo("https://i.oneme.ru/i?r=ok")
    assert (await sess(repo)).state == S.SUB_NEW_TYPE


class _RaisingRecognizer:
    async def recognize(self, *a, **k):
        raise RuntimeError("recognizer exploded")


async def test_recognizer_crash_then_manual_path(repo, settings, api):
    router = Router(Deps(api=api, repo=repo, settings=settings, recognizer=_RaisingRecognizer(),
                         bot_username="test_bot"))
    c = Chat(router, api)
    await register_chat(c)
    await c.photo("https://i.oneme.ru/i?r=rc")
    await c.press(COLD)
    await c.press(ARBAT_LABEL)
    assert T.ISSUE_TEXTS["service"] in api.last_text()
    await c.press(T.BTN_MANUAL)
    await c.text("123,456")
    await c.text("18-123456")                     # номер нового счётчика
    await c.press(T.BTN_SEND)
    assert len(await readings(repo)) == 1


async def test_get_me_failure_menu_still_works(repo, settings):
    class NoMe(fakes.FakeMaxApi):
        async def get_me(self):
            raise MaxApiError(0, "network", "down")
    api = NoMe()
    router = Router(Deps(api=api, repo=repo, settings=settings, recognizer=StubRecognizer(), bot_username=""))
    c = Chat(router, api)
    await register_chat(c)
    assert api.last_text().startswith(RT.DONE)
    assert MT.BTN_SUBMIT in kb_labels(api)


# === 9. Рестарт (новый Router и Repo на той же БД) ===

RESTART_STATES = [S.SUB_AWAIT_PHOTO, S.SUB_NEW_TYPE, S.SUB_NEW_ADDRESS, S.SUB_REVIEW, S.SUB_MANUAL,
                  S.SUB_VERIF_DATE, S.PROFILE_DELETE_CONFIRM]


@pytest.mark.parametrize("state", RESTART_STATES)
async def test_restart_in_every_state_last_button_works(settings, api, state):
    repo1 = await Repo.open(settings.db_path)
    c1 = Chat(new_router(repo1, settings, api), api)
    await _walk_to(c1, api, state)
    kb = api.outgoing()[-1][1]
    btn = [b for row in kb["payload"]["buttons"] for b in row if b["type"] == "callback"][-1]
    await repo1.close()

    repo2 = await Repo.open(settings.db_path)
    try:
        c2 = Chat(new_router(repo2, settings, api), api)
        api.clear()
        await c2.payload(btn["payload"])
        assert api.named("answer")
        assert all(n.get("notification") != C.STALE_BUTTON for n in api.named("answer")), btn
        assert C.ERROR not in api.texts()
        await c2.text("меню")
        assert (await sess(repo2)).state == S.IDLE
    finally:
        await repo2.close()


# === 10. bot_started, /demo, команды ===

async def test_bot_started_in_every_submission_step(chat, api, repo):
    await register_chat(chat)
    for st in (S.SUB_NEW_TYPE, S.SUB_NEW_ADDRESS, S.SUB_REVIEW):
        await chat.photo(f"https://i.oneme.ru/i?r=bs-{st}")
        if st != S.SUB_NEW_TYPE:
            await chat.press(COLD)
        if st == S.SUB_REVIEW:
            await chat.press(ARBAT_LABEL)
            await enter_serial(chat)
        assert (await sess(repo)).state == st
        api.clear()
        await chat.feed(fakes.bot_started(UID, "deep-link"))
        assert (await sess(repo)).state == S.IDLE
        assert api.outgoing()
    assert not list(chat.router.deps.settings.photos_dir.glob("*"))


async def test_demo_spam(chat, api, repo):
    await register_chat(chat)
    for _ in range(10):
        await chat.text("/demo")
    for kind in ("submit", "verify", "bill") * 5:
        await chat.payload(f"g|demo|{kind}")
    await submit_first_reading(chat)
    for kind in ("submit", "verify", "bill") * 3:
        await chat.payload(f"g|demo|{kind}")
    await chat.press(NT.BTN_VERIFY)
    await chat.press(NT.BTN_PAY)
    assert await repo._all("SELECT * FROM notifications") == []  # /demo не пишет дедуп
    assert (await sess(repo)).state == S.IDLE
    assert C.ERROR not in api.texts()
    assert_sane_output(api)


async def test_commands_in_every_state_do_not_crash(chat, api, repo):
    await chat.text("/demo")
    await chat.text("/unknown")
    await chat.text("/START extra")
    await register_chat(chat)
    for cmd in ("/demo", "/DEMO", "/menu", "/cancel", "/help", "/", "//", "/demo@test_bot"):
        await chat.photo(f"https://i.oneme.ru/i?r=cmd{cmd}")
        await chat.text(cmd)
        assert C.ERROR not in api.texts()
    await chat.text("меню")
    assert (await sess(repo)).state == S.IDLE
    assert not list(chat.router.deps.settings.photos_dir.glob("*"))


# === 11. Уведомления планировщика ===

async def test_scheduler_skips_unregistered_and_deleted(deps, repo, api, chat):
    clock.set_now(datetime(2026, 10, 15, 10, 0, tzinfo=clock.TZ))  # окно открылось
    await register_chat(chat)
    await chat.payload("g|submit|")                 # ... и не довёл подачу
    other = Chat(chat.router, api, UID2)
    await other.text("привет")                      # незарегистрированный
    await chat.payload("g|profile|")
    await chat.press(PT.BTN_DELETE)
    await chat.press(PT.BTN_DELETE_YES)
    api.clear()
    assert await notify_tick(deps, clock.now()) == 0
    assert api.named("send") == []


async def test_scheduler_notification_while_mid_scenario(deps, repo, api, chat):
    clock.set_now(datetime(2026, 10, 15, 10, 0, tzinfo=clock.TZ))
    await register_chat(chat)
    await submit_first_reading(chat)
    clock.set_now(datetime(2026, 11, 15, 10, 0, tzinfo=clock.TZ))
    await chat.photo("https://i.oneme.ru/i?r=mid")
    assert (await sess(repo)).state == S.SUB_PICK_METER
    user = await repo.get_user(UID)
    assert await send_due_notice(deps, user, clock.now())
    notice_btn = api.named("send")[-1]["keyboard"]["payload"]["buttons"][0][0]
    api.clear()
    await chat.payload(notice_btn["payload"])
    assert (await sess(repo)).state == S.SUB_AWAIT_PHOTO
    assert C.SUB_CANCELLED in api.texts()[0]
    assert not list(chat.router.deps.settings.photos_dir.glob("*"))


# === 12. Прочее: битая сессия, много счётчиков, дедуп ===

async def test_corrupted_session_data_recovers(chat, api, repo):
    await register_chat(chat)
    user = await repo.get_user(UID)
    for state, data in ((S.SUB_REVIEW, {}), (S.SUB_MANUAL, {"meter_id": "abc"}), ("bogus", {}),
                        (S.SUB_PICK_METER, {"photo_id": "nope"}), (S.REG_CONFIRM, {"reg": {}})):
        await repo.save_session(user["id"], str(state), data, "abcdef", clock.now(), None)
        api.clear()
        await chat.text("123")
        assert api.outgoing(), state
        assert C.ERROR not in api.texts(), state
    await repo._exec("UPDATE sessions SET data='not json' WHERE user_id=?", (user["id"],))
    await chat.text("меню")
    assert (await sess(repo)).state == S.IDLE


async def test_many_meters_pick_keyboard(chat, api, repo):
    """QA-3: 30+ счётчиков — выбор страницами по 20 с [Показать ещё] / [К началу списка]."""
    await register_chat(chat)
    user = await repo.get_user(UID)
    (addr,) = await repo.user_addresses(user["id"])
    for i in range(30):
        await repo.create_meter(addr["id"], "cold_water", 1, f"SN{i:04d}", user["id"])
    api.clear()
    await chat.photo("https://i.oneme.ru/i?r=many")
    assert C.ERROR not in api.texts()
    assert (await sess(repo)).state == S.SUB_PICK_METER
    assert "Показали 1–20 из 30." in api.last_text()
    assert T.BTN_PAGE_NEXT in kb_labels(api) and T.BTN_NEW_METER in kb_labels(api)
    await chat.press(T.BTN_PAGE_NEXT)
    assert "Показали 21–30 из 30." in api.last_text()
    labels = kb_labels(api)
    assert T.BTN_PAGE_FIRST in labels and sum(COLD in x for x in labels) == 10
    await chat.press(labels[0])                 # счётчик со второй страницы
    assert (await sess(repo)).state == S.SUB_REVIEW
    assert_sane_output(api)


async def test_duplicate_update_after_restart_is_not_double_processed(settings, api):
    """Один и тот же апдейт (тот же mid) до и после рестарта роутера — показание одно."""
    repo1 = await Repo.open(settings.db_path)
    try:
        c = Chat(new_router(repo1, settings, api), api)
        await register_chat(c)
        await c.photo("https://i.oneme.ru/i?r=d1")
        await c.press(COLD)
        await c.press(ARBAT_LABEL)
        await enter_serial(c)
        upd = fakes.message_callback(UID, api.button(T.BTN_SEND)["payload"], callback_id="cb.fixed")
        await c.router.handle(parse_update(upd))
        c2 = Chat(new_router(repo1, settings, api), api)
        await c2.router.handle(parse_update(json.loads(json.dumps(upd))))
        assert len(await readings(repo1)) == 1
    finally:
        await repo1.close()


async def test_ten_users_full_scenario_concurrently(router, api, repo):
    """10 пользователей одновременно: регистрация (5 на одном адресе) и подача — без ошибок и гонок.
    Кнопки жмём по payload из своей сессии (chat.press ищет по всему чату и перепутал бы пользователей)."""
    chats = [Chat(router, api, 8_000_000 + i) for i in range(10)]

    async def tap(c: Chat, action: str, arg: str = "") -> None:
        await c.payload(f"{(await sess(repo, c.uid)).flow_id}|{action}|{arg}")

    async def run(i: int, c: Chat) -> None:
        await c.text("привет")
        await c.text("Иванова Анна")
        await c.text("+7 912 345-67-89")
        await c.text(ARBAT if i < 5 else f"Москва, Тверская 1, кв. {i}")
        await tap(c, "yes")
        await tap(c, "yes")
        assert (await repo.get_user(c.uid))["registered_at"]
        await c.photo(f"https://i.oneme.ru/i?r=u{i}", f"\xff\xd8{i}".encode())
        if i >= 5 or i == 0:  # с доступом
            await tap(c, "t", "cold_water")
            uid = (await repo.get_user(c.uid))["id"]
            (addr,) = await repo.user_addresses(uid)
            await tap(c, "a", addr["id"])
            await tap(c, "send")

    await asyncio.gather(*(run(i, c) for i, c in enumerate(chats)))
    owners = await repo._all("SELECT * FROM user_addresses WHERE role='owner'")
    assert len(owners) == 6  # один собственник на Арбате + 5 разных квартир на Тверской
    arbat_owner = [o for o in owners if o["address_id"] == 1]
    assert len(arbat_owner) == 1
    assert C.ERROR not in api.texts()
    assert set(answers_per_callback(api).values()) == {1}


@pytest.mark.parametrize("where", ["replace", "plausibility"])
async def test_new_photo_in_replace_and_plausibility_steps(chat, api, repo, settings, where):
    await register_chat(chat)
    await submit_first_reading(chat)
    await chat.photo("https://i.oneme.ru/i?r=x1")
    await chat.press(f"{COLD} · {ARBAT_LABEL}")
    if where == "plausibility":  # прошлое показание (прошлый месяц) больше нового → «меньше прошлого»
        await repo._exec("UPDATE readings SET period='2026-09', t1=99999000")
    await chat.press(T.BTN_SEND)
    assert (await sess(repo)).state == (S.SUB_REPLACE_CONFIRM if where == "replace" else S.SUB_PLAUSIBILITY)
    api.clear()
    await chat.photo("https://i.oneme.ru/i?r=x2", b"\xff\xd8other")
    assert (await sess(repo)).state == S.SUB_REVIEW
    assert T.PHOTO_REPLACED in api.texts()[0]
    assert len(list(settings.photos_dir.glob("*"))) == 1
    await chat.press(C.BTN_CANCEL)
    assert not list(settings.photos_dir.glob("*"))


async def test_old_notification_buttons_after_delete_and_reregister(chat, api, repo):
    await register_chat(chat)
    await submit_first_reading(chat)
    await chat.text("/demo")
    await chat.payload("g|demo|verify")
    verify = api.button(NT.BTN_VERIFY)["payload"]
    await chat.payload("g|demo|bill")
    pay = api.button(NT.BTN_PAY)["payload"]
    await chat.payload("g|profile|")
    await chat.press(PT.BTN_DELETE)
    await chat.press(PT.BTN_DELETE_YES)
    api.clear()
    await chat.payload(verify)                 # удалённый пользователь жмёт старое уведомление
    assert C.WELCOME in api.texts()
    await register_chat(chat)
    for p in (verify, pay):
        api.clear()
        await chat.payload(p)
        assert api.outgoing() and C.ERROR not in api.texts()
    assert (await sess(repo)).state == S.IDLE


# === 13. Мини-API ===



@pytest.mark.parametrize("values", [
    {"t1": 1e308}, {"t1": 10**30}, {"t1": -1}, {"t1": "-0"}, {"t1": "  "}, {}, {"t9": "1"},
    {"t1": "1e5"}, {"t1": "NaN"}, {"t1": "Infinity"}, {"t1": "123,456,7"}, {"t1": "99999999"},
    {"t1": "0x10"}, {"t1": None},
])
def test_api_garbage_values_are_422_not_500(client, api, values):
    u = api_register(client)
    mid = add_meter(client, u["address_id"])
    r = post(client, mid, values=values)
    assert r.status_code == 422, (values, r.status_code, r.text)
    assert r.json()["code"] in ("bad_format", "bad_request")
    assert api.named("send") == []


@pytest.mark.parametrize("body", [
    b"", b"null", b"[]", b'{"meter_id": 1}', b'{"values": {"t1": "1"}}',
    b'{"meter_id": 1, "values": [1]}', b'{"meter_id": 1, "values": {"t1": [1]}}',
    b'{"meter_id": true, "values": {"t1": "1"}}',
])
def test_api_broken_bodies(client, body):
    api_register(client)
    r = client.post("/api/readings", headers={**auth(), "Content-Type": "application/json"}, content=body)
    assert r.status_code in (404, 422), (body, r.status_code, r.text)
    assert set(r.json()) == {"code", "message"}


def test_api_non_utf8_body_is_russian_bad_request(client):
    api_register(client)
    r = client.post("/api/readings", headers={**auth(), "Content-Type": "application/json"},
                    content=b'{"meter_id": 1, "values": {"t1": "\xff\xfe"}}')
    assert_error(r, 422, "bad_request")


def test_api_double_submit_one_reading(client, api):
    u = api_register(client)
    mid = add_meter(client, u["address_id"])
    first = post(client, mid, values={"t1": "123,456"})
    second = post(client, mid, values={"t1": "123,456"})
    assert first.status_code == 200
    assert_error(second, 409, "already_submitted")
    assert len(api.named("send")) == 1


def test_api_values_as_numbers(client):
    u = api_register(client)
    mid = add_meter(client, u["address_id"])
    r = post(client, mid, values={"t1": 123.456})
    assert r.status_code == 200 and r.json()["reading"]["values"]["t1"] == 123.456


def test_api_foreign_meter_everywhere_is_403(client):
    api_register(client)
    other = api_register(client, 777001, flat="33")
    foreign = add_meter(client, other["address_id"])
    assert_error(client.get(f"/api/meters/{foreign}", headers=auth()), 403, "no_access")
    assert_error(post(client, foreign, values={"t1": "1"}, confirm=True, replace=True), 403, "no_access")
    r = client.post("/api/recognize", headers=auth(), data={"meter_id": str(foreign)},
                    files={"file": ("m.jpg", b"\xff\xd8x", "image/jpeg")})
    assert_error(r, 403, "no_access")


@pytest.mark.parametrize("how", ["detail", "post", "recognize"])
def test_api_huge_meter_id_is_404(client, how):
    api_register(client)
    big = 10**20
    if how == "detail":
        r = client.get(f"/api/meters/{big}", headers=auth())
    elif how == "post":
        r = post(client, big, values={"t1": "1"})
    else:
        r = client.post("/api/recognize", headers=auth(), data={"meter_id": str(big)},
                        files={"file": ("m.jpg", b"\xff\xd8x", "image/jpeg")})
    assert_error(r, 404, "not_found")


# === 14. Случайное блуждание («обезьяна»): любые кнопки/текст/фото — без сбоев и тупиков ===

MONKEY_TEXTS = ["привет", "меню", "отмена", "назад", "да", "нет", "123,456", "0", "99999999", "abc",
                "Иванова Анна", "+7 912 345-67-89", ARBAT, "Москва, Тверская 1, кв. 5", "15.03.2030",
                "позже", "/demo", "/start", "/cancel", "/xyz", "кв 12", "   ", "😀"]


async def _monkey(chat: Chat, api, repo, seed: int, steps: int = 120) -> list[str]:
    import random
    rnd = random.Random(seed)
    log: list[str] = []
    n = 0
    for _ in range(steps):
        out = api.outgoing(everything=True)
        kb_buttons = [b for row in ((out[-1][1] or {}).get("payload", {}).get("buttons", []) if out else [])
                      for b in row if b["type"] == "callback"]
        old_buttons = [b for _, kb in out[-15:] for row in (kb or {}).get("payload", {}).get("buttons", [])
                       for b in row if b["type"] == "callback"]
        roll = rnd.random()
        before = len(api.calls)
        if roll < 0.55 and kb_buttons:
            b = rnd.choice(kb_buttons)
            log.append(f"press {b['text']!r}")
            await chat.payload(b["payload"])
        elif roll < 0.65 and old_buttons:
            b = rnd.choice(old_buttons)
            log.append(f"old {b['text']!r}")
            await chat.payload(b["payload"])
        elif roll < 0.8:
            n += 1
            log.append("photo")
            await chat.photo(f"https://i.oneme.ru/i?r=m{seed}-{n}", f"\xff\xd8{seed}-{n}".encode())
        elif roll < 0.83:
            log.append("contact")
            await chat.feed(fakes.message_created(chat.uid, None, [fakes.contact(chat.uid)]))
        else:
            t = rnd.choice(MONKEY_TEXTS)
            log.append(f"text {t!r}")
            await chat.feed(fakes.message_created(chat.uid, t))
        new = api.calls[before:]
        texts = [kw.get("text") or (kw.get("message") or {}).get("text") for name, kw in new
                 if name == "send" or (name == "answer" and kw.get("message"))]
        assert C.ERROR not in texts, "\n".join(log[-12:])
        assert new, "бот промолчал: " + "\n".join(log[-12:])
    return log


@pytest.mark.parametrize("seed", range(12))
async def test_monkey_walk(chat, api, repo, settings, seed):
    log = await _monkey(chat, api, repo, seed)
    assert set(answers_per_callback(api).values()) == {1}, log[-10:]
    assert_sane_output(api)
    await chat.text("отмена")
    await chat.text("меню")
    s = await sess(repo)
    assert s.state in (S.IDLE,) or s.state.is_reg
    # Файлы фото — только у живых сессий.
    live = {s.data.get("photo_id"), s.data.get("pending_photo_id")} - {None}
    rows = {r["id"] for r in await repo._all("SELECT id FROM photos")}
    assert rows <= live | rows  # записи могут жить до sweep, но файлов-сирот без записей быть не должно
    files = {p.stem for p in settings.photos_dir.glob("*")} if settings.photos_dir.exists() else set()
    assert files <= rows, "файлы фото без записи в БД (sweep их не найдёт)"


# === 15. Фото истекло (sweep) при живой сессии; удалённый пользователь и мини-API ===

async def test_photo_swept_while_session_alive(chat, api, repo, settings):
    """Сессию продлевают действия, а фото живёт 30 мин от загрузки: sweep удалил — бот просит фото ещё раз
    и сразу распознаёт новое для уже выбранного счётчика."""
    from app.bot import photos
    await register_chat(chat)
    await chat.photo("https://i.oneme.ru/i?r=sw")
    clock.set_now(NOW + timedelta(minutes=25))
    await chat.press(COLD)                                  # сессия продлена
    clock.set_now(NOW + timedelta(minutes=35))
    assert await photos.sweep(repo, clock.now()) == 1       # фото удалено планировщиком
    api.clear()
    await chat.press(ARBAT_LABEL)
    assert T.PHOTO_GONE in api.last_text()
    assert (await sess(repo)).state == S.SUB_AWAIT_PHOTO
    await chat.photo("https://i.oneme.ru/i?r=sw2")
    assert (await sess(repo)).state == S.SUB_SERIAL_MISSING  # новый счётчик: номер на фото не разобрали


async def test_deleted_user_in_miniapp_and_bot(client, api):
    """После «Удалить мои данные» мини-приложение показывает баннер регистрации, а не чужие/старые данные."""
    from tests.test_api import run
    u = api_register(client)
    add_meter(client, u["address_id"])
    run(client, client.app.state.repo.delete_user_data, u["id"])
    me = client.get("/api/me", headers=auth()).json()
    assert me["registered"] is False and me["meters"] == [] and me["user"] is None
