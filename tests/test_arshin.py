"""ФГИС «Аршин»: клиент (httpx.MockTransport), кэш, сервис проверки, сценарии бота, миграция v2→v3, API и тексты."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import aiosqlite
import httpx
import pytest

from app import arshin_service as AS
from app.bot.flows import menu as menu_flow
from app.bot.flows.notify import verification_notice
from app.bot.router import Router
from app.bot.states import S
from app.bot.texts import arshin as TA
from app.bot.texts import common as C
from app.bot.texts import submission as T
from app.db import MIGRATIONS, SCHEMA_FILE, SCHEMA_VERSION
from app.domain.dashboard import DashboardData, build_dashboard
from app.domain.verification import card_url, choose
from app.integrations import arshin as A
from app.integrations.arshin import ArshinClient
from app.repo import Repo
from app.web.api import meter_json
from tests.conftest import NOW, Chat
from tests.test_submission import FixedRecognizer, buttons, give_serial, register, state

TODAY = NOW.date()
WATER = {"vri_id": "1-331", "org_title": "ООО «Поверка»", "mit_number": "74189-19",
         "mit_title": "Счетчики холодной и горячей воды", "mit_notation": "СГВ", "mi_modification": "СГВ-15",
         "mi_number": "18-765432", "verification_date": "2023-10-19T12:00:00Z", "valid_date": "2029-10-18T12:00:00Z",
         "applicability": True}
OTHER = {**WATER, "vri_id": "1-332", "mit_number": "55555-13", "mit_title": "Счетчики воды крыльчатые",
         "mit_notation": "ВСКМ", "mi_modification": "", "verification_date": "10.03.2022", "valid_date": "09.03.2028"}
MANOMETER = {**WATER, "vri_id": "1-333", "mit_number": "11111-10", "mit_title": "Манометры показывающие",
             "mit_notation": "МП"}


def listing(*items) -> dict:
    return {"result": {"count": len(items), "start": 0, "rows": 100, "items": list(items)}}


class Clock:
    """Монотонные часы, которые двигает подменённый sleep."""

    def __init__(self):
        self.t, self.sleeps = 1000.0, []

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.sleeps.append(round(s, 3))
        self.t += s


def make(handler, repo=None, mode="live") -> tuple[ArshinClient, list[httpx.Request], Clock]:
    seen: list[httpx.Request] = []

    async def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        res = handler(request)
        return await res if asyncio.iscoroutine(res) else res

    clock = Clock()
    client = ArshinClient(mode, "https://fgis.test/eapi", repo, transport=httpx.MockTransport(wrapped),
                          sleep=clock.sleep, monotonic=clock)
    return client, seen, clock


def ok(*items) -> httpx.Response:
    return httpx.Response(200, json=listing(*items))


# --- Клиент ---

async def test_query_skips_current_year_trap_and_headers():
    client, seen, _ = make(lambda r: ok(WATER))
    res = await client.lookup("18-765432", "cold_water", TODAY)
    assert res.status == "found" and [r.vri_id for r in res.records] == ["1-331"]
    (req,) = seen
    assert req.url.path == "/eapi/vri"
    assert dict(req.url.params) == {"mi_number": "18-765432", "verification_date_start": "2019-10-19", "rows": "100"}
    assert req.headers["User-Agent"].startswith("max-smart-city-bot/") and req.headers["Accept"] == "application/json"
    assert res.records[0].valid_date == date(2029, 10, 18)


async def test_variants_then_none_within_budget():
    client, seen, _ = make(lambda r: ok())
    res = await client.lookup("18-765432", "cold_water", TODAY)
    assert res.status == "none"
    assert [r.url.params["mi_number"] for r in seen] == ["18-765432", "18765432", "765432"]


async def test_second_variant_found_ddmmyyyy_and_wrong_type_skipped():
    def handler(r):
        return ok(MANOMETER) if r.url.params["mi_number"] == "18-765432" else ok(OTHER)
    client, seen, _ = make(handler)
    res = await client.lookup("18-765432", "cold_water", TODAY)
    assert len(seen) == 2 and {r.vri_id for r in res.records} == {"1-333", "1-332"}
    assert next(r for r in res.records if r.vri_id == "1-332").valid_date == date(2028, 3, 9)


async def test_429_retry_after_then_ok():
    answers = [httpx.Response(429, headers={"Retry-After": "3"}), ok(WATER)]
    client, seen, clock = make(lambda r: answers.pop(0))
    assert (await client.lookup("18-765432", "cold_water", TODAY)).status == "found"
    assert len(seen) == 2 and 3.0 in clock.sleeps


async def test_5xx_twice_is_error_and_breaker_opens():
    client, seen, clock = make(lambda r: httpx.Response(504, text="<html>Gateway</html>"))
    for n in range(3):
        assert (await client.lookup(f"1234567{n}", "gas", TODAY)).status == "error"
    assert len(seen) == 6  # запрос + один повтор на каждую проверку
    assert (await client.lookup("12345679", "gas", TODAY)).status == "error"
    assert len(seen) == 6  # breaker: 10 минут не ходим
    clock.t += A.BREAKER_PAUSE
    await client.lookup("12345679", "gas", TODAY)
    assert len(seen) == 8


async def test_400_408_fallback_by_years_in_budget():
    def handler(r):
        if "verification_date_start" in r.url.params:
            return httpx.Response(408, json={"status": "REQUEST_TIMEOUT", "message": "превышен лимит"})
        return ok(WATER) if r.url.params["year"] == "2024" else ok()
    client, seen, _ = make(handler)
    res = await client.lookup("12345678", "cold_water", TODAY)
    assert res.status == "found"
    assert [r.url.params.get("year") for r in seen] == [None, "2026", "2025", "2024"]
    assert len(seen) <= A.MAX_REQUESTS


async def test_timeout_and_html_are_errors_never_raise():
    def boom(r):
        raise httpx.ReadTimeout("slow", request=r)
    client, seen, _ = make(boom)
    assert (await client.lookup("12345678", "gas", TODAY)).status == "error" and len(seen) == 2
    client, _, _ = make(lambda r: httpx.Response(200, text="<html>Сервис недоступен</html>"))
    assert (await client.lookup("12345678", "gas", TODAY)).status == "error"


async def test_throttle_between_requests():
    client, seen, clock = make(lambda r: ok())
    await client.lookup("12345678", "gas", TODAY)
    await client.lookup("87654321", "gas", TODAY)
    assert len(seen) == 2 and clock.sleeps == [A.MIN_INTERVAL]  # вторая проверка ждёт 0,6 с


async def test_details_fill_unfit():
    card = {"result": {"vriInfo": {"vrfDate": "08.02.2024", "inapplicable": {"noticeNum": "070-05"}}}}

    def handler(r):
        return httpx.Response(200, json=card) if r.url.path.endswith("/1-9") else ok(
            {"vri_id": "1-9", "mit_title": "Счетчики газа", "mit_number": "1-1", "verification_date": "08.02.2024"})
    client, seen, _ = make(handler)
    (r,) = (await client.lookup("12345678", "gas", TODAY)).records
    assert r.unfit and seen[-1].url.path == "/eapi/vri/1-9"


async def test_cache_ttl(repo):
    client, seen, _ = make(lambda r: ok(WATER), repo)
    assert (await client.lookup("18-765432", "cold_water", TODAY, NOW)).status == "found"
    assert (await client.lookup("18 765432", "cold_water", TODAY, NOW + timedelta(days=29))).status == "found"
    assert len(seen) == 1
    await client.lookup("18-765432", "cold_water", TODAY, NOW + timedelta(days=31))
    assert len(seen) == 2
    empty, seen2, _ = make(lambda r: ok(), repo)
    await empty.lookup("7777777", "gas", TODAY, NOW)
    n = len(seen2)
    await empty.lookup("7777777", "gas", TODAY, NOW + timedelta(days=2))
    assert len(seen2) == n
    await empty.lookup("7777777", "gas", TODAY, NOW + timedelta(days=3))
    assert len(seen2) == 2 * n
    row = await repo.arshin_cache_get("gas:7777777")
    assert row["status"] == "none" and json.loads(row["payload"]) == []


async def test_off_and_fixtures_modes_do_not_touch_network():
    off, seen, _ = make(lambda r: ok(WATER), mode="off")
    assert (await off.lookup("12345678", "gas", TODAY)).status == "off"
    demo, seen2, _ = make(lambda r: ok(WATER), mode="fixtures")
    res = await demo.lookup("12345678", "gas", TODAY)
    assert res.status == "found" and res.demo and res.records[0].vri_id.startswith("demo-")
    assert res.records[0].mi_number == "12345678" and res.records[0].valid_date > TODAY
    assert (await demo.lookup("12345670", "gas", TODAY)).status == "none"
    assert len((await demo.lookup("12345679", "cold_water", TODAY)).records) == 2  # коллизия номеров
    assert seen == seen2 == []


# --- Сценарии бота ---

@pytest.fixture
async def user(repo):
    return await register(repo)


def arshin_chat(deps, api, handler, recognizer=None, mode="live") -> tuple[Chat, list]:
    client, seen, _ = make(handler, deps.repo, mode)
    d = replace(deps, arshin=client, recognizer=recognizer or deps.recognizer)
    return Chat(Router(d), api), seen


async def new_water_meter(chat: Chat, serial: str = "18-765432") -> None:
    await chat.photo()
    await chat.press("Хол. вода")
    await chat.press("Арбат 47к1, кв 32")
    await give_serial(chat, serial)
    await chat.press(T.BTN_SEND)


async def test_found_no_date_question(deps, api, repo, user):
    chat, seen = arshin_chat(deps, api, lambda r: ok(WATER))
    await new_water_meter(chat)
    text = api.last_text()
    assert "Поверка по данным ФГИС «Аршин»: до **18.10.2029**" in text and T.ASK_VERIF not in text
    assert buttons(api) == [C.BTN_MENU, T.BTN_MORE, TA.BTN_CARD]
    link = api.outgoing()[-1][1]["payload"]["buttons"][1][0]
    assert link == {"type": "link", "text": TA.BTN_CARD, "url": "https://fgis.gost.ru/fundmetrology/cm/results/1-331"}
    assert (await state(repo)).state == S.IDLE
    (m,) = await repo.address_meters(user[1])
    assert (m["verification_due"], m["verification_source"], m["arshin_vri_id"]) == ("2029-10-18", "arshin", "1-331")
    assert m["arshin_mit_title"] == WATER["mit_title"] and m["arshin_checked_at"]
    assert len(seen) == 1


async def test_brand_from_photo_resolves_collision(deps, api, repo, user):
    rec = FixedRecognizer(values={"t1": 123456}, serial="18-765432", brand="Декаст", model="СГВ-15")
    chat, _ = arshin_chat(deps, api, lambda r: ok(OTHER, WATER), rec)
    await chat.photo()
    await chat.press("Хол. вода")
    await chat.press("Арбат 47к1, кв 32")
    await chat.press(T.BTN_SEND)
    assert "до **18.10.2029**" in api.last_text()
    assert (await repo.address_meters(user[1]))[0]["arshin_vri_id"] == "1-331"


async def test_low_asks_which_and_pick(deps, api, repo, user):
    chat, _ = arshin_chat(deps, api, lambda r: ok(WATER, OTHER))
    await new_water_meter(chat)
    text = api.last_text()
    assert TA.PICK_MANY.format(serial="18-765432") in text and TA.PICK_OR_DATE in text and T.ASK_VERIF not in text
    assert buttons(api) == ["СГВ · до 18.10.2029", "ВСКМ · до 09.03.2028", TA.BTN_NONE]
    assert (await state(repo)).state == S.SUB_VERIF_DATE
    assert (await repo.address_meters(user[1]))[0]["verification_due"] is None  # без подтверждения не пишем
    await chat.press("ВСКМ · до 09.03.2028")
    assert api.last_text() == "Записали: поверка до **09.03.2028** по данным ФГИС «Аршин». Напомним заранее."
    (m,) = await repo.address_meters(user[1])
    assert (m["verification_due"], m["verification_source"], m["arshin_vri_id"]) == ("2028-03-09", "arshin", "1-332")
    assert (await state(repo)).state == S.IDLE


async def test_low_none_of_them_asks_passport_date(deps, api, repo, user):
    chat, _ = arshin_chat(deps, api, lambda r: ok(WATER, OTHER))
    await new_water_meter(chat)
    await chat.text("31.02.2030")  # дата из паспорта вместо выбора — проверяется как обычно
    assert api.last_text() == T.VERIF_ERRORS["no_such_date"] and TA.BTN_NONE in buttons(api)
    await chat.press(TA.BTN_NONE)
    assert api.last_text() == T.ASK_VERIF and buttons(api) == [T.BTN_LATER]
    await chat.text("15.03.2030")
    m = (await repo.address_meters(user[1]))[0]
    assert (m["verification_due"], m["verification_source"]) == ("2030-03-15", "user")


async def test_none_honest_text_and_date_question(deps, api, repo, user):
    chat, _ = arshin_chat(deps, api, lambda r: ok())
    await new_water_meter(chat)
    text = api.last_text()
    assert TA.NOT_FOUND.format(serial="18-765432") in text and "40 рабочих дней" in text
    assert text.endswith(f"{T.ASK_VERIF}\n\n> {T.UK_MOCK}") and (await state(repo)).state == S.SUB_VERIF_DATE
    assert (await repo.address_meters(user[1]))[0]["arshin_checked_at"]


async def test_error_is_quiet(deps, api, repo, user):
    chat, _ = arshin_chat(deps, api, lambda r: httpx.Response(503))
    await new_water_meter(chat)
    text = api.last_text()
    assert "ФГИС" not in text and text.endswith(f"{T.ASK_VERIF}\n\n> {T.UK_MOCK}")
    assert (await repo.address_meters(user[1]))[0]["arshin_checked_at"] is None  # перепроверит планировщик


async def test_off_as_before(deps, api, repo, user):
    chat, seen = arshin_chat(deps, api, lambda r: ok(WATER), mode="off")
    await new_water_meter(chat)
    assert api.last_text().endswith(f"{T.ASK_VERIF}\n\n> {T.UK_MOCK}") and "ФГИС" not in api.last_text() and seen == []


async def test_fixtures_marked_demo(deps, api, repo, user):
    chat, _ = arshin_chat(deps, api, lambda r: ok(WATER), mode="fixtures")
    await new_water_meter(chat, "18-765431")
    assert api.last_text().endswith(f"> {T.UK_MOCK}\n> {TA.DEMO_NOTE}")  # демо — последней цитатой
    assert buttons(api) == [C.BTN_MENU, T.BTN_MORE]  # у демо-записи нет ссылки на реестр
    m = (await repo.address_meters(user[1]))[0]
    assert m["verification_source"] == "arshin" and AS.source_label(m) == TA.SOURCE_DEMO


async def test_timeout_continues_in_background(deps, api, repo, user, monkeypatch):
    monkeypatch.setattr(AS, "WAIT_SECONDS", 0.05)

    async def slow(r):
        await asyncio.sleep(0.2)
        return ok(WATER)
    chat, _ = arshin_chat(deps, api, slow)
    await new_water_meter(chat)
    assert api.last_text().endswith(f"{T.ASK_VERIF}\n\n> {T.UK_MOCK}") and "ФГИС" not in api.last_text()
    await asyncio.gather(*AS._BACKGROUND)
    m = (await repo.address_meters(user[1]))[0]
    assert (m["verification_due"], m["verification_source"]) == ("2029-10-18", "arshin")


async def test_user_date_not_overwritten_and_refresh_tick(deps, repo, user):
    uid, aid = user
    mine = await repo.create_meter(aid, "cold_water", 1, "18-765432", uid, "2031-01-01", "user")
    auto = await repo.create_meter(aid, "hot_water", 1, "18-765433", uid)
    client, seen, _ = make(lambda r: ok(WATER), repo)
    d = replace(deps, arshin=client)
    assert await AS.refresh_tick(d, NOW) == 1
    assert await AS.refresh_tick(d, NOW + timedelta(hours=1)) == 0  # раз в сутки
    assert (await repo.get_meter(mine))["verification_source"] == "user"
    assert (await repo.get_meter(auto))["verification_due"] == "2029-10-18"
    assert await repo.arshin_refresh_candidates(NOW + timedelta(days=5), 10) == []
    assert [m["id"] for m in await repo.arshin_refresh_candidates(NOW + timedelta(days=31), 10)] == [auto]


# --- Миграция v2 → v3 ---

async def test_migration_v2_to_v3_keeps_meters(tmp_path):
    path = tmp_path / "v2.db"
    async with aiosqlite.connect(path) as db:
        await db.executescript(SCHEMA_FILE.read_text("utf-8") + MIGRATIONS[2])
        await db.executescript(
            "INSERT INTO users(id, max_user_id) VALUES(1, 1);"
            "INSERT INTO addresses(id, norm_key, status, source, full_text) VALUES(1, 'k', 'unverified', 'local', 'A');"
            "INSERT INTO meters(id, address_id, type, serial, serial_norm, verification_due, verification_source) "
            "VALUES(7, 1, 'gas', '123', '123', '2030-01-01', 'user');"
            "INSERT INTO readings(meter_id, user_id, period, t1, source, status) VALUES(7, 1, '2026-10', 5, 'manual', 'accepted');"
            "PRAGMA user_version=2;")
        await db.commit()
    repo = await Repo.open(path)
    try:
        async with repo.db.execute("PRAGMA user_version") as cur:
            assert (await cur.fetchone())[0] == SCHEMA_VERSION == 3
        m = await repo.get_meter(7)
        assert (m["serial"], m["verification_due"], m["verification_source"], m["arshin_vri_id"]) == (
            "123", "2030-01-01", "user", None)
        assert (await repo.last_reading(7))["t1"] == 5
        await repo.set_arshin(7, due="2031-02-03", vri_id="1-5", mit_title="Счетчики газа", checked_at=NOW)
        assert (await repo.get_meter(7))["verification_source"] == "arshin"
        with pytest.raises(Exception):
            await repo._exec("UPDATE meters SET verification_source='bad' WHERE id=7")
        with pytest.raises(Exception):  # внешние ключи снова включены
            await repo._exec("INSERT INTO readings(meter_id, period, t1, source, status) "
                             "VALUES(999, '2026-10', 1, 'manual', 'accepted')")
        await repo.arshin_cache_put("gas:123", "[]", "none", NOW.isoformat())
    finally:
        await repo.close()
    repo = await Repo.open(path)  # повторное открытие — без миграций и ошибок
    await repo.close()


# --- API, дашборд, меню, уведомления ---

def arshin_row(**kw) -> dict:
    row = {"id": 3, "type": "cold_water", "tariffs": 1, "address_id": 1, "address_label": "Арбат 47к1, кв 32",
           "serial": "18-765432", "verification_due": "2029-10-18", "verification_source": "arshin",
           "arshin_vri_id": "1-331", "last_id": None}
    return {**row, **kw}


def test_api_fields():
    j = meter_json(arshin_row(), TODAY)
    assert (j["verification_source"], j["arshin_url"], j["arshin_demo"]) == (
        "arshin", "https://fgis.gost.ru/fundmetrology/cm/results/1-331", False)
    j = meter_json(arshin_row(arshin_vri_id="demo-w1"), TODAY)
    assert j["arshin_url"] is None and j["arshin_demo"] is True
    j = meter_json(arshin_row(verification_source="user"), TODAY)
    assert (j["verification_source"], j["arshin_url"], j["arshin_demo"]) == ("user", None, False)


def test_dashboard_and_notice_mark_source():
    row = arshin_row(verification_due=(TODAY + timedelta(days=20)).isoformat(), last_period=None)
    d = build_dashboard(DashboardData([row], [], [{"label": "Арбат 47к1, кв 32", "access": "granted",
                                                    "role": "owner"}]), TODAY)
    assert any(line.endswith(", по данным ФГИС «Аршин»") for line in d.lines)
    n = verification_notice(row, "Хол. вода · Арбат 47к1, кв 32", TODAY)
    assert TA.NOTICE_SOURCE in n.text and TA.NOTICE_ANTIFRAUD in n.text
    link = n.keyboard["payload"]["buttons"][1][0]
    assert link["type"] == "link" and link["url"].endswith("/cm/results/1-331")


async def test_my_meters_source_and_antifraud(chat, api, repo, user):
    uid, aid = user
    mid = await repo.create_meter(aid, "cold_water", 1, "18-765432", uid)
    await repo.set_arshin(mid, due="2029-10-18", vri_id="1-331", mit_title="Счетчики воды", checked_at=NOW)
    await chat.payload(f"g|{menu_flow.METERS}|")
    text = api.last_text()
    assert "поверка до **18.10.2029** (по данным ФГИС «Аршин»)" in text
    assert text.endswith(TA.ANTIFRAUD.format(date="18.10.2029"))
    await repo.set_verification(mid, (TODAY + timedelta(days=100)).isoformat(), "user")
    await chat.payload(f"g|{menu_flow.METERS}|")
    assert "Листовки" not in api.last_text()  # срок ближе года — не про листовки


async def test_demo_button_runs_check(deps, api, repo, user):
    uid, aid = user
    await repo.create_meter(aid, "gas", 1, "12345678", uid)
    chat, _ = arshin_chat(deps, api, lambda r: ok(), mode="fixtures")
    await chat.text("/demo")
    assert TA.BTN_DEMO in buttons(api)
    await chat.press(TA.BTN_DEMO)
    assert "Поверка по данным ФГИС «Аршин»" in api.last_text()
    assert api.last_text().endswith(f"\n\n> {TA.DEMO_NOTE}")


# --- Реальные фикстуры ФГИС «Аршин» ---

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "arshin"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text("utf-8"))


async def test_real_water_fixture_lookup():
    water_data = _fixture("search_water_18452178.json")
    client, seen, _ = make(lambda r: httpx.Response(200, json=water_data))
    res = await client.lookup("18-452178", "cold_water", TODAY)
    assert res.status == "found"
    assert len(res.records) == 1
    r = res.records[0]
    assert r.vri_id == "1-333392815"
    assert r.verification_date == date(2023, 11, 14)
    assert r.valid_date == date(2029, 11, 13)
    assert r.applicable is True
    assert "СГВ-15" in r.mi_modification
    match = choose(res.records, "cold_water")
    assert match.level == "high" and match.best.vri_id == "1-333392815"
    assert card_url(match.best.vri_id) == "https://fgis.gost.ru/fundmetrology/cm/results/1-333392815"


async def test_real_collision_and_inapplicable_fixture():
    collision_data = _fixture("search_collision_0112456.json")
    inapplicable_card = _fixture("card_inapplicable_340080782.json")

    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/1-340080782"):
            return httpx.Response(200, json=inapplicable_card)
        return httpx.Response(200, json=collision_data)

    client, seen, _ = make(handler)
    res = await client.lookup("0112456", "cold_water", TODAY)
    assert res.status == "found"
    vri_ids = [r.vri_id for r in res.records]
    assert "2-120021534" in vri_ids
    assert "1-340080782" in vri_ids
    assert "1-368481110" in vri_ids
    assert card_url("2-120021534") == "https://fgis.gost.ru/fundmetrology/cm/results/2-120021534"
    unfit_rec = next(r for r in res.records if r.vri_id == "1-340080782")
    assert unfit_rec.unfit is True and unfit_rec.valid_date is None
    # Запросы деталей для термометра 1-390257330 не должны отправляться
    assert not any("1-390257330" in r.url.path for r in seen)
    gas_match = choose(res.records, "gas")
    assert gas_match.level == "high" and gas_match.best.vri_id == "1-368481110"
    assert gas_match.best.valid_date == date(2032, 9, 5)


async def test_real_water_fixture_bot_flow(deps, api, repo, user):
    water_data = _fixture("search_water_18452178.json")
    chat, _ = arshin_chat(deps, api, lambda r: httpx.Response(200, json=water_data))
    await new_water_meter(chat, "18-452178")
    text = api.last_text()
    assert "Поверка по данным ФГИС «Аршин»: до **13.11.2029**" in text
    assert buttons(api) == [C.BTN_MENU, T.BTN_MORE, TA.BTN_CARD]
    link = api.outgoing()[-1][1]["payload"]["buttons"][1][0]
    assert link["url"] == "https://fgis.gost.ru/fundmetrology/cm/results/1-333392815"
    (m,) = await repo.address_meters(user[1])
    assert m["verification_due"] == "2029-11-13" and m["verification_source"] == "arshin"
    assert m["arshin_vri_id"] == "1-333392815"


async def test_real_collision_unfit_bot_flow(deps, api, repo, user):
    collision_data = _fixture("search_collision_0112456.json")
    chat, seen = arshin_chat(deps, api, lambda r: httpx.Response(200, json=collision_data))
    await new_water_meter(chat, "0112456")
    text = api.last_text()
    assert "По данным ФГИС «Аршин» последняя поверка (**08.05.2024**) признала счётчик непригодным" in text
    assert buttons(api) == [C.BTN_MENU, T.BTN_MORE, TA.BTN_CARD]
    link = api.outgoing()[-1][1]["payload"]["buttons"][1][0]
    assert link["url"] == "https://fgis.gost.ru/fundmetrology/cm/results/1-340080782"
    (m,) = await repo.address_meters(user[1])
    assert m["verification_due"] is None
    assert m["verification_source"] == "arshin"
    assert m["arshin_vri_id"] == "1-340080782"
    assert AS.meter_url(m) == "https://fgis.gost.ru/fundmetrology/cm/results/1-340080782"
    assert AS.source_label(m) == TA.SOURCE
    # Убеждаемся, что на посторонние приборы (термометр 1-390257330) запросы карточки не уходили
    assert not any("1-390257330" in r.url.path for r in seen)


async def test_dns_fallback_socket_and_anyio_backend():
    import socket
    from anyio._backends._asyncio import AsyncIOBackend

    # Проверяем, что при сбое DNS резолвер возвращает fallback IP
    res_sock = socket.getaddrinfo("fgis.gost.ru", 443)
    ips_sock = [r[4][0] for r in res_sock]
    assert any(ip in A.FGIS_FALLBACK_IPS for ip in ips_sock)

    res_anyio = await AsyncIOBackend.getaddrinfo("fgis.gost.ru", 443)
    ips_anyio = [r[4][0] for r in res_anyio]
    assert any(ip in A.FGIS_FALLBACK_IPS for ip in ips_anyio)


