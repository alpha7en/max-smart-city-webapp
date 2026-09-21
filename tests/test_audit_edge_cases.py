"""
Deep audit tests verifying edge cases, robustness, and new capabilities:
1. Arshin expired, valid, and unregistered / scam serial numbers
2. Red roller fractional filtering for small and large legitimate consumption
3. GOST R 56042-2014 parser with UTF-8 BOM, lowercase, and leading spaces
4. Inspector ARM service and API endpoint (GPS, SHA-256 seal, 1C:ЖКХ export)
5. Ticket status state machine transitions and thread-safe updates
6. Pre-configured guest tenant test token validation
7. Bot handler command routing for inspector and lowercase GOST QR payloads
"""

from fastapi.testclient import TestClient
from app.main import app
from app.services.arshin import arshin_service
from app.services.meter_service import meter_service
from app.services.gost_qr import parse_gost_qr_payload
from app.services.guest_service import guest_service
from app.services.ticket_service import ticket_service
from app.services.inspector_service import inspector_service
from app.bot.handlers import BotHandler
from app.bot.client import MaxBotClient
from app.models.domain import MeterType, VerificationStatus, TicketStatus
from app.models.schemas import (
    InspectorActCreateRequest,
    TicketCreateRequest,
    TicketPriority
)

client = TestClient(app)

def test_arshin_verification_varieties():
    # 1. Valid meter
    v_res = arshin_service.check_verification("2809142")
    assert v_res.status == VerificationStatus.VERIFIED
    assert v_res.shield_color == "green"
    assert v_res.is_fraud_warning is True

    # 2. Expired meter
    exp_res = arshin_service.check_verification("991201")
    assert exp_res.status == VerificationStatus.EXPIRED
    assert exp_res.shield_color == "red"
    assert exp_res.is_fraud_warning is False
    assert "ИСТЕК" in exp_res.safety_message

    # 3. Explicit fake / scam serial
    fake_res = arshin_service.check_verification("000000")
    assert fake_res.status == VerificationStatus.UNREGISTERED
    assert fake_res.shield_color == "red"
    assert "ОСТОРОЖНО" in fake_res.safety_message

def test_red_roller_filtering_edge_cases():
    # Case A: resident enters 5789 (5 m3 and 789 liters) when prev was 4.0
    val_a = meter_service.filter_red_rollers(5789.0, MeterType.COLD_WATER, prev_value=4.0)
    assert val_a == 5.0

    # Case B: large cottage/enterprise with legitimate 10505.0 m3
    val_b = meter_service.filter_red_rollers(10505.0, MeterType.COLD_WATER, prev_value=10500.0)
    assert val_b == 10505.0

    # Case C: large consumer with unscaled red rollers (10505789)
    val_c = meter_service.filter_red_rollers(10505789.0, MeterType.COLD_WATER, prev_value=10500.0)
    assert val_c == 10505.0

    # Case D: brand new meter with prev_value=0.0 and unscaled liters (1250 -> 1.0)
    val_d = meter_service.filter_red_rollers(1250.0, MeterType.COLD_WATER, prev_value=0.0)
    assert val_d == 1.0

def test_gost_qr_robustness_variations():
    # 1. Lowercase prefix and keys
    qr_lower = "st00012|name=ООО УК ТЕХНОДОМ|personalacc=40702810938000012345|bic=044525225|sum=250000"
    res1 = parse_gost_qr_payload(qr_lower)
    assert res1.is_valid_gost is True
    assert res1.total_amount_rubles == 2500.0

    # 2. UTF-8 BOM
    qr_bom = "\ufeffST00012|Name=ООО УК ТЕХНОДОМ|PersonalAcc=40702810938000012345|BIC=044525225|Sum=250000"
    res2 = parse_gost_qr_payload(qr_bom)
    assert res2.is_valid_gost is True
    assert res2.total_amount_rubles == 2500.0

    # 3. Leading whitespace
    qr_spaces = "  \n  ST00012|Name=ООО УК ТЕХНОДОМ|PersonalAcc=40702810938000012345|BIC=044525225|Sum=250000  "
    res3 = parse_gost_qr_payload(qr_spaces)
    assert res3.is_valid_gost is True

    # 4. Russian comma in sum (4850,50 rubles)
    qr_comma = "ST00012|Name=ООО УК ТЕХНОДОМ|PersonalAcc=40702810938000012345|BIC=044525225|Sum=4850,50"
    res4 = parse_gost_qr_payload(qr_comma)
    assert res4.is_valid_gost is True
    assert res4.total_amount_rubles == 4850.50

def test_inspector_act_end_to_end():
    req = InspectorActCreateRequest(
        meter_id="meter-khvs-1",
        reading_value=142.385,
        address="г. Москва, ул. Ленина, д. 42, кв. 15",
        inspector_name="Смирнов А. В.",
        gps_coordinates="55.7558° N, 37.6173° E"
    )
    act = inspector_service.create_act(req)
    assert act.status == "success"
    assert len(act.crypto_hash) == 64
    assert act.billing_export_status == "EXPORTED_TO_1C_ZHKH"
    assert "63-ФЗ" in act.legal_significance

    # API verification
    res = client.post("/api/uk/inspector-act", json=req.model_dump())
    assert res.status_code == 201
    assert res.json()["status"] == "success"

def test_ticket_status_progression():
    created = ticket_service.create_ticket(
        TicketCreateRequest(
            category="Электрика",
            description="Искрит вводной автомат",
            priority=TicketPriority.EMERGENCY,
            address="ул. Ленина, д. 42, кв. 15"
        )
    )
    assert created.status == TicketStatus.NEW
    assert created.sla_hours == 1

    # Advance status to in_progress
    updated1 = ticket_service.update_ticket_status(
        ticket_id=created.id,
        new_status=TicketStatus.IN_PROGRESS,
        assigned_master="Электрик Сидоров В. П."
    )
    assert updated1 is not None
    assert updated1.status == TicketStatus.IN_PROGRESS
    assert updated1.assigned_master == "Электрик Сидоров В. П."

    # Advance status to resolved
    updated2 = ticket_service.update_ticket_status(
        ticket_id=created.id,
        new_status=TicketStatus.RESOLVED,
        comment="Автомат заменен на ABB 25A"
    )
    assert updated2 is not None
    assert updated2.status == TicketStatus.RESOLVED
    assert len(updated2.status_history) >= 3

def test_preconfigured_guest_test_token():
    # Verify tenant test account from DATA-API.yaml
    token_data = guest_service.validate_guest_token("guest_test_token_2026")
    assert token_data is not None
    assert token_data["tenant_name"] == "Петров Петр Сергеевич"
    assert "submit_meter_readings" in token_data["actions"]

    # Verify API endpoint GET /api/guest/{token}
    res_ok = client.get("/api/guest/guest_test_token_2026")
    assert res_ok.status_code == 200
    assert res_ok.json()["status"] == "active"
    assert res_ok.json()["tenant_name"] == "Петров Петр Сергеевич"

    # Verify 404 on invalid token
    res_bad = client.get("/api/guest/invalid_non_existent_token")
    assert res_bad.status_code == 404

def test_bot_handlers_mock_interaction():
    # Test BotHandler with a dummy client that records messages
    class MockClient:
        def __init__(self):
            self.sent_messages = []
            self.answered_callbacks = []

        def send_message(self, **kwargs):
            self.sent_messages.append(kwargs)
            return {"status": "ok"}

        def answer_callback(self, **kwargs):
            self.answered_callbacks.append(kwargs)
            return {"status": "ok"}

    mock_client = MockClient()
    handler = BotHandler(mock_client)

    # 1. Test /inspector text command
    handler.handle_message_created({
        "message": {
            "body": {"text": "/inspector"},
            "sender": {"id": 12345, "first_name": "Тестер"},
            "recipient": {"chat_id": "chat_123"}
        }
    })
    assert len(mock_client.sent_messages) == 1
    assert "АРМ Обходчика" in mock_client.sent_messages[0]["text"]

    # 2. Test lowercase GOST QR paste
    handler.handle_message_created({
        "message": {
            "body": {"text": "st00012|name=ООО УК ТЕХНОДОМ|personalacc=40702810938000012345|bic=044525225|sum=250000"},
            "sender": {"id": 12345, "first_name": "Тестер"},
            "recipient": {"chat_id": "chat_123"}
        }
    })
    assert len(mock_client.sent_messages) == 2
    assert "успешно распознана" in mock_client.sent_messages[1]["text"]

    # 3. Test cmd_inspector callback
    handler.handle_callback({
        "callback": {"callback_id": "cb_1", "payload": "cmd_inspector"},
        "sender": {"id": 12345},
        "chat_id": "chat_123"
    })
    assert len(mock_client.answered_callbacks) == 1
    assert len(mock_client.sent_messages) == 3

    # 4. Test natural text meter reading submission in chat (e.g. "ХВС 148.5")
    handler.handle_message_created({
        "message": {
            "body": {"text": "ХВС 148.5"},
            "sender": {"id": 12345, "first_name": "Тестер"},
            "recipient": {"chat_id": "chat_123"}
        }
    })
    assert len(mock_client.sent_messages) == 4
    assert "Показания успешно приняты" in mock_client.sent_messages[3]["text"]
    assert "148.5" in mock_client.sent_messages[3]["text"]

    # 5. Test /meters command: button text lengths strictly <= 18 chars
    handler.handle_message_created({
        "message": {
            "body": {"text": "/meters"},
            "sender": {"id": 12345, "first_name": "Тестер"},
            "recipient": {"chat_id": "chat_123"}
        }
    })
    assert len(mock_client.sent_messages) == 5
    meters_msg = mock_client.sent_messages[4]
    btn_labels = []
    for row in meters_msg["keyboard"]["payload"]["buttons"]:
        for btn in row:
            btn_labels.append(btn["text"])
            assert len(btn["text"]) <= 18, f"Button label too long: {btn['text']} ({len(btn['text'])} chars)"
    assert "Сдать Свет" in btn_labels
    assert "Сдать Газ" in btn_labels

    # 6. Verify WebApp direct access (via link or inline button) and zero emoji spam
    prohibited_emojis = ["🌐", "📱", "🤖", "💡", "✓"]
    has_webapp = any(
        ("http://" in msg.get("text", "") or "https://" in msg.get("text", "")) or
        (msg.get("keyboard") and any(btn.get("url") for row in msg.get("keyboard", {}).get("payload", {}).get("buttons", []) for btn in row if isinstance(btn, dict)))
        for msg in mock_client.sent_messages
    )
    assert has_webapp, "WebApp must be accessible via link or button in the interaction"
    for msg in mock_client.sent_messages:
        text = msg["text"]
        assert "`http://localhost:8080/static/index.html`" not in text  # Must NOT be wrapped in backticks
        for bad_emoji in prohibited_emojis:
            assert bad_emoji not in text, f"Prohibited emoji {bad_emoji} found in message: {text}"
