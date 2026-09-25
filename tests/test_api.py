"""API мини-приложения (A1–A8): TestClient, настоящая подпись initData тестовым токеном."""
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
from app.bot.texts import submission as TS
from app.domain.addresses import AddressCandidate, norm_key
from app.integrations.recognizer import Recognition
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
    expected = {"registered": False, "user": None, "dashboard": {
                    "lines": [], "urgent": None, "window": None, "bill": None, "bills": [], "verification": None,
                    "pending": [], "submitted": 0, "total": 0},
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
    assert d["addresses"] == [{"id": u["address_id"], "label": u["label"], "full_text": ANY, "access": "granted",
                               "role": "owner", "verified": False, "members": []}]
    assert "Арбат" in d["addresses"][0]["full_text"]  # полный адрес для профиля; без DaData — не сверен с ФИАС

    by_id = {m["id"]: m for m in d["meters"]}
    water, power = by_id[m1], by_id[m2]
    assert set(water) == {"id", "type", "type_label", "unit", "tariffs", "address_id", "address_label", "serial", "last",
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


def test_a3_pending_address_marked_and_hidden_meters(client):
    owner = register(client, OTHER)
    add_meter(client, owner["address_id"])
    register(client, TENANT)  # тот же адрес → tenant/pending
    d = client.get("/api/me", headers=auth(TENANT)).json()
    assert d["registered"] is True and d["meters"] == []
    assert [(a["id"], a["label"], a["access"], a["role"]) for a in d["addresses"]] == [
        (owner["address_id"], owner["label"], "pending", "tenant")]
    dash = d["dashboard"]
    assert dash["pending"] == [{"label": owner["label"]}] and dash["bill"] is None and dash["total"] == 0
    assert sum("одобрения собственника" in line for line in dash["lines"]) == 1  # строка для бота — одна


def test_a3_owner_sees_members_without_personal_data(client):
    owner = register(client, OTHER)
    tenant = register(client, TENANT)  # тот же адрес → tenant/pending
    run(client, repo(client).update_user, tenant["id"], full_name="Петров Пётр Иванович")
    d = client.get("/api/me", headers=auth(OTHER)).json()
    assert d["addresses"][0]["members"] == [{"name_short": "Пётр П.", "access": "pending"}]
    run(client, repo(client).set_access, tenant["id"], owner["address_id"], "granted")
    (a,) = client.get("/api/me", headers=auth(OTHER)).json()["addresses"]
    assert a["members"] == [{"name_short": "Пётр П.", "access": "granted"}]
    assert "+7" not in str(a) and "Петров" not in str(a)  # ни телефона, ни фамилии целиком
    (t,) = client.get("/api/me", headers=auth(TENANT)).json()["addresses"]
    assert "members" not in t  # не собственнику список не отдаём


def test_a3_dashboard_verification_urgent(client):
    u = register(client)
    m = add_meter(client, u["address_id"])
    run(client, repo(client).set_verification, m, "2026-11-02", "user")
    dash = client.get("/api/me", headers=auth()).json()["dashboard"]
    assert dash["verification"] == {"meter_id": m, "meter_label": f"Хол. вода · {u['label']}", "type": "cold_water",
                                    "due": "2026-11-02", "days_left": 14}
    assert dash["urgent"] == {"kind": "verification", "text": "Запишитесь на поверку: 14 дней", "days_left": 14}


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


# --- Удаление счётчика: DELETE /api/meters/{id} ---

def test_delete_meter_200_then_gone(client):
    u = register(client)
    mid = add_meter(client, u["address_id"], readings={"2026-09": 118_200})
    keep = add_meter(client, u["address_id"], "electricity", serial=None)
    r = client.delete(f"/api/meters/{mid}", headers=auth())
    assert r.status_code == 200 and r.json() == {"status": "deleted", "id": mid}
    assert [m["id"] for m in client.get("/api/me", headers=auth()).json()["meters"]] == [keep]
    assert_error(client.get(f"/api/meters/{mid}", headers=auth()), 404, "not_found")
    assert_error(client.delete(f"/api/meters/{mid}", headers=auth()), 404, "not_found")  # повторно
    assert_error(post(client, mid, values={"t1": "120"}), 404, "not_found")
    assert run(client, repo(client).history, mid)  # показания остались в БД


def test_delete_meter_403_404(client):
    owner = register(client)
    mid = add_meter(client, owner["address_id"])
    assert_error(client.delete("/api/meters/999", headers=auth()), 404, "not_found")
    assert_error(client.delete("/api/meters/abc", headers=auth()), 404, "not_found")
    assert_error(client.delete(f"/api/meters/{mid}"), 401, "unauthorized")
    register(client, OTHER, flat="33")  # чужой адрес
    assert_error(client.delete(f"/api/meters/{mid}", headers=auth(OTHER)), 403, "no_access")
    tenant = register(client, TENANT)  # тот же адрес → tenant/pending
    assert_error(client.delete(f"/api/meters/{mid}", headers=auth(TENANT)), 403, "no_access")
    run(client, repo(client).set_access, tenant["id"], owner["address_id"], "granted")
    r = client.delete(f"/api/meters/{mid}", headers=auth(TENANT))
    assert_error(r, 403, "not_owner")
    assert "собственник" in r.json()["message"]
    assert client.delete(f"/api/meters/{mid}", headers=auth()).status_code == 200  # собственник может


def test_delete_meter_cors_preflight(make_client):
    c = make_client(miniapp_origins=(ORIGIN,))
    r = c.options("/api/meters/1", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "DELETE",
                                            "Access-Control-Request-Headers": "X-Max-Init-Data"})
    assert r.status_code == 200 and "DELETE" in r.headers["access-control-allow-methods"]


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


class Answer:
    """Распознаватель с заданным ответом."""

    def __init__(self, rec: Recognition):
        self.rec = rec

    async def recognize(self, *a, **k) -> Recognition:
        return self.rec


def recognize_with(client, monkeypatch, mid, **kw) -> dict:
    rec = Recognition(**{"values": {"t1": None, "t2": None, "t3": None}, **kw})
    monkeypatch.setattr(client.app.state.deps, "recognizer", Answer(rec))
    r = recognize(client, mid)
    assert r.status_code == 200, r.text
    return r.json()


def test_a6_recognize_explains_issues(client, monkeypatch):
    u = register(client)
    mid = add_meter(client, u["address_id"])
    d = recognize_with(client, monkeypatch, mid, issues=["glare", "angle", "too_dark"], note="Лампа отражается")
    assert (d["readable"], d["issues"], d["note"]) == (False, ["glare", "angle", "too_dark"], "Лампа отражается")
    assert d["message"] == " ".join([TS.ISSUE_TEXTS["glare"], TS.ISSUE_TEXTS["angle"], "Лампа отражается.",
                                     TS.FAILED_TAIL])
    d = recognize_with(client, monkeypatch, mid, error="timeout", issues=["service"])
    assert d["readable"] is False and d["message"] == f"{TS.ISSUE_TEXTS['service']} {TS.FAILED_TAIL_SERVICE}"
    d = recognize_with(client, monkeypatch, mid, confidence=0.3, values={"t1": 1000})
    assert d["readable"] is False and d["message"] == TS.API_UNREADABLE
    d = recognize_with(client, monkeypatch, mid, confidence=0.6, values={"t1": 1000}, issues=["wrong_type"])
    assert (d["readable"], d["message"], d["values"]["t1"]) == (True, TS.WRONG_TYPE_WARN, 1.0)
    d = recognize_with(client, monkeypatch, mid, confidence=0.9, values={"t1": 1000})
    assert (d["readable"], d["message"], d["issues"], d["serial_mismatch"]) == (True, None, [], False)


def test_a6_wrong_serial_in_miniapp_does_not_touch_meter(client, monkeypatch):
    u = register(client)
    mid = add_meter(client, u["address_id"])  # серийник 18-123456
    d = recognize_with(client, monkeypatch, mid, confidence=0.9, values={"t1": 1000}, serial="18-654321")
    assert d["serial_mismatch"] is True
    assert d["serial_note"] == "Номер на фото — 18-654321, у счётчика — 18-123456. Проверьте, тот ли счётчик выбран."
    d = recognize_with(client, monkeypatch, mid, confidence=0.9, values={"t1": 1000}, serial="18 123 456")
    assert (d["serial_mismatch"], d["serial_note"]) == (False, None)
    assert post(client, mid, values={"t1": "1"}).status_code == 200
    assert run(client, repo(client).get_meter, mid)["serial"] == "18-123456"


def test_a6_photo_quality_texts_and_serial(client, monkeypatch):
    """Прочитанное как есть (texts), конкретные предупреждения о фото, номер: не виден / обязателен."""
    u = register(client)
    mid = add_meter(client, u["address_id"], "electricity", serial=None)
    d = recognize_with(client, monkeypatch, mid, confidence=0.6, values={"t1": 2_168_000}, texts={"t1": "2168"},
                       issues=["glare", "serial_not_visible"])
    assert d["values"]["t1"] == 2168.0 and d["texts"] == {"t1": "2168"}
    assert d["message"] == TS.REVIEW_WARN["glare"] and d["serial_required"] is True
    with_serial = add_meter(client, u["address_id"], "gas", serial="1234567")
    d = recognize_with(client, monkeypatch, with_serial, confidence=0.9, values={"t1": 1000}, issues=["blurry"])
    assert d["serial_required"] is False and d["message"] == TS.REVIEW_WARN["blurry"]
    assert d["serial_note"] == "Номер на фото не виден — убедитесь, что это счётчик с номером 1234567."


def test_a5_photo_reading_requires_serial_for_meter_without_it(client, api):
    u = register(client)
    mid = add_meter(client, u["address_id"], "electricity", serial=None)
    other = add_meter(client, u["address_id"], "gas", serial="12345678")
    assert_error(post(client, mid, values={"t1": "2168"}, source="photo"), 422, "serial_required")
    assert_error(post(client, mid, values={"t1": "2168"}, source="photo", serial="2021"), 422, "serial_bad")
    assert_error(post(client, mid, values={"t1": "2168"}, source="photo", serial="12 345 678"), 422, "serial_taken")
    assert api.named("send") == [] and run(client, repo(client).reading_for_period, mid, "2026-10") is None
    r = post(client, mid, values={"t1": "2168"}, source="photo", serial="№ 01234567")
    assert r.status_code == 200 and r.json()["serial"] == "01234567"
    assert run(client, repo(client).get_meter, mid)["serial"] == "01234567"
    (msg,) = api.named("send")
    assert "**2168 кВт·ч**" in msg["text"] and "2168,00" not in msg["text"]  # как ввели, без выдуманных знаков
    # Ручной ввод номер не требует; у счётчика с номером поле serial игнорируется.
    assert post(client, other, values={"t1": "5"}, source="manual").status_code == 200
    assert post(client, other, values={"t1": "5"}, replace=True, source="photo", serial="99999999").status_code == 200
    assert run(client, repo(client).get_meter, other)["serial"] == "12345678"


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


# --- Пустые поля и серийники в API ---

@pytest.mark.parametrize("values", [{}, {"t1": None}, {"t1": ""}, {"t1": " "}])
def test_readings_missing_value_422_nothing_saved(client, values):
    u = register(client)
    mid = add_meter(client, u["address_id"])
    assert_error(post(client, mid, values=values), 422, "bad_format")
    assert run(client, repo(client).reading_for_period, mid, "2026-10") is None


def test_readings_multi_tariff_missing_t2_422(client):
    u = register(client)
    mid = add_meter(client, u["address_id"], "electricity", 2)
    r = post(client, mid, values={"t1": "100", "t2": None})
    assert_error(r, 422, "bad_format")
    assert "Т2" in r.json()["message"]


def test_recognize_partial_multi_tariff_lists_missing(client, monkeypatch):
    u = register(client)
    mid = add_meter(client, u["address_id"], "electricity", 2)
    d = recognize_with(client, monkeypatch, mid, confidence=0.9, values={"t1": None, "t2": 505_500, "t3": None})
    assert d["readable"] is True and d["missing"] == ["t1"]
    assert d["message"] == "Т1: на фото не видно — введите вручную."
    d = recognize_with(client, monkeypatch, mid, confidence=0.9, values={"t1": 1, "t2": 2, "t3": None})
    assert (d["missing"], d["message"]) == ([], None)
    d = recognize_with(client, monkeypatch, mid)
    assert d["readable"] is False and d["missing"] == ["t1", "t2"]


def test_recognize_serial_formatted_and_not_serial_dropped(client, monkeypatch):
    u = register(client)
    mid = add_meter(client, u["address_id"], serial="№ 18-123456")
    d = recognize_with(client, monkeypatch, mid, confidence=0.9, values={"t1": 1000}, serial="№ 18 - 654321")
    assert d["serial"] == "18-654321"
    assert d["serial_note"] == "Номер на фото — 18-654321, у счётчика — 18-123456. Проверьте, тот ли счётчик выбран."
    d = recognize_with(client, monkeypatch, mid, confidence=0.9, values={"t1": 1000}, serial="ГОСТ 50193")
    assert (d["serial"], d["serial_mismatch"]) == (None, False)
    me = client.get("/api/me", headers=auth()).json()
    assert me["meters"][0]["serial"] == "18-123456"
