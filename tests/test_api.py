"""API мини-приложения: TestClient, настоящая подпись initData тестовым токеном."""
from __future__ import annotations

import dataclasses
import functools
import json
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import ANY

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import clock
from app.bot.texts import common as C
from app.bot.texts import submission as TS
from app.domain.addresses import AddressCandidate, norm_key
from app.web import api as web_api
from app.web.auth import sign_init_data
from tests import fakes
from tests.conftest import TOKEN

UID, OTHER, TENANT = 5273381, 777001, 777002
ERROR_TEXT = {"code", "message"}


async def _no_bot(deps):
    return None


async def _idle(deps):
    return None


@pytest.fixture
def make_client(settings, api, monkeypatch):
    """Приложение с настоящим lifespan, но FakeMaxApi вместо MAX и без poller/scheduler."""
    monkeypatch.setattr(main, "MaxApi", lambda *a, **k: api)
    monkeypatch.setattr(main, "_start_bot", _no_bot)
    monkeypatch.setattr(main, "run_scheduler", _idle)
    clients = []

    def make(**overrides) -> TestClient:
        c = TestClient(main.create_app(dataclasses.replace(settings, **overrides)))
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def run(client: TestClient, fn, *args, **kw):
    """Вызвать async-функцию в цикле приложения."""
    return client.portal.call(functools.partial(fn, *args, **kw))


def repo(client: TestClient):
    return client.app.state.repo


def init_data(uid: int = UID, age: float = 0, token: str = TOKEN, **extra) -> str:
    user = json.dumps({"id": uid, "first_name": "Анна", "last_name": "Иванова", **extra}, ensure_ascii=False)
    return sign_init_data({"auth_date": str(int(time.time() - age)), "query_id": "q1", "user": user}, token)


def auth(uid: int = UID) -> dict:
    return {"X-Max-Init-Data": init_data(uid)}


def register(client: TestClient, uid: int = UID, flat: str = "32", key: str | None = None) -> dict:
    """Зарегистрированный пользователь с адресом (первый по адресу — owner/granted)."""
    async def go():
        r = repo(client)
        user = await r.ensure_user(uid, fakes.chat_of(uid))
        cand = AddressCandidate(f"г Москва, ул Арбат, д 47, кв {flat}", "г Москва", "г Москва", "ул Арбат",
                                "47", "к1", flat)
        res = await r.complete_registration(user["id"], full_name="Иванова Анна Сергеевна", phone="+79123456789",
                                            phone_verified=True, address=cand.to_dict(), norm_key=key or norm_key(cand),
                                            raw_input="Арбат 47к1", now=clock.now())
        return {**await r.get_user_by_id(user["id"]), **res}
    return run(client, go)


def add_meter(client: TestClient, address_id: int, type_: str = "cold_water", tariffs: int = 1,
              readings: dict[str, int] | None = None, serial: str | None = "18-123456") -> int:
    async def go():
        r = repo(client)
        mid = await r.create_meter(address_id, type_, tariffs, serial)
        for period, t1 in (readings or {}).items():
            await r.add_reading(mid, None, period, {"t1": t1, "t2": None, "t3": None}, "manual")
        return mid
    return run(client, go)


def assert_error(resp, status: int, code: str) -> None:
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == ERROR_TEXT and body["code"] == code
    assert any("а" <= ch <= "я" for ch in body["message"].lower())  # по-русски


@pytest.mark.parametrize("method,path", [("get", "/api/me"), ("get", "/api/meters/1"),
                                         ("post", "/api/readings"), ("post", "/api/recognize")])
def test_a1_no_init_data_401(client, method, path):
    assert_error(getattr(client, method)(path), 401, "unauthorized")


def test_a1_bad_expired_and_foreign_init_data_401(client):
    good = init_data()
    assert client.get("/api/me", headers={"X-Max-Init-Data": good}).status_code == 200
    tampered = good.replace("%D0%90%D0%BD%D0%BD%D0%B0", "%D0%9E%D0%BB%D1%8F")  # подменили имя
    assert tampered != good
    for bad in (tampered, init_data(token="other-token"), init_data(age=25 * 3600), "garbage"):
        assert_error(client.get("/api/me", headers={"X-Max-Init-Data": bad}), 401, "unauthorized")


def test_a3_me_contract_and_dashboard(client):
    u = register(client)
    m1 = add_meter(client, u["address_id"], readings={"2026-09": 118_200, "2026-10": 123_456})
    m2 = add_meter(client, u["address_id"], "electricity", 2, serial=None)
    d = client.get("/api/me", headers=auth()).json()
    assert d["registered"] is True and d["bot_username"] == "test_bot"
    assert d["user"] == {"full_name": "Иванова Анна Сергеевна", "phone": "+79123456789", "phone_verified": True,
                         "photo_url": None}
    assert d["addresses"] == [{"id": u["address_id"], "label": u["label"], "full_text": ANY, "access": "granted",
                               "role": "owner", "verified": False, "owner": None, "owner_gen": None, "owner_female": None,
                               "shared_count": 0, "invites_count": 0, "since": None,
                               "members": []}]
    assert "Арбат" in d["addresses"][0]["full_text"]  # полный адрес для профиля; без DaData — не сверен с ФИАС

    by_id = {m["id"]: m for m in d["meters"]}
    water, power = by_id[m1], by_id[m2]
    assert set(water) == {"id", "type", "type_label", "unit", "tariffs", "address_id", "address_label", "address_full", "serial",
                          "last",
                          "submitted_this_period", "verification_due", "verification_source", "arshin_url",
                          "arshin_demo"}
    assert water["type_label"] == "Хол. вода" and water["unit"] == "м³" and water["address_label"] == u["label"]
    assert water["address_id"] == u["address_id"]
    assert water["serial"] == "18-123456" and water["submitted_this_period"] is True
    assert water["last"]["period"] == "2026-10"
    assert water["last"]["values"] == {"t1": 123.456, "t2": None, "t3": None}  # числа в единицах, не тысячные
    assert water["last"]["created_at"].startswith("20") and "+03:00" in water["last"]["created_at"]
    assert power["tariffs"] == 2 and power["unit"] == "кВт·ч" and power["last"] is None
    assert power["submitted_this_period"] is False

    # дашборд — тот же, что строит сервис меню: текст для бота + структура для мини-приложения
    dash = d["dashboard"]
    assert dash == run(client, web_api.dashboard, client.app.state.deps, u["id"], clock.today()).to_api()
    assert any("Счёт" in line and "(демо)" in line for line in dash["lines"])
    assert dash["urgent"] is None  # 19.10: до конца окна 6 дн., поверки нет, счёт до 10.11
    assert dash["window"] == {"period": "2026-10", "month_label": "октябрь", "from": "2026-10-15",
                              "to": "2026-10-25", "open": True, "days_left": 6, "next_from": None}
    bill = dash["bill"]
    assert set(bill) == {"id", "address_id", "amount_kop", "amount_text", "due", "days_left", "demo", "count"}
    assert bill["address_id"] == u["address_id"]
    assert dash["bills"] == [{k: v for k, v in bill.items() if k != "count"}]  # по ним фильтр по адресу
    assert bill["due"] == "2026-11-10" and bill["days_left"] == 22 and bill["demo"] is True
    assert bill["amount_text"].endswith("₽") and isinstance(bill["amount_kop"], int)
    assert (dash["verification"], dash["pending"], dash["submitted"], dash["total"]) == (None, [], 1, 2)


def test_a4_meter_404_403(client):
    register(client)
    other = register(client, OTHER, flat="33")
    foreign = add_meter(client, other["address_id"])
    assert_error(client.get("/api/meters/999", headers=auth()), 404, "not_found")
    assert_error(client.get("/api/meters/abc", headers=auth()), 404, "not_found")
    assert_error(client.get(f"/api/meters/{foreign}", headers=auth()), 403, "no_access")
    register(client, TENANT, flat="33")  # арендатор без доступа
    assert_error(client.get(f"/api/meters/{foreign}", headers=auth(TENANT)), 403, "no_access")


def post(client, meter_id, uid=UID, **body):
    return client.post("/api/readings", headers=auth(uid), json={"meter_id": meter_id, **body})


def test_a5_submit_success_and_chat_message(client, api):
    u = register(client)
    mid = add_meter(client, u["address_id"], readings={"2026-09": 118_200})
    r = post(client, mid, values={"t1": "123,456"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "accepted"
    assert d["reading"]["period"] == "2026-10" and d["reading"]["source"] == "miniapp"
    assert d["reading"]["values"] == {"t1": 123.456, "t2": None, "t3": None}
    row = run(client, repo(client).reading_for_period, mid, "2026-10")
    assert row["t1"] == 123_456 and row["user_id"] == u["id"] and row["source"] == "miniapp"

    (msg,) = api.named("send")
    assert msg["chat_id"] == fakes.chat_of(UID)
    assert "мини-приложения" in msg["text"] and "123,456 м³" in msg["text"] and "смоделирована" in msg["text"]
    buttons = [b for row_ in msg["keyboard"]["payload"]["buttons"] for b in row_]
    assert [b["text"] for b in buttons] == [TS.BTN_MORE, C.BTN_MENU]
    assert all(b["payload"].startswith("g|") for b in buttons)
    me = client.get("/api/me", headers=auth()).json()
    assert me["meters"][0]["submitted_this_period"] is True


def photos_left(client) -> list[Path]:
    d = client.app.state.settings.photos_dir
    return list(d.iterdir()) if d.exists() else []


def recognize(client, meter_id, data=b"\xff\xd8jpeg", ctype="image/jpeg", uid=UID):
    return client.post("/api/recognize", headers=auth(uid), data={"meter_id": str(meter_id)},
                       files={"file": ("m.jpg", data, ctype)})


def test_a6_recognize_stub_and_file_deleted(client):
    u = register(client)
    mid = add_meter(client, u["address_id"], readings={"2026-09": 118_200})
    r = recognize(client, mid)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["stub"] is True and d["confidence"] == 0.9 and d["serial"] == "18-123456"
    assert isinstance(d["values"]["t1"], float) and d["values"]["t1"] > 118.2 and d["values"]["t2"] is None
    assert photos_left(client) == []
    assert run(client, repo(client).expired_photos, clock.now() + timedelta(days=1)) == []
