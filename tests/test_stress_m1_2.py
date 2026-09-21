"""
Challenger M1-2 Empirical Stress Test Suite.
Covers:
1. Arshin Anti-Fraud (991201 vs known vs unknown vs fakes, serial formatting)
2. Gas Meter & Red Rollers Cutoff (monotonicity, red roller truncation, >50m³ anomalies)
3. MAX Bot Webhook Payload Handling (message_created, attachments, captions, callbacks, fuzzing)
"""

import copy
import json
import pytest
from datetime import date
from fastapi.testclient import TestClient

from app.main import app
from app.models.domain import MeterType, VerificationStatus
from app.models.schemas import MeterReadingSubmitRequest
from app.services.arshin import ArshinService, ARSHIN_MOCK_DATABASE, arshin_service
from app.services.meter_service import MeterService, INITIAL_METERS, meter_service
from app.bot.handlers import BotHandler, get_bot_handler
from app.bot.client import MaxBotClient

client = TestClient(app)

# =====================================================================
# 1. ARSHIN ANTI-FRAUD EMPIRICAL STRESS TESTS
# =====================================================================

class TestArshinAntiFraudEmpirical:
    """Stress tests for Arshin Anti-Fraud Shield under 102-FZ."""

    def test_arshin_expired_meter_991201_full_contract(self):
        """Verify serial 991201 via API matches all M1 SCOPE.md and DISPATCH requirements."""
        res = client.get("/api/arshin/check", params={"serial": "991201"})
        assert res.status_code == 200
        data = res.json()

        # Canonical contract requirements
        assert data["serial_number"] == "991201"
        assert data["status"] == "expired"
        assert data["shield_color"] == "red"
        assert data["is_fraud_warning"] is True

        # Anti-fraud warning content
        msg = data["safety_message"]
        assert "ИСТЕК" in msg
        assert "МОШЕННИК" in msg.upper()
        assert "листовк" in msg.lower()

        # Metadata validation
        assert "ЗАО «Метрология»" in data["organization_name"]
        assert data["verification_date"] == "2018-01-10"
        assert data["valid_until"] == "2024-01-10"
        assert "https://fgis.gost.ru/fundmetrology/eapi/vri?search=991201" == data["fgis_arshin_url"]

    def test_arshin_serial_variations_and_sanitization(self):
        """Verify serial number formatting robustness (spaces, symbols, casing)."""
        variations = [
            "991201",
            " 991201 ",
            "№991201",
            "№ 991201",
            "\t991201\n",
        ]
        for serial_input in variations:
            res = client.get("/api/arshin/check", params={"serial": serial_input})
            assert res.status_code == 200, f"Failed on input: {repr(serial_input)}"
            data = res.json()
            assert data["serial_number"] == "991201"
            assert data["status"] == "expired"
            assert data["shield_color"] == "red"
            assert data["is_fraud_warning"] is True

    def test_arshin_all_known_database_meters(self):
        """Stress test all known meters in ARSHIN_MOCK_DATABASE via API."""
        for serial, expected_info in ARSHIN_MOCK_DATABASE.items():
            res = client.get("/api/arshin/check", params={"serial": serial})
            assert res.status_code == 200
            data = res.json()

            assert data["serial_number"] == serial
            assert data["organization_name"] == expected_info["org"]
            assert data["verification_date"] == expected_info["verif_date"].isoformat()
            assert data["valid_until"] == expected_info["valid_until"].isoformat()

            if serial == "991201":
                assert data["status"] == "expired"
                assert data["shield_color"] == "red"
                assert data["is_fraud_warning"] is True
            else:
                assert data["status"] == "verified"
                assert data["shield_color"] == "green"
                assert data["is_fraud_warning"] is True
                assert "Зеленый Щит Безопасности" in data["safety_message"]

    def test_arshin_gas_meter_5540912(self):
        """Verify gas meter 5540912 in Arshin."""
        res = client.get("/api/arshin/check", params={"serial": "5540912"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "verified"
        assert data["shield_color"] == "green"
        assert "ВК-G4" in data["safety_message"] or "Газовый" in data["safety_message"]
        assert "ФБУ «РОСТЕСТ-МОСКВА»" in data["organization_name"]

    def test_arshin_fake_and_unregistered_serials(self):
        """Verify explicit fake and unregistered meter detection."""
        fakes = ["000000", "00000000", "FAKE", "FAKE01", "FAKE-WATER-99"]
        for fake_serial in fakes:
            res = client.get("/api/arshin/check", params={"serial": fake_serial})
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "unregistered"
            assert data["shield_color"] == "red"
            assert data["is_fraud_warning"] is True
            assert "НЕ ЧИСЛИТСЯ" in data["safety_message"]
            assert "100% обман мошенников" in data["safety_message"]

    def test_arshin_unknown_fallback_classification(self):
        """Verify unknown serial fallbacks (expired vs verified)."""
        # Unknown ending with '000' -> fallback expired
        res_exp = client.get("/api/arshin/check", params={"serial": "472000"})
        assert res_exp.status_code == 200
        assert res_exp.json()["status"] == "expired"
        assert res_exp.json()["shield_color"] == "red"
        assert res_exp.json()["is_fraud_warning"] is True

        # Unknown ending with '999999' -> fallback expired
        res_exp2 = client.get("/api/arshin/check", params={"serial": "999999"})
        assert res_exp2.status_code == 200
        assert res_exp2.json()["status"] == "expired"

        # Arbitrary unknown serial -> fallback verified with green shield
        res_un = client.get("/api/arshin/check", params={"serial": "8472910"})
        assert res_un.status_code == 200
        assert res_un.json()["status"] == "verified"
        assert res_un.json()["shield_color"] == "green"
        assert res_un.json()["is_fraud_warning"] is True

    def test_arshin_alias_verify_endpoint(self):
        """Verify alias GET /api/arshin/verify."""
        res = client.get("/api/arshin/verify", params={"serial_number": "991201"})
        assert res.status_code == 200
        data = res.json()
        assert data["serial_number"] == "991201"
        assert data["status"] == "expired"
        assert data["shield_color"] == "red"
        assert data["is_fraud_warning"] is True

    def test_arshin_missing_param_validation(self):
        """Verify API response on missing parameter."""
        res = client.get("/api/arshin/check")
        assert res.status_code == 422  # Missing required query param


# =====================================================================
# 2. GAS METER & RED ROLLER CUTOFF EMPIRICAL STRESS TESTS
# =====================================================================

class TestGasMeterStressValidation:
    """Stress tests for Gas Meter validation, red roller filter, monotonicity, and anomaly detection."""

    @pytest.fixture(autouse=True)
    def reset_meter_service_state(self):
        """Fixture ensuring isolated meter service state across test executions."""
        original_meters = {k: copy.deepcopy(v) for k, v in meter_service._meters.items()}
        yield
        meter_service._meters = original_meters

    def test_gas_meter_metadata_conformance(self):
        """Verify meter-gas-1 configuration meets Russian metrological specs."""
        meter = meter_service.get_meter("meter-gas-1")
        assert meter is not None
        assert meter.meter_type == MeterType.GAS
        assert meter.serial_number == "5540912"
        assert meter.unit == "м³"
        assert meter.decimal_digits == 3
        assert meter.installation_place == "Кухня"
        assert meter.last_reading_value == 340.0

    def test_gas_meter_valid_submission(self):
        """Submit regular gas consumption (e.g. 5.5 m³)."""
        req = MeterReadingSubmitRequest(
            meter_id="meter-gas-1",
            reading_value=345.5,
            submission_channel="test_harness"
        )
        res = meter_service.validate_and_submit_reading(req)
        assert res.is_valid is True
        assert res.consumption == 5.5
        assert res.current_value == 345.5
        assert res.previous_value == 340.0
        assert res.anomaly_detected is False
        assert res.red_roller_filtered is False

        # State updated
        assert meter_service.get_meter("meter-gas-1").last_reading_value == 345.5

    def test_gas_meter_zero_consumption_permitted(self):
        """Equal reading (zero consumption) is valid (vacation/idle)."""
        req = MeterReadingSubmitRequest(
            meter_id="meter-gas-1",
            reading_value=340.0,
            submission_channel="test_harness"
        )
        res = meter_service.validate_and_submit_reading(req)
        assert res.is_valid is True
        assert res.consumption == 0.0
        assert res.current_value == 340.0
        assert res.anomaly_detected is False

    def test_gas_meter_monotonicity_violation_rejection(self):
        """Reading lower than previous must be rejected and state NOT updated."""
        req = MeterReadingSubmitRequest(
            meter_id="meter-gas-1",
            reading_value=339.9,
            submission_channel="test_harness"
        )
        res = meter_service.validate_and_submit_reading(req)
        assert res.is_valid is False
        assert res.anomaly_detected is True
        assert "Ошибка монотонности" in res.message
        assert res.consumption == -0.1

        # Verify state did not change
        assert meter_service.get_meter("meter-gas-1").last_reading_value == 340.0

    def test_gas_meter_red_roller_cutoff_1000x_prevention(self):
        """User types all 8 digits from meter face: 343250 (343 m³ and 250 liters)."""
        req = MeterReadingSubmitRequest(
            meter_id="meter-gas-1",
            reading_value=343250.0,
            submission_channel="test_harness"
        )
        res = meter_service.validate_and_submit_reading(req)
        assert res.is_valid is True
        assert res.red_roller_filtered is True
        assert res.current_value == 343.0
        assert res.consumption == 3.0
        assert res.anomaly_detected is False

    def test_gas_meter_red_roller_filter_standalone(self):
        """Test red roller filter function directly across multiple values."""
        svc = MeterService()
        # 1. Unscaled with prev_value
        assert svc.filter_red_rollers(345812.0, MeterType.GAS, prev_value=340.0) == 345.0
        # 2. Normal value without fraction confusion
        assert svc.filter_red_rollers(344.25, MeterType.GAS, prev_value=340.0) == 344.25
        # 3. Unscaled with prev_value None (>10000 fallback)
        assert svc.filter_red_rollers(345000.0, MeterType.GAS, prev_value=None) == 345.0
        # 4. Legitimate high reading (e.g. commercial/boiler gas meter at 10505 m³)
        assert svc.filter_red_rollers(10505.0, MeterType.GAS, prev_value=10500.0) == 10505.0

    def test_gas_meter_consumption_anomaly_thresholds(self):
        """Verify 50 m³ anomaly threshold for gas."""
        svc = MeterService()

        # Case A: exactly 50.0 m³ diff -> boundary not exceeded
        req_50 = MeterReadingSubmitRequest(
            meter_id="meter-gas-1",
            reading_value=390.0,
            submission_channel="test_harness"
        )
        res_50 = svc.validate_and_submit_reading(req_50)
        assert res_50.is_valid is True
        assert res_50.consumption == 50.0
        assert res_50.anomaly_detected is False

        # Case B: 50.1 m³ diff -> anomaly triggered
        req_50_1 = MeterReadingSubmitRequest(
            meter_id="meter-gas-1",
            reading_value=440.1,
            submission_channel="test_harness"
        )
        res_50_1 = svc.validate_and_submit_reading(req_50_1)
        assert res_50_1.is_valid is True
        assert res_50_1.consumption == 50.1
        assert res_50_1.anomaly_detected is True
        assert "превышает среднемесячную норму" in res_50_1.message

    def test_gas_meter_api_endpoint_submit(self):
        """Submit gas reading via POST /api/meters/submit endpoint."""
        payload = {
            "meter_id": "meter-gas-1",
            "reading_value": 344.8,
            "submission_channel": "max_webapp_test"
        }
        res = client.post("/api/meters/submit", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["is_valid"] is True
        assert data["consumption"] == 4.8
        assert data["current_value"] == 344.8
        assert data["anomaly_detected"] is False


# =====================================================================
# 3. BOT WEBHOOK PAYLOAD HANDLING EMPIRICAL STRESS TESTS
# =====================================================================

class MockMaxBotClient:
    """Mock client capturing all outbound bot API interactions."""
    def __init__(self):
        self.sent_messages = []
        self.answered_callbacks = []

    def send_message(self, **kwargs):
        self.sent_messages.append(kwargs)
        return {"status": "ok", "message_id": f"msg_{len(self.sent_messages)}"}

    def answer_callback(self, callback_id, notification=None):
        self.answered_callbacks.append({"callback_id": callback_id, "notification": notification})
        return {"status": "ok"}


class TestBotWebhookStressValidation:
    """Empirical stress testing of bot webhook dispatcher, photo routing, and callback queries."""

    @pytest.fixture
    def mock_handler(self, monkeypatch):
        mock_client = MockMaxBotClient()
        handler = BotHandler(mock_client)
        # Patch singleton provider in app.bot.handlers
        monkeypatch.setattr("app.bot.handlers.get_bot_handler", lambda: handler)
        return handler, mock_client

    def test_webhook_bot_started_event(self, mock_handler):
        handler, mock_client = mock_handler
        payload = {
            "update_type": "bot_started",
            "chat_id": "chat_user_777",
            "user": {"id": 777, "first_name": "Константин"}
        }
        res = client.post("/api/bot/webhook", json=payload)
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

        assert len(mock_client.sent_messages) == 1
        msg = mock_client.sent_messages[0]
        assert "Константин" in msg["text"]
        assert "MAX Умный Дом" in msg["text"]
        assert msg["chat_id"] == "chat_user_777"
        assert msg["buttons"] is not None

    def test_webhook_photo_with_gas_caption(self, mock_handler):
        handler, mock_client = mock_handler
        payload = {
            "update_type": "message_created",
            "message": {
                "body": {
                    "text": "Счетчик газа на кухне",
                    "attachments": [{"type": "image", "url": "https://example.com/gas.jpg"}]
                },
                "sender": {"id": 101, "first_name": "Елена"},
                "recipient": {"chat_id": "chat_gas_101"}
            }
        }
        res = client.post("/api/bot/webhook", json=payload)
        assert res.status_code == 200

        assert len(mock_client.sent_messages) == 1
        msg = mock_client.sent_messages[0]
        assert "Газоснабжение" in msg["text"]
        assert "5540912" in msg["text"]  # Serial for gas meter
        assert "ФГИС «АРШИН»" in msg["text"]

        # Check inline keyboard has gas confirmation and utility selector
        buttons = msg["buttons"]
        callbacks = [btn["payload"] for row in buttons for btn in row if "payload" in btn]
        assert "submit_meter_meter-gas-1" in callbacks
        assert "rescan_meter_gas" in callbacks
        assert "rescan_meter_cold_water" in callbacks

    def test_webhook_photo_with_cold_water_caption(self, mock_handler):
        handler, mock_client = mock_handler
        payload = {
            "update_type": "message_created",
            "message": {
                "body": {
                    "text": "Показания ХВС санузел",
                    "attachments": [{"type": "image", "url": "https://example.com/khvs.jpg"}]
                },
                "sender": {"id": 102},
                "recipient": {"chat_id": "chat_khvs_102"}
            }
        }
        res = client.post("/api/bot/webhook", json=payload)
        assert res.status_code == 200

        msg = mock_client.sent_messages[0]
        assert "ХВС (Холодная вода)" in msg["text"]
        assert "2809142" in msg["text"]
        callbacks = [btn["payload"] for row in msg["buttons"] for btn in row if "payload" in btn]
        assert "submit_meter_meter-khvs-1" in callbacks

    def test_webhook_photo_without_caption_defaults_to_cold_water(self, mock_handler):
        handler, mock_client = mock_handler
        payload = {
            "update_type": "message_created",
            "message": {
                "body": {
                    "text": "",
                    "attachments": [{"type": "photo", "url": "https://example.com/meter.jpg"}]
                },
                "sender": {"id": 103},
                "recipient": {"chat_id": "chat_103"}
            }
        }
        res = client.post("/api/bot/webhook", json=payload)
        assert res.status_code == 200

        msg = mock_client.sent_messages[0]
        assert "ХВС (Холодная вода)" in msg["text"]
        assert "автоматическое распознавание" in msg["text"]

    def test_webhook_callback_rescan_meter_gas(self, mock_handler):
        handler, mock_client = mock_handler
        payload = {
            "update_type": "message_callback",
            "callback": {
                "callback_id": "cb_gas_42",
                "payload": "rescan_meter_gas"
            },
            "sender": {"id": 201},
            "chat_id": "chat_201"
        }
        res = client.post("/api/bot/webhook", json=payload)
        assert res.status_code == 200

        assert len(mock_client.answered_callbacks) == 1
        assert mock_client.answered_callbacks[0]["callback_id"] == "cb_gas_42"
        assert len(mock_client.sent_messages) == 1
        assert "Газоснабжение" in mock_client.sent_messages[0]["text"]

    def test_webhook_callback_rescan_meter_cold_water(self, mock_handler):
        handler, mock_client = mock_handler
        payload = {
            "update_type": "message_callback",
            "callback": {
                "callback_id": "cb_khvs_43",
                "payload": "rescan_meter_cold_water"
            },
            "sender": {"id": 202},
            "chat_id": "chat_202"
        }
        res = client.post("/api/bot/webhook", json=payload)
        assert res.status_code == 200

        assert len(mock_client.answered_callbacks) == 1
        assert mock_client.answered_callbacks[0]["callback_id"] == "cb_khvs_43"
        assert "ХВС (Холодная вода)" in mock_client.sent_messages[0]["text"]

    def test_webhook_callback_submit_meter_gas(self, mock_handler):
        handler, mock_client = mock_handler
        payload = {
            "update_type": "message_callback",
            "callback": {
                "callback_id": "cb_sub_gas_44",
                "payload": "submit_meter_meter-gas-1"
            },
            "sender": {"id": 203},
            "chat_id": "chat_203"
        }
        res = client.post("/api/bot/webhook", json=payload)
        assert res.status_code == 200

        assert len(mock_client.answered_callbacks) == 1
        assert len(mock_client.sent_messages) == 1
        sent = mock_client.sent_messages[0]["text"]
        assert "Показания успешно зафиксированы" in sent
        assert "Газоснабжение" in sent

    def test_webhook_batch_updates_handling(self, mock_handler):
        handler, mock_client = mock_handler
        batch = {
            "updates": [
                {
                    "update_type": "bot_started",
                    "chat_id": "c1",
                    "user": {"id": 1, "first_name": "User 1"}
                },
                {
                    "update_type": "message_created",
                    "message": {
                        "body": {"text": "/meters"},
                        "sender": {"id": 2},
                        "recipient": {"chat_id": "c2"}
                    }
                },
                {
                    "update_type": "message_callback",
                    "callback": {"callback_id": "cb3", "payload": "cmd_inspector"},
                    "sender": {"id": 3},
                    "chat_id": "c3"
                }
            ]
        }
        res = client.post("/api/bot/webhook", json=batch)
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}
        assert len(mock_client.sent_messages) == 3
        assert len(mock_client.answered_callbacks) == 1

    def test_webhook_adversarial_and_fuzz_payloads(self, mock_handler):
        """Webhook endpoint must never crash (500) on malformed or empty payloads."""
        fuzz_payloads = [
            {},
            {"update_type": "unrecognized_future_event", "extra_data": [1, 2, 3]},
            {"message": None},
            {"updates": []},
            {"updates": [{"random_junk": True}]},
            {"update_type": "message_callback", "callback": {}},
            {"update_type": "message_created", "message": {"body": None}},
        ]
        for p in fuzz_payloads:
            res = client.post("/api/bot/webhook", json=p)
            assert res.status_code == 200
            assert res.json() == {"status": "ok"}
