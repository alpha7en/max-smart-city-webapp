import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health_endpoint():
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert "bot_username" in data

def test_get_meters_endpoint():
    res = client.get("/api/meters")
    assert res.status_code == 200
    meters = res.json()
    assert len(meters) >= 4
    assert any(m["id"] == "meter-khvs-1" for m in meters)

def test_submit_reading_endpoint():
    payload = {
        "meter_id": "meter-khvs-1",
        "reading_value": 145.5,
        "submission_channel": "test"
    }
    res = client.post("/api/meters/submit", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["is_valid"] is True
    assert data["current_value"] == 145.5

def test_ai_vision_scan_stub():
    payload = {
        "device_type_hint": "cold_water"
    }
    res = client.post("/api/ai/scan", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["is_mock"] is True
    assert "[ЗАГЛУШКА / AI VISION STUB]" in data["mock_notice"]
    assert data["recognized_serial_number"] == "2809142"

def test_arshin_check_endpoint():
    res = client.get("/api/arshin/check?serial=2809142")
    assert res.status_code == 200
    data = res.json()
    assert data["shield_color"] == "green"
    assert data["is_fraud_warning"] is True

def test_billing_gost_qr_endpoint():
    payload = {
        "qr_payload": "ST00012|Name=ООО УК ДОМОВОЙ СЕРВИС|PersonalAcc=40702810938000012345|BIC=044525225|CorrespAcc=30101810400000000225|PayeeINN=7701234567|Sum=485050|PersAcc=1004567890|Period=092026"
    }
    res = client.post("/api/billing/parse-qr", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["total_amount_rubles"] == 4850.50
    assert len(data["split_details"]) == 4

def test_tickets_crud_endpoints():
    # 1. List
    res = client.get("/api/tickets")
    assert res.status_code == 200
    assert len(res.json()) >= 1

    # 2. Create
    payload = {
        "category": "Электрика",
        "description": "Искрит щиток на 4 этаже",
        "priority": "emergency",
        "address": "ул. Ленина, 42"
    }
    res = client.post("/api/tickets", json=payload)
    assert res.status_code == 201
    created = res.json()
    assert created["priority"] == "emergency"
    assert created["sla_hours"] == 1

def test_guest_token_generation():
    payload = {
        "property_id": "flat-42-15",
        "tenant_name": "Сергеев И.",
        "duration_days": 14
    }
    res = client.post("/api/guest/generate", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "startapp=guest_" in data["direct_max_link"]

# ----------------- Milestone 1 Tests -----------------

def test_inspector_act_endpoint_with_json_payload():
    payload = {
        "meter_id": "meter-khvs-1",
        "reading_value": 142.385,
        "address": "г. Москва, ул. Тверская, д. 7, кв. 14",
        "inspector_name": "Контролер Смирнов В. И.",
        "photo_base64": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDA..."
    }
    res = client.post("/api/uk/inspector-act", json=payload)
    assert res.status_code in (200, 201)
    data = res.json()
    assert data["status"] == "success"
    assert data["act_id"].startswith("ACT-ЖКХ-")
    assert data["act_number"] == data["act_id"]
    assert data["meter_id"] == "meter-khvs-1"
    assert data["reading_value"] == 142.385
    assert data["address"] == "г. Москва, ул. Тверская, д. 7, кв. 14"
    assert "55.7558° N, 37.6173° E" in data["gps"]
    assert len(data["photo_hash_sha256"]) == 64
    assert data["crypto_hash"] == data["photo_hash_sha256"]
    assert data["export_1c_ready"] is True
    assert data["billing_export_status"] == "EXPORTED_TO_1C_ZHKH"
    assert "63-ФЗ" in data["legal_significance"]

def test_inspector_act_endpoint_legacy_webapp_query():
    res = client.post("/api/uk/inspector-act?address=г.+Москва,+ул.+Ленина,+д.+42")
    assert res.status_code in (200, 201)
    data = res.json()
    assert data["status"] == "success"
    assert "Ленина" in data["address"]
    assert data["act_id"].startswith("ACT-ЖКХ-")
    assert len(data["photo_hash_sha256"]) == 64
    assert data["export_1c_ready"] is True

def test_arshin_check_expired_meter_991201():
    res = client.get("/api/arshin/check?serial=991201")
    assert res.status_code == 200
    data = res.json()
    assert data["serial_number"] == "991201"
    assert data["status"] == "expired"
    assert data["shield_color"] == "red"
    assert data["is_fraud_warning"] is True
    assert "ИСТЕК" in data["safety_message"]
    assert "МОШЕННИК" in data["safety_message"].upper()

def test_auth_verify_endpoint_dev_bypass():
    res = client.get("/api/auth/verify-init-data")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "authenticated"
    assert data["is_dev_bypass"] is True
    assert data["user"]["role"] == "resident"

def test_auth_verify_endpoint_with_valid_and_invalid_header():
    from app.config import settings
    from app.api.security import generate_init_data
    import time
    import json

    token = settings.BOT_TOKEN or "test_token_123"
    raw_data = {
        "auth_date": str(int(time.time())),
        "query_id": "test_query_id",
        "user": json.dumps({"id": 423938205, "first_name": "Иван"})
    }
    signed_query = generate_init_data(raw_data, token)

    # Valid
    res_valid = client.get("/api/auth/verify-init-data", headers={"X-Init-Data": signed_query})
    assert res_valid.status_code == 200
    data = res_valid.json()
    assert data["status"] == "authenticated"
    assert data["is_dev_bypass"] is False
    assert data["user"]["id"] == 423938205

    # Invalid
    res_invalid = client.get("/api/auth/verify-init-data", headers={"X-Init-Data": "invalid=signature&hash=deadbeef"})
    assert res_invalid.status_code == 401
    assert "Invalid initData" in res_invalid.json()["detail"]

def test_bot_webhook_endpoint_single_and_batch():
    # 1. Single
    single_payload = {
        "update_type": "message_created",
        "message": {
            "body": {"text": "/start"},
            "sender": {"id": 12345, "first_name": "Тестер"},
            "recipient": {"chat_id": "chat_123"}
        }
    }
    res1 = client.post("/api/bot/webhook", json=single_payload)
    assert res1.status_code == 200
    assert res1.json() == {"status": "ok"}

    # 2. Batch
    batch_payload = {
        "updates": [
            {"update_type": "bot_started", "user": {"id": 12345}},
            {"update_type": "message_created", "message": {"body": {"text": "поверка"}}}
        ]
    }
    res2 = client.post("/api/bot/webhook", json=batch_payload)
    assert res2.status_code == 200
    assert res2.json() == {"status": "ok"}

