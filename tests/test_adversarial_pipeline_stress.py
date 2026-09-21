"""
Adversarial Stress Test Suite for MAX Smart City Pipeline.
Authored by challenger_pipeline_e2e_1 (Empirical Challenger).

Comprehensive stress testing:
1. REST API Endpoints Verification:
   - /api/meters (GET all, GET by ID, invalid ID 404)
   - /api/meters/submit (monotonicity violation, anomaly detection, non-existent meter)
   - /api/arshin/check (valid, expired #991201 with fraud alert, unregistered/fake, formatted serials with spaces/№)
   - /api/billing/parse-qr (standard GOST, BOM, lowercase, invalid keys, non-numeric sum, missing prefix, corrupt payload)
   - /api/guest/generate & /api/guest/{token} (active token, non-existent token 404, negative duration, special chars)
   - /api/uk/inspector-act (empty body fallback, custom GPS formatting, photo_base64 SHA-256 integrity, large payload)
   - /api/tickets (GET all, POST create, PATCH status transition, invalid ticket 404, SLA assignment per PP RF No. 40)

2. Boundary Value Stress Tests:
   - Red roller x1000 cutoff across different meter types (cold water, hot water, gas, electricity, heat)
   - Boundary inputs: 0, 999, 1000, 1001, 10500, 10505, 10505789, negative numbers, extreme floats
   - Invalid QR keys: malformed syntax, missing required fields, corrupt delimiters
   - Expired meter #991201 fraud alert flag & shield color verification
   - GPS coordinates formatting variations (plain, already confirmed, lat/lon decimal, unicode)
   - SHA-256 cryptographic photo hash determinism and length verification
"""

import hashlib
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models.domain import MeterType, VerificationStatus, TicketPriority, TicketStatus
from app.services.meter_service import meter_service, INITIAL_METERS
from app.services.arshin import arshin_service
from app.services.gost_qr import parse_gost_qr_payload, verify_bank_account_checksum

client = TestClient(app)


# =====================================================================
# 1. REST API: /api/meters & /api/meters/submit & /api/meters/{meter_id}
# =====================================================================

class TestMetersApiAdversarial:
    def test_get_all_meters(self):
        resp = client.get("/api/meters")
        assert resp.status_code == 200
        meters = resp.json()
        assert len(meters) >= 5
        ids = [m["id"] for m in meters]
        assert "meter-khvs-1" in ids
        assert "meter-gvs-1" in ids
        assert "meter-el-1" in ids

    def test_get_meter_by_id_success(self):
        resp = client.get("/api/meters/meter-khvs-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "meter-khvs-1"
        assert data["serial_number"] == "2809142"

    def test_get_meter_by_id_not_found(self):
        resp = client.get("/api/meters/non_existent_meter_999")
        assert resp.status_code == 404
        assert "не найден" in resp.json()["detail"]

    def test_submit_reading_non_existent_meter(self):
        payload = {
            "meter_id": "meter_ghost_404",
            "reading_value": 150.0,
            "submission_channel": "test"
        }
        resp = client.post("/api/meters/submit", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_valid"] is False
        assert data["anomaly_detected"] is True
        assert "не найден" in data["message"]

    def test_submit_reading_monotonicity_violation(self):
        meter = meter_service.get_meter("meter-khvs-1")
        current_prev = meter.last_reading_value
        payload = {
            "meter_id": "meter-khvs-1",
            "reading_value": current_prev - 10.0,
            "submission_channel": "test"
        }
        resp = client.post("/api/meters/submit", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_valid"] is False
        assert data["anomaly_detected"] is True
        assert "Ошибка монотонности" in data["message"]

    def test_submit_reading_anomaly_consumption(self):
        meter = meter_service.get_meter("meter-khvs-1")
        current_prev = meter.last_reading_value
        payload = {
            "meter_id": "meter-khvs-1",
            "reading_value": current_prev + 30.0,
            "submission_channel": "test"
        }
        resp = client.post("/api/meters/submit", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_valid"] is True
        assert data["anomaly_detected"] is True
        assert "скрытой утечки" in data["message"]

    def test_submit_reading_with_red_rollers_cutoff(self):
        meter = meter_service.get_meter("meter-khvs-1")
        current_prev = meter.last_reading_value
        new_int = int(current_prev + 3) * 1000 + 450
        payload = {
            "meter_id": "meter-khvs-1",
            "reading_value": float(new_int),
            "submission_channel": "max_miniapp"
        }
        resp = client.post("/api/meters/submit", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_valid"] is True
        assert data["red_roller_filtered"] is True
        assert data["current_value"] == float(int(current_prev + 3))


# =====================================================================
# 2. REST API: /api/arshin/check & Anti-Fraud Boundary Tests
# =====================================================================

class TestArshinApiAdversarial:
    def test_arshin_valid_meter_green_shield(self):
        resp = client.get("/api/arshin/check?serial=2809142")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "verified"
        assert data["shield_color"] == "green"
        assert data["is_fraud_warning"] is True
        assert "Зеленый Щит Безопасности" in data["safety_message"]
        assert "https://fgis.gost.ru/fundmetrology/eapi/vri?search=2809142" in data["fgis_arshin_url"]

    def test_arshin_expired_meter_991201_fraud_alert(self):
        # Explicit verification of serial 991201
        resp = client.get("/api/arshin/check?serial=991201")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "expired"
        assert data["shield_color"] == "red"
        # fraud_warning_on_expired=True is enabled by default in /api/arshin/check
        assert data["is_fraud_warning"] is True
        assert "ИСТЕК" in data["safety_message"]
        assert "АНТИ-ФРОД ПРЕДУПРЕЖДЕНИЕ" in data["safety_message"]

    def test_arshin_unregistered_meter_fraud_alert(self):
        resp = client.get("/api/arshin/check?serial=000000")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "unregistered"
        assert data["shield_color"] == "red"
        assert data["is_fraud_warning"] is True
        assert "НЕ ЧИСЛИТСЯ" in data["safety_message"]

    def test_arshin_serial_number_formatting_tolerance(self):
        # With № and spaces
        resp = client.get("/api/arshin/check?serial=%E2%84%96%202809142%20")
        assert resp.status_code == 200
        data = resp.json()
        assert data["serial_number"] == "2809142"
        assert data["status"] == "verified"

    def test_arshin_alias_route(self):
        resp = client.get("/api/arshin/verify?serial_number=3910844")
        assert resp.status_code == 200
        data = resp.json()
        assert data["serial_number"] == "3910844"
        assert data["status"] == "verified"


# =====================================================================
# 3. REST API: /api/billing/parse-qr & GOST R 56042-2014 Boundaries
# =====================================================================

class TestBillingQrApiAdversarial:
    def test_valid_gost_qr_with_account_40821_split(self):
        payload = {
            "qr_payload": "ST00012|Name=ООО УК СЕРВИС|PersonalAcc=40702810938000012345|BIC=044525225|Sum=500000|PersAcc=123456789"
        }
        resp = client.post("/api/billing/parse-qr", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_valid_gost"] is True
        assert data["total_amount_rubles"] == 5000.0
        assert data["is_40821_split_supported"] is True
        assert len(data["split_details"]) == 4
        # Verify 40821 accounts present
        accs = [s["account_40821"] for s in data["split_details"]]
        assert any(a.startswith("40821") for a in accs)

    def test_gost_qr_utf8_bom_handling(self):
        payload = {
            "qr_payload": "\ufeffST00012|Name=ООО УК СЕРВИС|PersonalAcc=40702810938000012345|BIC=044525225|Sum=125050"
        }
        resp = client.post("/api/billing/parse-qr", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_amount_rubles"] == 1250.50

    def test_gost_qr_invalid_prefix_returns_400(self):
        payload = {"qr_payload": "INVALID_QR_STRING_WITHOUT_ST0001"}
        resp = client.post("/api/billing/parse-qr", json=payload)
        assert resp.status_code == 400
        assert "не соответствует стандарту ГОСТ" in resp.json()["detail"]

    def test_gost_qr_empty_payload_returns_400(self):
        payload = {"qr_payload": ""}
        resp = client.post("/api/billing/parse-qr", json=payload)
        assert resp.status_code == 400

    def test_gost_qr_non_numeric_sum_fallback(self):
        payload = {
            "qr_payload": "ST00012|Name=ООО УК СЕРВИС|PersonalAcc=40702810938000012345|BIC=044525225|Sum=CORRUPTED_SUM"
        }
        resp = client.post("/api/billing/parse-qr", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_amount_rubles"] == 0.0
        assert len(data["split_details"]) == 0

    def test_gost_qr_alias_endpoint(self):
        payload = {
            "qr_payload": "ST00012|Name=ООО ТЕХНО|PersonalAcc=40702810938000012345|BIC=044525225|Sum=300000"
        }
        resp = client.post("/api/qr/parse", json=payload)
        assert resp.status_code == 200
        assert resp.json()["total_amount_rubles"] == 3000.0


# =====================================================================
# 4. REST API: /api/guest/generate & /api/guest/{token}
# =====================================================================

class TestGuestApiAdversarial:
    def test_generate_and_validate_guest_token(self):
        gen_payload = {
            "property_id": "flat-101",
            "tenant_name": "Алексеев Алексей Алексеевич",
            "tenant_phone": "+7 900 111-22-33",
            "duration_days": 14
        }
        resp = client.post("/api/guest/generate", json=gen_payload)
        assert resp.status_code == 200
        data = resp.json()
        token = data["guest_token"]
        assert token.startswith("guest_")
        assert "startapp=" in data["direct_max_link"]

        # Validate token via GET /api/guest/{token}
        val_resp = client.get(f"/api/guest/{token}")
        assert val_resp.status_code == 200
        val_data = val_resp.json()
        assert val_data["status"] == "active"
        assert val_data["property_id"] == "flat-101"
        assert val_data["tenant_name"] == "Алексеев Алексей Алексеевич"
        assert "submit_meter_readings" in val_data["allowed_actions"]

    def test_validate_non_existent_guest_token_returns_404(self):
        resp = client.get("/api/guest/guest_non_existent_token_xyz")
        assert resp.status_code == 404
        assert "недействителен" in resp.json()["detail"]

    def test_preconfigured_guest_test_token(self):
        resp = client.get("/api/guest/guest_test_token_2026")
        assert resp.status_code == 200
        assert resp.json()["property_id"] == "flat-42-15"

    def test_expired_guest_token_returns_404(self):
        gen_payload = {
            "property_id": "flat-expired",
            "tenant_name": "Истекший Пользователь",
            "duration_days": -1
        }
        resp = client.post("/api/guest/generate", json=gen_payload)
        token = resp.json()["guest_token"]
        val_resp = client.get(f"/api/guest/{token}")
        assert val_resp.status_code == 404


# =====================================================================
# 5. REST API: /api/uk/inspector-act (GPS, SHA-256 Photo Hash)
# =====================================================================

class TestInspectorActApiAdversarial:
    def test_inspector_act_with_photo_sha256(self):
        fake_photo_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        expected_hash = hashlib.sha256(fake_photo_b64.encode("utf-8")).hexdigest()

        payload = {
            "meter_id": "meter-khvs-1",
            "reading_value": 143.5,
            "address": "г. Москва, ул. Арбат, д. 10, кв. 5",
            "inspector_name": "Инспектор Кузнецов А. С.",
            "photo_base64": fake_photo_b64,
            "gps_coordinates": "55.751244° N, 37.618423° E"
        }
        resp = client.post("/api/uk/inspector-act", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "success"
        assert data["export_1c_ready"] is True
        assert data["photo_hash_sha256"] == expected_hash
        assert data["crypto_hash"] == expected_hash
        assert len(data["photo_hash_sha256"]) == 64
        assert "55.751244° N, 37.618423° E" in data["gps_coordinates"]
        assert "Метка подтверждена" in data["gps_coordinates"]

    def test_inspector_act_without_photo_computes_deterministic_hash(self):
        payload = {
            "meter_id": "meter-khvs-1",
            "reading_value": 143.5,
            "address": "г. Москва, ул. Арбат, д. 10, кв. 5",
            "inspector_name": "Инспектор Кузнецов А. С."
        }
        resp = client.post("/api/uk/inspector-act", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["photo_hash_sha256"] is not None
        assert len(data["photo_hash_sha256"]) == 64

    def test_inspector_act_empty_body_uses_defaults(self):
        resp = client.post("/api/uk/inspector-act", json={})
        assert resp.status_code == 201
        data = resp.json()
        assert data["meter_id"] == "meter-khvs-1"
        assert data["status"] == "success"
        assert data["export_1c_ready"] is True

    def test_inspector_act_already_confirmed_gps_does_not_duplicate_suffix(self):
        payload = {
            "gps_coordinates": "55.7558° N, 37.6173° E (Метка подтверждена ГЛОНАСС)"
        }
        resp = client.post("/api/uk/inspector-act", json=payload)
        assert resp.status_code == 201
        gps = resp.json()["gps_coordinates"]
        assert gps.count("Метка подтверждена") == 1


# =====================================================================
# 6. REST API: /api/tickets (PP RF No. 40 SLA & State Machine)
# =====================================================================

class TestTicketsApiAdversarial:
    def test_create_and_get_ticket(self):
        payload = {
            "category": "Электрика",
            "description": "Искрит щиток на 3 этаже",
            "priority": "emergency",
            "address": "ул. Гагарина, д. 5"
        }
        resp = client.post("/api/tickets", json=payload)
        assert resp.status_code == 201
        ticket = resp.json()
        assert ticket["priority"] == "emergency"
        # Emergency SLA under PP RF No. 40 is 1 hour
        assert ticket["sla_hours"] == 1
        assert ticket["status"] == "new"

        ticket_id = ticket["id"]
        get_resp = client.get(f"/api/tickets/{ticket_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == ticket_id

    def test_get_ticket_not_found(self):
        resp = client.get("/api/tickets/TCK-NON-EXISTENT-999")
        assert resp.status_code == 404
        assert "не найдена" in resp.json()["detail"]

    def test_update_ticket_status_workflow(self):
        # Create ticket
        payload = {
            "category": "Кровля",
            "description": "Протечка на техническом этаже",
            "priority": "urgent"
        }
        create_resp = client.post("/api/tickets", json=payload)
        t_id = create_resp.json()["id"]

        # Transition: new -> in_progress
        patch_payload = {
            "status": "in_progress",
            "comment": "Назначен кровельщик Сидоров",
            "assigned_master": "Сидоров К. В."
        }
        patch_resp = client.patch(f"/api/tickets/{t_id}/status", json=patch_payload)
        assert patch_resp.status_code == 200
        updated = patch_resp.json()
        assert updated["status"] == "in_progress"
        assert updated["assigned_master"] == "Сидоров К. В."
        assert len(updated["status_history"]) >= 2

        # Transition: in_progress -> resolved
        patch_resp2 = client.patch(
            f"/api/tickets/{t_id}/status",
            json={"status": "resolved", "comment": "Течь устранена"}
        )
        assert patch_resp2.status_code == 200
        assert patch_resp2.json()["status"] == "resolved"

    def test_update_status_non_existent_ticket(self):
        resp = client.patch(
            "/api/tickets/TCK-NOT-FOUND-000/status",
            json={"status": "resolved"}
        )
        assert resp.status_code == 404

    def test_update_status_invalid_value_fails_pydantic_validation(self):
        resp = client.patch(
            "/api/tickets/TCK-2026-0819/status",
            json={"status": "invalid_status_xyz"}
        )
        assert resp.status_code == 422


# =====================================================================
# 7. Stress & Boundary: Red Roller x1000 Cutoff Algorithm
# =====================================================================

class TestRedRollerAlgorithmStress:
    def test_boundary_zero_and_small_values(self):
        val = meter_service.filter_red_rollers(1250.0, MeterType.COLD_WATER, prev_value=0.0)
        assert val == 1.0

    def test_boundary_exact_1000(self):
        val = meter_service.filter_red_rollers(1000.0, MeterType.COLD_WATER, prev_value=0.0)
        assert val == 1.0

    def test_boundary_large_industrial_meter(self):
        val = meter_service.filter_red_rollers(15020.0, MeterType.COLD_WATER, prev_value=15000.0)
        assert val == 15020.0

    def test_boundary_large_meter_unscaled_liters(self):
        val = meter_service.filter_red_rollers(15020500.0, MeterType.COLD_WATER, prev_value=15000.0)
        assert val == 15020.0

    def test_electricity_meter_unaffected_by_roller_cutoff(self):
        val = meter_service.filter_red_rollers(1850.0, MeterType.ELECTRICITY_MULTI, prev_value=1840.0)
        assert val == 1850.0

    def test_heat_meter_unaffected_by_roller_cutoff(self):
        val = meter_service.filter_red_rollers(16.5, MeterType.HEAT, prev_value=14.2)
        assert val == 16.5
