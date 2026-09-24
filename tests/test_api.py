"""API мини-приложения (A1–A8): TestClient, настоящая подпись initData тестовым токеном."""
from __future__ import annotations

import dataclasses
import functools
import json
import time
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import clock
from app.domain.addresses import AddressCandidate, norm_key
from app.web import api as web_api
from app.web.auth import sign_init_data
from tests import fakes
from tests.conftest import TOKEN

UID, OTHER, TENANT = 5273381, 777001, 777002
ORIGIN = "https://alpha7en.github.io"
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


def init_data(uid: int = UID, age: float = 0, token: str = TOKEN) -> str:
    user = json.dumps({"id": uid, "first_name": "Анна", "last_name": "Иванова"}, ensure_ascii=False)
    return sign_init_data({"auth_date": str(int(time.time() - age)), "query_id": "q1", "user": user}, token)


def auth(uid: int = UID) -> dict:
    return {"X-Max-Init-Data": init_data(uid)}


def register(client: TestClient, uid: int = UID, flat: str = "32") -> dict:
    """Зарегистрированный пользователь с адресом (первый по адресу — owner/granted)."""
    async def go():
        r = repo(client)
        user = await r.ensure_user(uid, fakes.chat_of(uid))
        cand = AddressCandidate(f"г Москва, ул Арбат, д 47, кв {flat}", "г Москва", "г Москва", "ул Арбат",
                                "47", "к1", flat)
        res = await r.complete_registration(user["id"], full_name="Иванова Анна Сергеевна", phone="+79123456789",
                                            phone_verified=True, address=cand.to_dict(), norm_key=norm_key(cand),
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


# --- A1: авторизация ---

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


def test_a1_dev_user_only_with_dev_auth(make_client):
    assert_error(make_client().get("/api/me", headers={"X-Dev-User": str(UID)}), 401, "unauthorized")
    dev = make_client(dev_auth=True)
    assert dev.get("/api/me", headers={"X-Dev-User": str(UID)}).json()["registered"] is False


# --- A2: незарегистрированный ---

def test_a2_me_unregistered(client):
    expected = {"registered": False, "user": None, "dashboard": {"lines": [], "urgent": None},
                "meters": [], "addresses": [], "bot_username": "test_bot"}
    assert client.get("/api/me", headers=auth()).json() == expected
    run(client, repo(client).ensure_user, UID, 1)  # начал регистрацию, но не закончил
    assert client.get("/api/me", headers=auth()).json() == expected


# --- A3: /api/me зарегистрированного ---

def test_a3_me_contract_and_dashboard(client):
    u = register(client)
    m1 = add_meter(client, u["address_id"], readings={"2026-09": 118_200, "2026-10": 123_456})
    m2 = add_meter(client, u["address_id"], "electricity", 2, serial=None)
    d = client.get("/api/me", headers=auth()).json()
    assert d["registered"] is True and d["bot_username"] == "test_bot"
    assert d["user"] == {"full_name": "Иванова Анна Сергеевна", "phone": "+79123456789", "phone_verified": True}
    assert d["addresses"] == [{"label": u["label"], "access": "granted", "role": "owner"}]

    by_id = {m["id"]: m for m in d["meters"]}
    water, power = by_id[m1], by_id[m2]
    assert set(water) == {"id", "type", "type_label", "unit", "tariffs", "address_label", "serial", "last",
                          "submitted_this_period", "verification_due"}
    assert water["type_label"] == "Хол. вода" and water["unit"] == "м³" and water["address_label"] == u["label"]
    assert water["serial"] == "18-123456" and water["submitted_this_period"] is True
    assert water["last"]["period"] == "2026-10"
    assert water["last"]["values"] == {"t1": 123.456, "t2": None, "t3": None}  # числа в единицах, не тысячные
    assert water["last"]["created_at"].startswith("20") and "+03:00" in water["last"]["created_at"]
    assert power["tariffs"] == 2 and power["unit"] == "кВт·ч" and power["last"] is None
    assert power["submitted_this_period"] is False

    # дашборд — тот же, что строит сервис меню (S4), в формате контракта
    expected = web_api.dashboard_json(run(client, web_api.dashboard, client.app.state.deps, u["id"], clock.today()))
    assert d["dashboard"] == expected
    assert any("Счёт" in line and "(демо)" in line for line in d["dashboard"]["lines"])
    urgent = d["dashboard"]["urgent"]
    assert urgent is None or (urgent["kind"] in {"verification", "bill", "submit"} and urgent["text"])


def test_a3_pending_address_marked_and_hidden_meters(client):
    owner = register(client, OTHER)
    add_meter(client, owner["address_id"])
    register(client, TENANT)  # тот же адрес → tenant/pending
    d = client.get("/api/me", headers=auth(TENANT)).json()
    assert d["registered"] is True and d["meters"] == []
    assert d["addresses"] == [{"label": owner["label"], "access": "pending", "role": "tenant"}]


def test_a3_urgent_kind_and_dashboard_shapes():
    @dataclasses.dataclass
    class U:
        kind: str
        text: str
        days_left: int

    @dataclasses.dataclass
    class D:
        lines: list
        urgent: U | None

    got = web_api.dashboard_json(D(["a", "", "b"], U("verif", "Запишитесь на поверку: 20 дн.", 20)))
    assert got == {"lines": ["a", "", "b"], "urgent": {"kind": "verification",
                                                       "text": "Запишитесь на поверку: 20 дн.", "days_left": 20}}
    assert web_api.dashboard_json({"lines": [], "urgent": {"kind": "pay", "text": "x", "days_left": 1}})[
        "urgent"]["kind"] == "bill"
    assert web_api.urgent_kind("submit") == "submit"


# --- A4: /api/meters/{id} ---

def test_a4_meter_detail_history(client):
    u = register(client)
    periods = {f"{2025 + (i + 9) // 12}-{(i + 9) % 12 + 1:02d}": 100_000 + i * 1000 for i in range(14)}
    mid = add_meter(client, u["address_id"], readings=periods)
    d = client.get(f"/api/meters/{mid}", headers=auth()).json()
    assert d["id"] == mid and d["type_label"] == "Хол. вода" and d["serial"] == "18-123456"
    hist = d["history"]
    assert len(hist) == 12
    assert [h["period"] for h in hist] == sorted(periods, reverse=True)[:12]
    assert hist[0]["values"]["t1"] == 113.0 and hist[0]["source"] == "manual" and hist[0]["status"] == "accepted"
    assert {"period", "values", "source", "status", "created_at"} <= set(hist[0])


def test_a4_meter_404_403(client):
    register(client)
    other = register(client, OTHER, flat="33")
    foreign = add_meter(client, other["address_id"])
    assert_error(client.get("/api/meters/999", headers=auth()), 404, "not_found")
    assert_error(client.get("/api/meters/abc", headers=auth()), 404, "not_found")
    assert_error(client.get(f"/api/meters/{foreign}", headers=auth()), 403, "no_access")
    register(client, TENANT, flat="33")  # арендатор без доступа
    assert_error(client.get(f"/api/meters/{foreign}", headers=auth(TENANT)), 403, "no_access")


# --- A5: /api/readings ---

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
    assert [b["text"] for b in buttons] == ["Подать ещё", "В меню"]
    assert all(b["payload"].startswith("g|") for b in buttons)
    me = client.get("/api/me", headers=auth()).json()
    assert me["meters"][0]["submitted_this_period"] is True


def test_a5_errors_and_confirm_replace(client, api):
    u = register(client)
    mid = add_meter(client, u["address_id"], readings={"2026-09": 118_200})
    for bad in ("12,34,5", "123,4567", "-5", "abc", ""):
        assert_error(post(client, mid, values={"t1": bad}), 422, "bad_format")
    assert_error(post(client, mid, values={"t1": "100"}), 422, "less_than_previous")

    assert_error(post(client, mid, values={"t1": "200"}), 409, "needs_confirm")  # +81 м³ > 30
    r = post(client, mid, values={"t1": "200"}, confirm=True)
    assert r.status_code == 200 and r.json()["status"] == "flagged"

    assert_error(post(client, mid, values={"t1": "125"}), 409, "already_submitted")
    r = post(client, mid, values={"t1": "125"}, replace=True)
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    rows = run(client, repo(client).history, mid)
    assert [(x["period"], x["t1"]) for x in rows] == [("2026-10", 125_000), ("2026-09", 118_200)]
    assert len(api.named("send")) == 2  # по сообщению на каждую успешную подачу


def test_a5_access_and_bad_request(client, api):
    register(client)
    other = register(client, OTHER, flat="33")
    foreign = add_meter(client, other["address_id"])
    assert_error(post(client, foreign, values={"t1": "1"}), 403, "no_access")
    assert_error(post(client, 999, values={"t1": "1"}), 404, "not_found")
    assert_error(client.post("/api/readings", headers=auth(), json={"meter_id": "x"}), 422, "bad_request")
    assert_error(client.post("/api/readings", headers=auth(), content=b"{"), 422, "bad_request")
    assert api.named("send") == []


def test_a5_chat_failure_is_only_logged(client, api, monkeypatch, caplog):
    u = register(client)
    mid = add_meter(client, u["address_id"], "electricity", 2, readings={})

    async def boom(*a, **k):
        raise RuntimeError("MAX down")

    monkeypatch.setattr(api, "send", boom)
    r = post(client, mid, values={"t1": "1234,5", "t2": "600"})
    assert r.status_code == 200 and r.json()["reading"]["values"] == {"t1": 1234.5, "t2": 600.0, "t3": None}
    assert "chat notify failed" in caplog.text


# --- A6: /api/recognize ---

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


def test_a6_recognize_errors(client, monkeypatch):
    u = register(client)
    mid = add_meter(client, u["address_id"])
    assert_error(recognize(client, mid, b"hello", "text/plain"), 415, "not_image")
    assert_error(recognize(client, mid, b"", "image/jpeg"), 415, "not_image")
    assert_error(recognize(client, mid, b"\0" * (10 * 1024 * 1024 + 1)), 413, "too_large")
    other = register(client, OTHER, flat="33")
    assert_error(recognize(client, add_meter(client, other["address_id"])), 403, "no_access")
    assert_error(client.post("/api/recognize", headers=auth(), data={"meter_id": str(mid)}), 422, "no_file")
    assert photos_left(client) == []

    seen = []

    class Broken:
        async def recognize(self, path, *a, **k):
            seen.append(Path(path).exists())
            raise RuntimeError("boom")

    monkeypatch.setattr(client.app.state.deps, "recognizer", Broken())
    assert_error(recognize(client, mid), 500, "internal")
    assert seen == [True] and photos_left(client) == []


def test_a6_too_large_by_content_length(client):
    headers = {**auth(), "Content-Length": str(20 * 1024 * 1024), "Content-Type": "multipart/form-data; boundary=x"}
    assert_error(client.post("/api/recognize", headers=headers, content=b"--x--"), 413, "too_large")


# --- A7: CORS ---

def test_a7_cors_for_miniapp_origin(make_client):
    c = make_client(miniapp_origins=(ORIGIN,))
    pre = c.options("/api/me", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET",
                                        "Access-Control-Request-Headers": "X-Max-Init-Data"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == ORIGIN
    assert "x-max-init-data" in pre.headers["access-control-allow-headers"].lower()
    r = c.get("/api/me", headers={"Origin": ORIGIN, **auth()})
    assert r.headers["access-control-allow-origin"] == ORIGIN
    denied = c.get("/api/me", headers={"Origin": "https://evil.example", **auth()})
    assert "access-control-allow-origin" not in denied.headers
    err = c.get("/api/me", headers={"Origin": ORIGIN})  # ошибка тоже читается фронтендом
    assert err.status_code == 401 and err.headers["access-control-allow-origin"] == ORIGIN


# --- A8: health ---

def test_a8_health_without_auth(client):
    assert client.get("/api/health").json() == {"ok": True}
