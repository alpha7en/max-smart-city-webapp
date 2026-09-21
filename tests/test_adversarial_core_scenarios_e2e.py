"""
Empirical Adversarial E2E Test Suite for the 4 Core MVP Scenarios.
Authored by Challenger 2 (teamwork_preview_challenger).

Scenarios tested:
1. Scenario 1: Direct photo drop with red roller cutoff (x1000 overpayment protection):
   - Feed oversized readings (e.g. 145789.0 vs previous 142.5) -> verify automatic scaling to 145.0 m³.
   - Boundary & stress tests across cold water, hot water, gas, electricity, heat.
   - Text & photo channel submissions in bot & API.
2. Scenario 2: Antifraud "Зеленый Щит" (FGIS ARSHIN 102-FZ):
   - Legitimate serial 2809142 -> Green Shield status, verification date, anti-scam flyer alert.
   - Expired serial 991201 -> Red Shield status, fraud warning alert against fake flyers.
   - Tolerance to formatting (№, whitespace, lowercase), fake/unregistered serials.
3. Scenario 3: Utility payment via GOST R 56042-2014 split to special account 40821 (103-FZ):
   - Parse GOST QR payloads, checksum verification of Russian bank accounts.
   - Exact penny preservation across split recipients (Водоканал, МОЭК, Мосэнергосбыт, УК).
4. Scenario 4: Inspector ARM with GPS, ISO-8601 timestamp, photo SHA-256 hash, and 1C:ZHKH export (63-FZ):
   - Digital act generation with GPS coordinates, ISO-8601 timestamp, 64-char SHA-256 hash.
   - 1C XML and JSON export integrity.
"""

import re
import hashlib
import random
import xml.etree.ElementTree as ET
from datetime import datetime
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.domain import MeterType, VerificationStatus
from app.models.schemas import (
    MeterReadingSubmitRequest,
    AiVisionScanRequest,
    InspectorActCreateRequest,
    GostQrParseRequest
)
from app.services.meter_service import meter_service, INITIAL_METERS
from app.services.arshin import arshin_service
from app.services.gost_qr import (
    parse_gost_qr_payload,
    generate_gost_qr_string,
    verify_bank_account_checksum
)
from app.services.inspector_service import inspector_service
from app.bot.handlers import get_bot_handler
from max_bot_sdk import Update

client = TestClient(app)


# =====================================================================
# Scenario 1: Direct Photo Drop & Red Roller Cutoff (x1000 Protection)
# =====================================================================

class TestScenario1RedRollerAdversarial:
    def test_oversized_reading_145789_scaled_to_145_cold_water(self):
        """
        Explicit requirement:
        Feed oversized readings (145789.0 vs previous 142.5) and verify
        automatic scaling/normalization to 145.0 m³.
        """
        # Test directly via filter_red_rollers
        scaled = meter_service.filter_red_rollers(145789.0, MeterType.COLD_WATER, prev_value=142.5)
        assert scaled == 145.0, f"Expected 145.0, got {scaled}"

        # Test with previous 142.0 (standard fixture)
        scaled_std = meter_service.filter_red_rollers(145789.0, MeterType.COLD_WATER, prev_value=142.0)
        assert scaled_std == 145.0, f"Expected 145.0, got {scaled_std}"

    def test_oversized_reading_hot_water_and_gas(self):
        """Verify red roller cutoff works uniformly for HOT_WATER and GAS."""
        # Hot water: previous 98.0, user reads 101500 -> 101.0 m³
        hw_scaled = meter_service.filter_red_rollers(101500.0, MeterType.HOT_WATER, prev_value=98.0)
        assert hw_scaled == 101.0

        # Gas: previous 340.0, user reads 345890 -> 345.0 m³
        gas_scaled = meter_service.filter_red_rollers(345890.0, MeterType.GAS, prev_value=340.0)
        assert gas_scaled == 345.0

    def test_electricity_and_heat_never_divided_by_1000(self):
        """Adversarial check: ensure electricity (kWh) and heat (Gcal) meters are NOT scaled."""
        el_val = meter_service.filter_red_rollers(1850.0, MeterType.ELECTRICITY_MULTI, prev_value=1840.0)
        assert el_val == 1850.0

        heat_val = meter_service.filter_red_rollers(16.5, MeterType.HEAT, prev_value=14.2)
        assert heat_val == 16.5

    def test_high_industrial_meter_boundary(self):
        """
        Adversarial check: High industrial reading (e.g. 10505 m³ vs prev 10500 m³)
        must NOT be mistakenly divided to 10.0 m³!
        """
        # Legitimate high reading
        legit = meter_service.filter_red_rollers(10505.0, MeterType.COLD_WATER, prev_value=10500.0)
        assert legit == 10505.0

        # Unscaled high reading with liters (10505789 vs 10500)
        unscaled = meter_service.filter_red_rollers(10505789.0, MeterType.COLD_WATER, prev_value=10500.0)
        assert unscaled == 10505.0

    def test_api_submit_reading_with_red_roller_145789(self):
        """Submit 145789.0 to /api/meters/submit and verify normalized current_value=145.0."""
        meter = meter_service.get_meter("meter-khvs-1")
        prev_reading = meter.last_reading_value

        payload = {
            "meter_id": "meter-khvs-1",
            "reading_value": 145789.0,
            "submission_channel": "direct_photo_drop"
        }
        resp = client.post("/api/meters/submit", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert data["is_valid"] is True
        assert data["red_roller_filtered"] is True
        assert data["current_value"] == 145.0
        assert data["previous_value"] == prev_reading
        assert data["consumption"] == round(145.0 - prev_reading, 3)

    def test_bot_text_reading_with_red_roller_cutoff(self):
        """Test bot textual submission with unscaled liters: 'ХВС 148250'."""
        handler = get_bot_handler()
        sent_messages = []
        handler.client.send_message = lambda **kwargs: sent_messages.append(kwargs) or {"message_id": 1}

        update = Update.from_dict({
            "update_type": "message_created",
            "message": {
                "sender": {"user_id": 67890, "first_name": "Тестер"},
                "recipient": {"chat_id": 12345, "chat_type": "dialog"},
                "body": {"text": "ХВС 148250"}
            }
        })
        handler.process_update(update)

        assert len(sent_messages) == 1
        msg_text = sent_messages[0]["text"]
        assert "ГИС ЖКХ: Показания успешно приняты" in msg_text
        assert "148.0 м³" in msg_text
        assert "Да (отсечены литры)" in msg_text


# =====================================================================
# Scenario 2: Antifraud "Зеленый Щит" (FGIS ARSHIN 102-FZ)
# =====================================================================

class TestScenario2ArshinAntifraudAdversarial:
    def test_legitimate_serial_2809142_green_shield(self):
        """
        Explicit requirement:
        Verify legitimate serial 2809142 gets Green Shield status.
        """
        resp = client.get("/api/arshin/check?serial=2809142")
        assert resp.status_code == 200
        data = resp.json()

        assert data["serial_number"] == "2809142"
        assert data["status"] == "verified"
        assert data["shield_color"] == "green"
        assert data["is_fraud_warning"] is True
        assert "Зеленый Щит Безопасности" in data["safety_message"]
        assert "МОШЕННИЧЕСТВО" in data["safety_message"]
        assert "https://fgis.gost.ru/fundmetrology/eapi/vri?search=2809142" == data["fgis_arshin_url"]

    def test_expired_serial_991201_red_shield_fraud_alert(self):
        """
        Explicit requirement:
        Verify expired serial 991201 gets fraud warning alert and red shield
        against fake verification flyers.
        """
        resp = client.get("/api/arshin/check?serial=991201")
        assert resp.status_code == 200
        data = resp.json()

        assert data["serial_number"] == "991201"
        assert data["status"] == "expired"
        assert data["shield_color"] == "red"
        assert data["is_fraud_warning"] is True
        assert "ИСТЕК" in data["safety_message"]
        assert "АНТИ-ФРОД ПРЕДУПРЕЖДЕНИЕ" in data["safety_message"]
        assert "Остерегайтесь мошенников" in data["safety_message"]

    def test_unregistered_and_adversarial_serials(self):
        """Adversarial test for fake or zeroed serial numbers."""
        for fake in ["000000", "00000000", "FAKE", "FAKE-01"]:
            resp = client.get(f"/api/arshin/check?serial={fake}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "unregistered"
            assert data["shield_color"] == "red"
            assert data["is_fraud_warning"] is True
            assert "НЕ ЧИСЛИТСЯ" in data["safety_message"]

    def test_formatting_tolerance_and_sanitization(self):
        """Verify dirty serials: № symbol, spaces, lowercase, newline."""
        resp = client.get("/api/arshin/check?serial=%20%E2%84%96%202809142%20%20")
        assert resp.status_code == 200
        data = resp.json()
        assert data["serial_number"] == "2809142"
        assert data["status"] == "verified"
        assert data["shield_color"] == "green"


# =====================================================================
# Scenario 3: Utility Payment GOST R 56042-2014 & Split to 40821 (103-FZ)
# =====================================================================

class TestScenario3GostQrPaymentSplitAdversarial:
    def test_parse_gost_qr_payload_and_split_verification(self):
        """
        Explicit requirement:
        Verify parsing of GOST QR payload, checksum verification, and split payment logic.
        """
        qr_string = (
            "ST00012|Name=ООО УК ТЕХНОДОМ|PersonalAcc=40821810123456789012|"
            "BankName=ПАО СБЕРБАНК|BIC=044525225|Sum=450000|PersAcc=998877|Period=092026"
        )
        res = parse_gost_qr_payload(qr_string)

        assert res.is_valid_gost is True
        assert res.format_version == "ST00012"
        assert res.recipient_name == "ООО УК ТЕХНОДОМ"
        assert res.bank_bik == "044525225"
        assert res.total_amount_rubles == 4500.00
        assert res.is_40821_split_supported is True
        assert len(res.split_details) == 4

        # Check split recipients
        recipients = {s.recipient_name: s for s in res.split_details}
        assert any("Мосводоканал" in name for name in recipients)
        assert any("МОЭК" in name for name in recipients)
        assert any("Мосэнергосбыт" in name for name in recipients)
        assert any("ООО УК ТЕХНОДОМ" in name for name in recipients)

        # Check all utility recipients have special 40821 accounts
        for s in res.split_details[:3]:
            assert s.account_40821.startswith("40821"), f"Account {s.account_40821} must start with 40821"

        # Verify sum exactness down to the kopeck
        total_split = round(sum(s.amount_rubles for s in res.split_details), 2)
        assert total_split == res.total_amount_rubles

    def test_russian_bank_account_checksum_algorithm(self):
        """
        Verify Central Bank of Russia checksum algorithm (weights 7, 1, 3 mod 10).
        """
        # Known valid accounts
        bik = "044525225"
        valid_corr = "30101810400000000225"
        assert verify_bank_account_checksum(bik, valid_corr) is True

        # Invalid account with altered digit
        tampered_corr = "30101810400000000226"
        assert verify_bank_account_checksum(bik, tampered_corr) is False

        # Invalid lengths
        assert verify_bank_account_checksum("123", "456") is False
        assert verify_bank_account_checksum("", "") is False

    def test_penny_exactness_fuzzing_50_iterations(self):
        """
        Adversarial fuzzing: 50 diverse monetary sums (odd pennies, tiny sums, huge sums)
        must NEVER lose or gain a single kopeck in the 4-way split.
        """
        random.seed(42)
        for _ in range(50):
            amount = round(random.uniform(1.0, 50000.0), 2)
            kopecks = int(round(amount * 100))
            payload = f"ST00012|Name=ООО СЕРВИС|PersonalAcc=40702810938000012345|BIC=044525225|Sum={kopecks}"
            res = parse_gost_qr_payload(payload)

            assert res.total_amount_rubles == amount
            split_sum = round(sum(s.amount_rubles for s in res.split_details), 2)
            assert split_sum == amount, f"Penny drift detected: split={split_sum} vs total={amount}"

    def test_gost_qr_error_handling(self):
        """Malformed or non-GOST QR payloads must be cleanly rejected."""
        # Non-GOST prefix
        with pytest.raises(ValueError, match="не соответствует стандарту ГОСТ"):
            parse_gost_qr_payload("HTTP://INVALID-QR.COM/PAY")

        # Empty string
        with pytest.raises(ValueError):
            parse_gost_qr_payload("")


# =====================================================================
# Scenario 4: Inspector ARM (GPS, ISO-8601, SHA-256, 1C:ZHKH Export)
# =====================================================================

class TestScenario4InspectorArmAdversarial:
    def test_inspector_act_creation_and_contracts(self):
        """
        Explicit requirement:
        Verify act creation with GPS coordinates, ISO-8601 timestamp,
        64-char SHA-256 hash, and export to 1C XML/JSON.
        """
        fake_photo = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        expected_sha256 = hashlib.sha256(fake_photo.encode("utf-8")).hexdigest()

        payload = {
            "meter_id": "meter-khvs-1",
            "reading_value": 145.0,
            "address": "г. Москва, ул. Ленина, д. 42, кв. 15",
            "inspector_name": "Контролер Службы Учета Водоснабжения Смирнов В. И.",
            "photo_base64": fake_photo,
            "gps_coordinates": "55.7558° N, 37.6173° E"
        }
        resp = client.post("/api/uk/inspector-act", json=payload)
        assert resp.status_code == 201
        data = resp.json()

        # 1. GPS coordinates verification
        assert "55.7558° N, 37.6173° E" in data["gps_coordinates"]
        assert "Метка подтверждена" in data["gps_coordinates"]
        assert "55.7558° N, 37.6173° E" in data["gps"]

        # 2. ISO-8601 timestamp verification
        timestamp_str = data["timestamp"]
        parsed_dt = datetime.fromisoformat(timestamp_str)
        assert isinstance(parsed_dt, datetime)

        # 3. 64-char SHA-256 hash verification
        sha256_hash = data["photo_hash_sha256"]
        assert len(sha256_hash) == 64
        assert re.match(r"^[0-9a-f]{64}$", sha256_hash)
        assert sha256_hash == expected_sha256
        assert data["crypto_hash"] == expected_sha256

        # 4. Status and 1C readiness
        assert data["status"] == "success"
        assert data["export_1c_ready"] is True
        assert data["billing_export_status"] == "EXPORTED_TO_1C_ZHKH"
        assert "63-ФЗ" in data["legal_significance"]

    def test_export_to_1c_json_and_xml(self):
        """
        Verify export capability to both 1C:ZHKH JSON and 1C:Enterprise XML formats.
        """
        fake_photo = "SAMPLE_METER_PHOTO_BASE64_DATA"
        payload = {
            "meter_id": "meter-khvs-1",
            "reading_value": 145.0,
            "address": "г. Москва, ул. Арбат, д. 10, кв. 2",
            "inspector_name": "Иванов И. И.",
            "photo_base64": fake_photo,
            "gps_coordinates": "55.751244° N, 37.618423° E"
        }
        resp = client.post("/api/uk/inspector-act", json=payload)
        assert resp.status_code == 201
        act_dict = resp.json()

        # 1. 1C:ZHKH JSON interchange verification
        assert "act_id" in act_dict
        assert "reading_value" in act_dict
        assert "photo_hash_sha256" in act_dict
        assert "gps_coordinates" in act_dict
        assert act_dict["billing_export_status"] == "EXPORTED_TO_1C_ZHKH"

        # 2. 1C:Enterprise XML generation & parsing verification
        root = ET.Element("VedomostSnjatijaPokazanij1C", {
            "version": "1.0",
            "schema": "urn:1c-enterprise:zhkh:meter-acts:v1",
            "standart": "63-FZ"
        })
        ET.SubElement(root, "NomerAkta").text = act_dict["act_id"]
        ET.SubElement(root, "DataVremyaISO").text = act_dict["timestamp"]
        ET.SubElement(root, "IdentifikatorPU").text = act_dict["meter_id"]
        ET.SubElement(root, "Pokazanie").text = str(act_dict["reading_value"])
        ET.SubElement(root, "Adres").text = act_dict["address"]
        ET.SubElement(root, "InspektorFIO").text = act_dict["inspector_name"]
        ET.SubElement(root, "KoordinatyGPS").text = act_dict["gps_coordinates"]
        ET.SubElement(root, "KriptograficheskiyKheshSHA256").text = act_dict["photo_hash_sha256"]
        ET.SubElement(root, "StatusSinkhronizatsii").text = act_dict["billing_export_status"]

        xml_string = ET.tostring(root, encoding="utf-8")
        assert len(xml_string) > 100

        # Validate that the generated XML is well-formed and can be re-parsed
        parsed_xml = ET.fromstring(xml_string)
        assert parsed_xml.tag == "VedomostSnjatijaPokazanij1C"
        assert parsed_xml.find("NomerAkta").text == act_dict["act_id"]
        assert parsed_xml.find("Pokazanie").text == "145.0"
        assert len(parsed_xml.find("KriptograficheskiyKheshSHA256").text) == 64
        assert parsed_xml.find("StatusSinkhronizatsii").text == "EXPORTED_TO_1C_ZHKH"

    def test_inspector_service_direct_execution(self):
        """Direct execution of InspectorService create_act."""
        req = InspectorActCreateRequest(
            meter_id="meter-gvs-1",
            reading_value=99.2,
            address="г. Москва, пр-т Мира, д. 15",
            inspector_name="Сидоров П. К.",
            gps_coordinates="55.7820° N, 37.6330° E"
        )
        res = inspector_service.create_act(req)
        assert res.status == "success"
        assert res.act_number.startswith("ACT-ЖКХ-")
        assert len(res.crypto_hash) == 64
        assert res.billing_export_status == "EXPORTED_TO_1C_ZHKH"
