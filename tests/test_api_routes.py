"""
Integration tests for FastAPI REST API endpoints.
"""

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

def test_night_flow_endpoint():
    payload = {
        "entrance_id": 1,
        "napkin_test_result": "wet"
    }
    res = client.post("/api/night-flow/diagnose", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["apartment_leak_detected"] is True
    assert data["reward_eligible"] is True

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
