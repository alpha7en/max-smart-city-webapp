"""Профиль и доступ: сценарии P1–P8 (SPEC_REVIEW, S1)."""
from __future__ import annotations

from pathlib import Path

from app.bot.session import load_session
from app.bot.states import S
from app.bot.texts import common as C
from app.bot.texts import invite as IT
from app.bot.texts import profile as PT
from app.bot.texts import registration as RT
from app.integrations.max_api import MaxApiError
from tests import fakes
from tests.conftest import Chat
from tests.test_registration import ADDRESS, UID, UID2, buttons, labels, last_kb, register, session


async def _profile(chat: Chat) -> None:
    await chat.payload("g|profile|")


async def _pair(router, api) -> tuple[Chat, Chat]:
    """Собственник и арендатор на одном адресе."""
    owner, tenant = Chat(router, api, UID), Chat(router, api, UID2)
    await register(owner)
    await register(tenant, name="Петров Пётр", phone="+7 900 111-22-33")
    return owner, tenant


async def _ids(repo) -> tuple[int, int, int]:
    tenant = await repo.get_user(UID2)
    (addr,) = await repo.user_addresses(tenant["id"])
    return (await repo.get_user(UID))["id"], tenant["id"], addr["id"]


# --- P1–P3 ---

async def test_p1_profile(chat, api):
    await register(chat)
    api.clear()
    await _profile(chat)
    text = api.last_text()
    assert text == PT.PROFILE.format(name="Иванова Анна Сергеевна", phone="+7 912 345-67-89",
                                     addresses="Арбат 47к1, кв 32 — собственник\nДоступ: только вы")
    assert labels(last_kb(api)) == [PT.BTN_EDIT_PHONE, PT.BTN_ADD_ADDRESS, IT.BTN_INVITE, PT.BTN_DELETE, C.BTN_MENU]
    assert all(b["payload"].startswith("g|") for b in buttons(last_kb(api)))


async def test_p2_change_phone(chat, api, repo):
    await register(chat)
    await chat.payload("g|prof_phone|")
    assert (await session(repo)).state == S.PROFILE_PHONE
    assert labels(last_kb(api)) == [RT.BTN_SHARE_PHONE, C.BTN_CANCEL]
    await chat.text("12345")
    assert api.last_text() == RT.PHONE_ERROR
    await chat.feed(fakes.message_created(UID, None, [fakes.contact(UID, "79005554433")]))
    user = await repo.get_user(UID)
    assert (user["phone"], user["phone_verified"]) == ("+79005554433", 1)
    assert api.last_text().startswith(PT.PHONE_SAVED) and "+7 900 555-44-33 (номер из MAX)" in api.last_text()
    assert (await session(repo)).state == S.IDLE


async def test_p2_cancel_phone_change(chat, api, repo):
    await register(chat)
    await chat.payload("g|prof_phone|")
    await chat.press(C.BTN_CANCEL)
    assert api.last_text().startswith(C.PROFILE_CANCELLED_BY_USER)
    assert (await repo.get_user(UID))["phone"] == "+79123456789"


async def test_p3_add_address_duplicate_new_and_foreign(router, api, repo):
    chat = Chat(router, api, UID)
    await register(chat)
    uid = (await repo.get_user(UID))["id"]

    await chat.payload("g|prof_addr|")
    assert api.last_text() == PT.ASK_ADDRESS and (await session(repo)).state == S.PROFILE_ADDR_INPUT
    await chat.text(ADDRESS)
    await chat.press(RT.BTN_YES)
    assert api.last_text().startswith(PT.ADDRESS_DUP.format(label="Арбат 47к1, кв 32"))
    assert len(await repo.user_addresses(uid)) == 1

    await chat.payload("g|prof_addr|")
    await chat.text("Санкт-Петербург, Невский 10, кв 5")
    await chat.press(RT.BTN_YES)
    rows = await repo.user_addresses(uid)
    assert [r["label"] for r in rows] == ["Мск, Арбат 47к1, кв 32", "СПб, Невский 10, кв 5"]  # пересчитаны
    assert api.last_text().startswith(PT.ADDRESS_SAVED.format(label="СПб, Невский 10, кв 5"))

    other = Chat(router, api, 7000099)
    await register(other, name="Сидоров Иван", phone="+7 900 000-00-01", address="Казань, Баумана 5, кв 7")
    await chat.payload("g|prof_addr|")
    await chat.text("Казань, Баумана 5")
    await chat.press(RT.BTN_YES)
    assert (await session(repo)).state == S.PROFILE_ADDR_FLAT
    await chat.text("7")
    rows = await repo.user_addresses(uid)
    assert [(r["role"], r["access"]) for r in rows][-1] == ("tenant", "pending")
    assert "уже зарегистрирован собственник" in api.last_text()
    assert labels(last_kb(api))[0] == PT.BTN_REQUEST
    assert (await session(repo)).state == S.IDLE


async def test_p3_cancel_address(chat, api, repo):
    await register(chat)
    await chat.payload("g|prof_addr|")
    await chat.press(C.BTN_CANCEL)
    assert api.last_text().startswith(C.PROFILE_CANCELLED_BY_USER)
    assert (await session(repo)).state == S.IDLE


# --- P4–P5: удаление данных ---

async def test_p4_delete_data(chat, api, repo, settings, frozen_clock):
    await register(chat)
    user = await repo.get_user(UID)
    (addr,) = await repo.user_addresses(user["id"])
    meter_id, _ = await repo.submit_reading(
        user_id=user["id"], period="2026-10", values={"t1": 100_000}, source="manual",
        draft={"address_id": addr["id"], "type": "cold_water"})
    await chat.payload("g|prof_del|")
    assert api.last_text() == PT.DELETE_ASK
    assert labels(last_kb(api)) == [PT.BTN_DELETE_YES, PT.BTN_DELETE_NO]
    photo = Path(settings.photos_dir) / "p1.jpg"
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(b"jpeg")
    await repo.add_photo("p1", user["id"], str(photo), frozen_clock, frozen_clock)
    await chat.press(PT.BTN_DELETE_YES)
    assert api.last_text() == PT.DELETED and labels(last_kb(api)) == [PT.BTN_START_OVER]
    assert await repo.get_user(UID) is None
    assert await repo.get_session(user["id"]) is None
    assert await repo.user_addresses(user["id"]) == []
    assert (await repo.history(meter_id))[0]["user_id"] is None
    assert (await repo.get_meter(meter_id))["created_by"] is None
    assert await repo.get_photo("p1") is None and not photo.exists()

    api.clear()
    await chat.text("привет")
    assert api.texts() == [C.WELCOME, RT.ASK_NAME]


async def test_p5_keep_data(chat, api, repo):
    await register(chat)
    await chat.payload("g|prof_del|")
    await chat.text("нет")
    assert api.last_text().startswith(PT.DELETE_KEPT)
    assert (await repo.get_user(UID))["registered_at"]
    assert (await session(repo)).state == S.IDLE


# --- P6–P8: доступ арендатора ---

async def test_p6_request_access(router, api, repo):
    _, tenant = await _pair(router, api)
    _, tenant_id, aid = await _ids(repo)
    api.clear()
    await tenant.press(PT.BTN_REQUEST)
    (to_owner,) = [s for s in api.named("send") if s["user_id"] == UID]
    assert to_owner["text"].startswith("Петров Пётр просит доступ") and "+7 900 111-22-33" in to_owner["text"]
    assert [(b["text"], b["payload"]) for b in buttons(to_owner["keyboard"])] == [
        (PT.BTN_ALLOW, f"g|acc_ok|{tenant_id}.{aid}"), (PT.BTN_DENY, f"g|acc_no|{tenant_id}.{aid}")]
    assert api.last_text() == PT.REQUEST_SENT
    api.clear()
    await tenant.press(PT.BTN_REQUEST)
    assert api.last_text() == PT.REQUEST_DUP
    assert not [s for s in api.named("send") if s["user_id"] == UID]


async def test_p6_request_send_failure_can_retry(router, api, repo, monkeypatch):
    _, tenant = await _pair(router, api)
    real_send = api.send

    async def failing(text, *, user_id=None, **kw):
        if user_id == UID:
            raise MaxApiError(403, "chat.denied", "blocked")
        return await real_send(text, user_id=user_id, **kw)

    monkeypatch.setattr(api, "send", failing)
    await tenant.press(PT.BTN_REQUEST)
    assert api.last_text() == PT.REQUEST_FAILED
    monkeypatch.setattr(api, "send", real_send)
    await tenant.press(C.BTN_RETRY)
    assert api.last_text() == PT.REQUEST_SENT


async def test_p7_allow(router, api, repo):
    owner, tenant = await _pair(router, api)
    owner_id, tenant_id, aid = await _ids(repo)
    await repo.submit_reading(user_id=owner_id, period="2026-10", values={"t1": 100_000}, source="manual",
                              draft={"address_id": aid, "type": "cold_water"})
    assert await repo.user_meters(tenant_id) == []
    await tenant.press(PT.BTN_REQUEST)
    await tenant.payload(f"g|acc_ok|{tenant_id}.{aid}")  # не собственник
    assert api.last_text() == PT.OWNER_ONLY
    api.clear()
    await owner.press(PT.BTN_ALLOW, mid="mid.req")
    assert (await repo.user_address(tenant_id, aid))["access"] == "granted"
    assert {"mid": "mid.req", "text": None, "keyboard": None} in api.named("edit")  # кнопки запроса убраны
    (to_tenant,) = [s for s in api.named("send") if s["user_id"] == UID2]
    assert to_tenant["text"].startswith("Собственник открыл вам доступ")
    assert labels(to_tenant["keyboard"]) == [PT.BTN_SUBMIT, C.BTN_MENU]
    assert any(t.startswith("Открыли доступ: Петров Пётр") for t in api.texts())
    assert len(await repo.user_meters(tenant_id)) == 1  # счётчики адреса теперь видны арендатору
    await owner.press(PT.BTN_ALLOW)
    assert api.last_text() == PT.DECIDED["granted"]


async def test_p8_deny(router, api, repo):
    owner, tenant = await _pair(router, api)
    _, tenant_id, aid = await _ids(repo)
    await tenant.press(PT.BTN_REQUEST)
    api.clear()
    await owner.press(PT.BTN_DENY)
    assert (await repo.user_address(tenant_id, aid))["access"] == "denied"
    (to_tenant,) = [s for s in api.named("send") if s["user_id"] == UID2]
    assert labels(to_tenant["keyboard"]) == [PT.BTN_PROFILE, C.BTN_MENU]
    await tenant.payload(f"g|acc_req|{aid}")
    assert api.last_text().startswith("Собственник не открыл вам доступ")
    await tenant.payload("g|profile|")
    assert "собственник не открыл доступ" in api.last_text()


async def test_request_after_owner_deleted_claims_address(router, api, repo):
    owner, tenant = await _pair(router, api)
    _, tenant_id, aid = await _ids(repo)
    await owner.payload("g|prof_del|")
    await owner.press(PT.BTN_DELETE_YES)
    await tenant.press(PT.BTN_REQUEST)
    assert api.last_text().startswith("По адресу Арбат 47к1, кв 32 больше нет собственника")
    ua = await repo.user_address(tenant_id, aid)
    assert (ua["role"], ua["access"]) == ("owner", "granted")


async def test_access_request_edge_cases(router, api, repo):
    owner, tenant = await _pair(router, api)
    _, tenant_id, aid = await _ids(repo)
    await tenant.payload("g|acc_req|999")
    assert api.last_text() == PT.ACCESS_UNKNOWN
    await owner.payload(f"g|acc_req|{aid}")  # собственник: доступ уже есть
    assert api.last_text().startswith("Доступ по адресу Арбат 47к1, кв 32 уже открыт")
    assert (await load_session(repo, tenant_id)).state == S.IDLE
    await owner.payload("g|acc_ok|junk")
    assert api.last_text() == PT.OWNER_ONLY


# --- Демо: «Открыть доступ (демо)» — жюри на адресе из примера не застревает в «нет прав» ---

def _no_access_kb(api) -> list[str]:
    """Подписи кнопок последнего сообщения «нет прав» (после регистрации за ним идёт меню)."""
    return labels(next(k for t, k in reversed(api.outgoing()) if "уже зарегистрирован собственник" in t))


async def test_demo_grant_opens_access_and_submission_works(router, api, repo):
    owner, tenant = await _pair(router, api)
    _, tenant_id, aid = await _ids(repo)
    assert _no_access_kb(api) == [PT.BTN_REQUEST, PT.BTN_DEMO_GRANT, PT.BTN_PROFILE, C.BTN_MENU]
    await tenant.press(PT.BTN_REQUEST)                      # запрос собственнику тоже ушёл
    await tenant.payload(f"g|acc_demo|{aid}")
    assert api.last_text() == PT.DEMO_GRANTED
    assert labels(last_kb(api)) == [PT.BTN_SUBMIT, C.BTN_MENU]
    ua = await repo.user_address(tenant_id, aid)
    assert (ua["role"], ua["access"]) == ("tenant", "granted")
    await tenant.payload(f"g|acc_demo|{aid}")               # повторное нажатие
    assert api.last_text().startswith("Доступ по адресу Арбат 47к1, кв 32 уже открыт")
    api.clear()
    await owner.press(PT.BTN_ALLOW)                          # собственник ответил позже — без дубля арендатору
    assert api.last_text() == PT.DECIDED["granted"]
    assert not [s for s in api.named("send") if s["user_id"] == UID2]
    await tenant.press(PT.BTN_SUBMIT)                        # основной сценарий дальше идёт как обычно
    assert (await session(repo, UID2)).state == S.SUB_AWAIT_PHOTO


async def test_demo_grant_hidden_and_ignored_without_demo_mode(api, repo, deps):
    from dataclasses import replace

    from app.bot.router import Router

    router = Router(replace(deps, settings=replace(deps.settings, demo_mode=False)))
    _, tenant = await _pair(router, api)
    _, tenant_id, aid = await _ids(repo)
    assert _no_access_kb(api) == [PT.BTN_REQUEST, PT.BTN_PROFILE, C.BTN_MENU]
    await tenant.payload(f"g|acc_demo|{aid}")               # кнопка из старого сообщения / подделка
    assert (await repo.user_address(tenant_id, aid))["access"] == "pending"
    assert api.last_text().startswith("По адресу Арбат 47к1, кв 32 уже зарегистрирован собственник")
