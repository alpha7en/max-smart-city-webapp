"""Регистрация: сценарии R1–R23 (SPEC_REVIEW, S1) через настоящий роутер и FakeMaxApi."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from app import clock
from app.bot.ctx import Deps
from app.bot.flows.registration import best_candidate, parse_flat
from app.bot.router import HOOKS, Router
from app.bot.session import load_session
from app.bot.states import S
from app.bot.texts import common as C
from app.bot.texts import profile as PT
from app.bot.texts import registration as RT
from app.domain.addresses import AddressCandidate, button_text, format_full
from app.integrations.address_service import AddressService
from app.integrations.recognizer import StubRecognizer
from app.repo import Repo
from tests import fakes
from tests.conftest import Chat

UID, UID2 = 5273381, 7000001
ADDRESS = "Москва, Арбат 47к1, кв. 32"


# --- Помощники ---

def buttons(kb: dict | None) -> list[dict]:
    return [b for row in (kb or {}).get("payload", {}).get("buttons", []) for b in row]


def labels(kb: dict | None) -> list[str]:
    return [b["text"] for b in buttons(kb)]


def last_kb(api) -> dict | None:
    return api.outgoing()[-1][1]


async def session(repo, uid: int = UID):
    user = await repo.get_user(uid)
    return await load_session(repo, user["id"])


async def to_confirm(chat: Chat, name: str = "Иванова Анна Сергеевна", phone: str = "+7 912 345-67-89",
                     address: str = ADDRESS) -> None:
    await chat.text("привет")
    await chat.text(name)
    await chat.text(phone)
    await chat.text(address)
    await chat.press(RT.BTN_YES)


async def register(chat: Chat, **kw) -> None:
    await to_confirm(chat, **kw)
    await chat.press(RT.BTN_ALL_OK)


def dadata(street: str, house: str, flat: str | None = None, fias: str | None = None,
           flat_count: int | None = None) -> AddressCandidate:
    c = AddressCandidate(full_text="", region="г Москва", locality="г Москва", street=street, house=house,
                         block=None, flat=flat, house_fias_id=fias or f"fias-{street}-{house}",
                         house_flat_count=flat_count, status="verified_house", source="dadata")
    c.full_text = format_full(c)
    return c


class FakeAddresses:
    """AddressService с заданными ответами DaData (verified=True)."""
    verified = True

    def __init__(self, cands: list[AddressCandidate]):
        self.cands = cands
        self.local = AddressService()

    async def suggest(self, text: str) -> list[AddressCandidate]:
        return list(self.cands)

    def parse_local(self, text: str) -> list[AddressCandidate]:
        return self.local.parse_local(text)


# --- R1–R5: приветствие и имя ---

async def test_r1_first_message_welcome_then_name(chat, api, repo):
    await chat.text("привет")
    assert api.texts() == [C.WELCOME, RT.ASK_NAME]
    assert "сфотографировать счётчик" in C.WELCOME and "персональных данных" in RT.ASK_NAME
    assert (await session(repo)).state == S.REG_NAME
    assert labels(last_kb(api)) == [RT.BTN_ITS_ME.format(name="Анна Иванова")]


@pytest.mark.parametrize("payload", [None, "meter_12"])
async def test_r2_bot_started_same_as_text(chat, api, repo, payload):
    await chat.feed(fakes.bot_started(UID, payload))
    assert api.texts() == [C.WELCOME, RT.ASK_NAME]
    assert (await session(repo)).state == S.REG_NAME


async def test_r3_name_title_case_then_phone(chat, api, repo):
    await chat.text("привет")
    await chat.text("иванова  анна сергеевна")
    s = await session(repo)
    assert s.state == S.REG_PHONE and s.data["reg"]["name"] == "Иванова Анна Сергеевна"
    assert api.last_text() == RT.ASK_PHONE.format(name="Анна")
    kb = buttons(last_kb(api))
    assert (kb[0]["type"], kb[0]["text"]) == ("request_contact", RT.BTN_SHARE_PHONE)
    assert kb[-1]["text"] == C.BTN_BACK and kb[-1]["type"] == "callback"


@pytest.mark.parametrize("text,error", [
    ("Ivanova Anna", "latin"), ("Анна", "too_few"), ("Иванова Анна 2", "digits"),
    (" ".join(["Ааааааааааааааааааааааааааа"] * 4), "too_long"), ("Иванова Анна!", "chars"),
])
async def test_r4_name_errors(chat, api, repo, text, error):
    await chat.text("привет")
    await chat.text(text)
    assert api.last_text() == RT.NAME_ERRORS[error]
    assert (await session(repo)).state == S.REG_NAME
    assert labels(last_kb(api)) == [RT.BTN_ITS_ME.format(name="Анна Иванова")]  # не тупик: кнопка шага на месте


async def test_r5_its_me_only_for_valid_profile_name(chat, api, repo):
    await chat.text("привет")
    await chat.press(RT.BTN_ITS_ME.format(name="Анна Иванова"))
    s = await session(repo)
    assert s.state == S.REG_PHONE and s.data["reg"]["name"] == "Иванова Анна"


@pytest.mark.parametrize("first,last", [("Анна", None), ("Anna", "Ivanova"), ("Анна", "И")])
async def test_r5_no_its_me_for_bad_profile_name(chat, api, first, last):
    upd = fakes.message_created(UID, "привет")
    upd["message"]["sender"] = fakes.user(UID, first, last)
    await chat.feed(upd)
    assert api.last_text() == RT.ASK_NAME and last_kb(api) is None


# --- R6–R8: телефон и «Назад» ---

async def _at_phone(chat):
    await chat.text("привет")
    await chat.text("Иванова Анна")


async def test_r6_own_contact_verified(chat, api, repo):
    await _at_phone(chat)
    await chat.feed(fakes.message_created(UID, None, [fakes.contact(UID)]))
    s = await session(repo)
    assert s.state == S.REG_ADDRESS and s.data["reg"]["phone"] == "+79123456789"
    assert s.data["reg"]["phone_verified"] is True
    assert api.last_text() == RT.ASK_ADDRESS


async def test_r6_foreign_contact_rejected(chat, api, repo):
    await _at_phone(chat)
    await chat.feed(fakes.message_created(UID, None, [fakes.contact(999)]))
    assert api.last_text() == RT.NOT_YOUR_CONTACT
    assert (await session(repo)).state == S.REG_PHONE
    assert RT.BTN_SHARE_PHONE in labels(last_kb(api))


async def test_r6_contact_without_max_info_is_manual(chat, api, repo):
    await _at_phone(chat)
    att = fakes.contact(UID, phone="79001112233\r\nTEL;TYPE=home:74951234567")  # несколько TEL → первый
    del att["payload"]["max_info"]
    await chat.feed(fakes.message_created(UID, None, [att]))
    reg = (await session(repo)).data["reg"]
    assert (reg["phone"], reg["phone_verified"]) == ("+79001112233", False)


@pytest.mark.parametrize("text,phone,error", [
    ("8 (912) 345-67-89", "+79123456789", None),
    ("9123456789", "+79123456789", None),
    ("12345", None, RT.PHONE_ERROR),
    ("+1 202 555 0100", None, RT.PHONE_FOREIGN),
])
async def test_r7_manual_phone(chat, api, repo, text, phone, error):
    await _at_phone(chat)
    await chat.text(text)
    s = await session(repo)
    if error:
        assert api.last_text() == error and s.state == S.REG_PHONE
    else:
        assert s.data["reg"]["phone"] == phone and s.data["reg"]["phone_verified"] is False


async def test_r8_back_steps_keep_previous_answers(chat, api, repo):
    await _at_phone(chat)
    await chat.press(C.BTN_BACK)
    assert (await session(repo)).state == S.REG_NAME
    assert api.last_text() == RT.ASK_NAME_AGAIN.format(name="Иванова Анна")
    await chat.press(RT.BTN_KEEP)
    assert (await session(repo)).state == S.REG_PHONE
    await chat.text("89123456789")
    await chat.press(C.BTN_BACK)  # REG_ADDRESS → REG_PHONE
    assert (await session(repo)).state == S.REG_PHONE
    assert "+7 912 345-67-89" in api.last_text() and RT.BTN_KEEP in labels(last_kb(api))
    await chat.text("назад")  # набранное вручную «назад» тоже работает
    assert (await session(repo)).state == S.REG_NAME


# --- R9–R14: адрес ---

async def _at_address(chat):
    await _at_phone(chat)
    await chat.text("+7 912 345-67-89")


async def test_r9_one_candidate_with_flat_to_confirm(chat, api, repo):
    await _at_address(chat)
    await chat.text(ADDRESS)
    assert api.last_text().startswith(RT.ADDRESS_ONE.split("\n")[0]) and "Арбат, д. 47" in api.last_text()
    assert labels(last_kb(api)) == [RT.BTN_YES, RT.BTN_NO_OTHER, C.BTN_BACK]
    assert (await session(repo)).state == S.REG_ADDRESS_PICK
    await chat.press(RT.BTN_YES)
    s = await session(repo)
    assert s.state == S.REG_CONFIRM and s.data["reg"]["raw_address"] == ADDRESS
    text = api.last_text()
    assert text.startswith(RT.CONFIRM.format(name="Иванова Анна", phone="+7 912 345-67-89",
                                             address="г. Москва, Арбат, д. 47, корп. 1, кв. 32"))
    assert labels(last_kb(api)) == [RT.BTN_ALL_OK, RT.BTN_EDIT_NAME, RT.BTN_EDIT_PHONE, RT.BTN_EDIT_ADDRESS]


async def test_r9_no_and_back_on_pick(chat, api, repo):
    await _at_address(chat)
    await chat.text(ADDRESS)
    await chat.press(RT.BTN_NO_OTHER)
    assert (await session(repo)).state == S.REG_ADDRESS and api.last_text() == RT.ADDRESS_RETRY
    await chat.text(ADDRESS)
    await chat.press(C.BTN_BACK)
    assert (await session(repo)).state == S.REG_PHONE


async def test_r10_flat_step(chat, api, repo):
    await _at_address(chat)
    await chat.text("Москва, Арбат 47к1")
    await chat.press(RT.BTN_YES)
    assert (await session(repo)).state == S.REG_FLAT
    assert labels(last_kb(api)) == [RT.BTN_PRIVATE_HOUSE, C.BTN_BACK]
    await chat.text("квартира тридцать")
    assert api.last_text() == RT.FLAT_ERROR
    await chat.text("кв. 32")
    s = await session(repo)
    assert s.state == S.REG_CONFIRM and s.data["reg"]["address"]["flat"] == "32"
    assert "корп. 1, кв. 32" in api.last_text()


async def test_r10_private_house_and_back(chat, api, repo):
    await _at_address(chat)
    await chat.text("Москва, Арбат 47к1")
    await chat.press(RT.BTN_YES)
    await chat.press(C.BTN_BACK)
    assert (await session(repo)).state == S.REG_ADDRESS_PICK
    await chat.press(RT.BTN_YES)
    await chat.press(RT.BTN_PRIVATE_HOUSE)
    s = await session(repo)
    assert s.state == S.REG_CONFIRM and s.data["reg"]["address"]["flat"] is None


async def test_r11_several_candidates(chat, api, repo, deps):
    cands = [dadata("ул Ленина", "5", flat_count=40), dadata("ул Ленина", "7"), dadata("пр-кт Ленинский", "5")]
    deps.addresses = FakeAddresses(cands)
    await _at_address(chat)
    await chat.text("Ленина")
    assert api.last_text() == RT.ADDRESS_MANY
    assert labels(last_kb(api)) == [button_text(c) for c in cands] + [RT.BTN_NOT_MINE, C.BTN_BACK]
    assert all(len(t) <= 40 for t in labels(last_kb(api)))
    await chat.press(button_text(cands[0]))
    assert (await session(repo)).state == S.REG_FLAT
    await chat.text("500")
    text = api.last_text()
    assert RT.FLAT_WARNING.format(flats="40 квартир") in text and RT.LOCAL_NOTE not in text
    assert text.startswith(RT.CONFIRM.split("\n")[0]) and text.endswith(RT.FLAT_WARNING.format(flats="40 квартир"))


async def test_r11_house_match_picks_single(chat, api, repo, deps):
    deps.addresses = FakeAddresses([dadata("ул Ленина", "5"), dadata("ул Ленина", "7")])
    await _at_address(chat)
    await chat.text("Москва, Ленина 7, кв 3")
    assert api.last_text().startswith(RT.ADDRESS_ONE.split("\n")[0]) and "Ленина, д. 7" in api.last_text()


async def test_r12_not_found_save_as_is(chat, api, repo, deps):
    deps.addresses = FakeAddresses([])
    await _at_address(chat)
    await chat.text(ADDRESS)
    assert api.last_text().startswith(RT.ADDRESS_NOT_FOUND_ASIS.split("\n")[0])
    assert labels(last_kb(api)) == [RT.BTN_ASIS, RT.BTN_FIX, C.BTN_BACK]
    await chat.press(RT.BTN_FIX)
    assert api.last_text() == RT.ADDRESS_RETRY
    await chat.text(ADDRESS)
    await chat.press(RT.BTN_ASIS)
    s = await session(repo)
    assert s.state == S.REG_CONFIRM and s.data["reg"]["address"]["status"] == "unverified"
    assert "ФИАС" not in api.last_text()  # о сверке уже сказали в «Не нашли такой адрес…»


async def test_r12_no_house_repeats_with_example(chat, api, repo):
    await _at_address(chat)
    await chat.text("Ленина")
    assert api.last_text() == RT.ADDRESS_NOT_FOUND and "Например" in RT.ADDRESS_NOT_FOUND
    assert labels(last_kb(api)) == [C.BTN_BACK]
    assert (await session(repo)).state == S.REG_ADDRESS


async def test_r13_local_address_marked_once(chat, api):
    """«Не сверен с ФИАС» — один раз, на «Мы поняли так»; ни на подтверждении, ни в «Записали»."""
    await _at_address(chat)
    await chat.text(ADDRESS)
    assert api.last_text().endswith(f"Верно?\n\n> {RT.LOCAL_NOTE}")  # демо-оговорка — последней цитатой
    await chat.press(RT.BTN_YES)
    assert api.last_text().startswith(RT.CONFIRM.split("\n")[0]) and "ФИАС" not in api.last_text()
    await chat.press(RT.BTN_ALL_OK)
    saved = api.named("answer")[-1]["message"]["text"]
    assert saved.startswith(RT.SAVED.split("\n")[0]) and "ФИАС" not in saved


async def test_r14_text_on_pick_is_new_search(chat, api, repo):
    await _at_address(chat)
    await chat.text(ADDRESS)
    await chat.text("Москва, Арбат 10, кв 5")
    assert api.last_text().startswith(RT.ADDRESS_ONE.split("\n")[0]) and "Арбат, д. 10" in api.last_text()
    await chat.text("да")
    assert (await session(repo)).data["reg"]["address"]["house"] == "10"


# --- R15–R18: подтверждение ---

async def test_r15_edit_from_confirm_returns_to_confirm(chat, api, repo):
    await to_confirm(chat)
    await chat.press(RT.BTN_EDIT_NAME)
    assert (await session(repo)).state == S.REG_NAME and C.BTN_BACK in labels(last_kb(api))
    await chat.text("Петрова Мария")
    s = await session(repo)
    assert s.state == S.REG_CONFIRM
    assert s.data["reg"]["name"] == "Петрова Мария" and s.data["reg"]["phone"] == "+79123456789"
    assert "editing" not in s.data["reg"]

    await chat.press(RT.BTN_EDIT_PHONE)
    await chat.press(C.BTN_BACK)
    assert (await session(repo)).state == S.REG_CONFIRM

    await chat.press(RT.BTN_EDIT_ADDRESS)
    assert (await session(repo)).state == S.REG_ADDRESS
    await chat.press(C.BTN_BACK)
    assert (await session(repo)).state == S.REG_CONFIRM
    await chat.press(RT.BTN_EDIT_ADDRESS)
    await chat.text("Москва, Арбат 10, кв 5")
    await chat.press(RT.BTN_YES)
    s = await session(repo)
    assert s.state == S.REG_CONFIRM and s.data["reg"]["address"]["house"] == "10"
    assert s.data["reg"]["name"] == "Петрова Мария"


async def test_r16_all_ok_registers(chat, api, repo):
    await to_confirm(chat)
    api.clear()
    await chat.press(RT.BTN_ALL_OK)
    user = await repo.get_user(UID)
    assert user["registered_at"] and user["full_name"] == "Иванова Анна Сергеевна"
    assert (user["phone"], user["phone_verified"]) == ("+79123456789", 0)
    (addr,) = await repo.user_addresses(user["id"])
    assert (addr["role"], addr["access"], addr["label"]) == ("owner", "granted", "Арбат 47к1, кв 32")
    assert addr["raw_input"] == ADDRESS and addr["source"] == "local"
    assert len(await repo.unpaid_bills(user["id"])) == 1
    assert (await session(repo)).state == S.IDLE
    ans = api.named("answer")[0]["message"]
    assert ans["text"].startswith(RT.SAVED.split("\n")[0]) and ans["attachments"] == []  # сводка без кнопок
    assert api.last_text().startswith(RT.DONE)


async def test_r17_second_user_same_address_gets_no_access(router, api, repo):
    await register(Chat(router, api, UID))
    tenant = Chat(router, api, UID2)
    await to_confirm(tenant, name="Петров Пётр")
    api.clear()
    await tenant.press(RT.BTN_ALL_OK)
    user = await repo.get_user(UID2)
    (addr,) = await repo.user_addresses(user["id"])
    assert (addr["role"], addr["access"]) == ("tenant", "pending")
    assert await repo.unpaid_bills(user["id"]) == []
    texts = api.texts()
    no_access = next(t for t in texts if "уже зарегистрирован собственник" in t)
    assert no_access.startswith(RT.DONE_SHORT) and "Арбат 47к1, кв 32" in no_access
    kb = next(k for t, k in api.outgoing() if t == no_access)
    assert labels(kb) == [PT.BTN_REQUEST, PT.BTN_DEMO_GRANT, PT.BTN_PROFILE, C.BTN_MENU]  # DEMO_MODE=true
    assert texts.index(no_access) < len(texts) - 1  # затем меню-дашборд
    assert (await session(repo, UID2)).state == S.IDLE


async def test_r18_double_all_ok(chat, api, repo):
    await to_confirm(chat)
    await chat.press(RT.BTN_ALL_OK)
    api.clear()
    await chat.press(RT.BTN_ALL_OK)
    assert api.named("answer")[0]["notification"] == C.STALE_BUTTON
    user = await repo.get_user(UID)
    assert len(await repo.user_addresses(user["id"])) == 1


# --- R19–R23: глобальные правила внутри регистрации ---

async def test_r19_start_in_phone_then_restart_keeps_photo(chat, api, repo):
    await chat.photo("https://i/1")
    await chat.text("Иванова Анна")
    pid = (await session(repo)).data["pending_photo_id"]
    api.clear()
    await chat.text("/start")
    assert api.texts()[0] == C.CONTINUE_REG and api.texts()[1].startswith(RT.ASK_PHONE[:20])
    await chat.press(C.BTN_RESTART)
    s = await session(repo)
    assert s.state == S.REG_NAME and not s.data.get("reg") and s.data["pending_photo_id"] == pid
    assert await repo.get_photo(pid) is not None


async def test_r20_pending_photo_goes_to_submission(chat, api, repo, monkeypatch):
    calls = []

    async def with_photo(ctx, photo_id=None, **_):
        calls.append(photo_id)
        await ctx.reply("подача")

    monkeypatch.setitem(HOOKS, "submission.with_photo", with_photo)
    await _at_address(chat)
    await chat.photo("https://i/1")
    assert api.last_text() == f"{C.PHOTO_SAVED}\n\n{RT.ASK_ADDRESS}"
    pid = (await session(repo)).data["pending_photo_id"]
    await chat.text(ADDRESS)
    await chat.press(RT.BTN_YES)
    await chat.press(RT.BTN_ALL_OK)
    assert calls == [pid]
    assert api.last_text() == f"{RT.DONE_PHOTO}\n\nподача"


async def test_r20_pending_photo_expired(chat, api, repo, monkeypatch, frozen_clock):
    calls = []

    async def with_photo(ctx, photo_id=None, **_):
        calls.append(photo_id)

    monkeypatch.setitem(HOOKS, "submission.with_photo", with_photo)
    await chat.photo("https://i/1")
    pid = (await session(repo)).data["pending_photo_id"]
    path = Path((await repo.get_photo(pid))["path"])
    await chat.text("Иванова Анна")
    await chat.text("+7 912 345-67-89")
    await chat.text(ADDRESS)
    await chat.press(RT.BTN_YES)
    clock.set_now(frozen_clock + timedelta(hours=25))
    await chat.press(RT.BTN_ALL_OK)
    assert calls == [] and api.last_text().startswith(RT.DONE_PHOTO_EXPIRED)
    assert await repo.get_photo(pid) is None and not path.exists()


async def test_r20_tenant_pending_photo_deleted(router, api, repo):
    await register(Chat(router, api, UID))
    tenant = Chat(router, api, UID2)
    await tenant.photo("https://i/2")
    pid = (await session(repo, UID2)).data["pending_photo_id"]
    await tenant.text("Петров Пётр")
    await tenant.text("+7 900 111-22-33")
    await tenant.text(ADDRESS)
    await tenant.press(RT.BTN_YES)
    await tenant.press(RT.BTN_ALL_OK)
    assert await repo.get_photo(pid) is None


async def test_r21_sticker_and_commands(chat, api, repo):
    await chat.text("привет")
    await chat.feed(fakes.message_created(UID, None, [fakes.sticker()]))
    assert api.last_text() == f"{C.UNSUPPORTED}\n\n{RT.ASK_NAME}"
    await chat.text("/demo")
    assert api.last_text().startswith((C.UNKNOWN_COMMAND, C.FINISH_REG_FIRST))
    await chat.text("/foo")
    assert api.last_text().startswith(C.UNKNOWN_COMMAND)
    assert (await session(repo)).state == S.REG_NAME


async def test_r22_restart_in_address_step(settings, api):
    repo1 = await Repo.open(settings.db_path)
    c1 = Chat(Router(Deps(api, repo1, settings, StubRecognizer(), bot_username="test_bot")), api)
    await c1.text("привет")
    await c1.text("Иванова Анна")
    await c1.text("+7 912 345-67-89")
    await repo1.close()

    repo2 = await Repo.open(settings.db_path)
    c2 = Chat(Router(Deps(api, repo2, settings, StubRecognizer(), bot_username="test_bot")), api)
    await c2.text(ADDRESS)
    await c2.press(RT.BTN_YES)
    await c2.press(RT.BTN_ALL_OK)
    user = await repo2.get_user(UID)
    assert user["registered_at"] and user["full_name"] == "Иванова Анна"
    await repo2.close()


async def test_r23_global_button_in_registration(chat, api, repo):
    await _at_phone(chat)
    api.clear()
    await chat.payload("g|menu|")
    assert api.named("answer")[0]["notification"] == C.FINISH_REG_FIRST
    assert api.last_text().startswith(RT.ASK_PHONE[:20])
    assert (await session(repo)).state == S.REG_PHONE


# --- Чистые помощники ---

def test_best_candidate_and_flat():
    a, b = dadata("ул Ленина", "5"), dadata("ул Ленина", "7")
    parsed = AddressService().parse_local("Москва, Ленина 7")
    assert best_candidate([a], []) is a
    assert best_candidate([a, b], parsed) is b
    assert best_candidate([a, dadata("ул Ленина", "5", fias="x")], AddressService().parse_local("Москва, Ленина 5")) is None
    assert [parse_flat(t) for t in ("32", "кв. 32а", "квартира 7", "0", "abc", "123456")] == \
        ["32", "32А", "7", None, None, None]
