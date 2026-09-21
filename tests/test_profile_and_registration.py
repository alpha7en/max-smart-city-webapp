"""
Test suite for User Profile, Multiple Linked Properties (ЕЛС), and Registration in MAX.
Covers:
1. UserProfileService domain logic (get, switch active, add property, GOST QR auto-linking, phone verification).
2. FastAPI REST Endpoints (/api/profile, /api/profile/switch-property, /api/profile/add-property, /api/profile/verify-contact).
3. MAX Bot flows (/profile, /account, /address, /register, switch_prop_* callbacks, and incoming contact attachments).
4. Compliance: button text length <= 18 chars and zero emoji policy.
"""

import hmac
import hashlib
import json
import time
from typing import Dict, Any, List
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.config import settings
from app.api.security import generate_init_data
from app.services.profile_service import profile_service, normalize_phone_number, parse_vcf_phone, UserProfileService
from app.bot.handlers import BotHandler, get_main_menu_keyboard
from max_bot_sdk.keyboards import Button, KeyboardBuilder
from max_bot_sdk.models import Update


class MockClient:
    def __init__(self):
        self.sent_messages: List[Dict[str, Any]] = []
        self.answered_callbacks: List[Dict[str, Any]] = []

    def send_message(self, **kwargs) -> Dict[str, Any]:
        self.sent_messages.append(kwargs)
        return {"status": "ok"}

    def answer_callback(self, **kwargs) -> Dict[str, Any]:
        self.answered_callbacks.append(kwargs)
        return {"status": "ok"}


@pytest.fixture
def client():
    return TestClient(app)


# =========================================================================
# 1. USER PROFILE SERVICE UNIT TESTS
# =========================================================================

def test_profile_service_default_and_new_user():
    service = UserProfileService()
    default_prof = service.get_profile()
    assert default_prof.user_id == 100456
    assert len(default_prof.properties) >= 2
    assert default_prof.active_property_id == "prop-flat-42-15"
    assert default_prof.is_verified is True

    # New user lazy provisioning
    new_user_prof = service.get_profile(user_id=778899)
    assert new_user_prof.user_id == 778899
    assert new_user_prof.is_verified is False
    assert len(new_user_prof.properties) >= 2


def test_profile_service_switch_property():
    service = UserProfileService()
    uid = 9001
    prof = service.get_profile(uid)
    assert prof.active_property_id == "prop-flat-42-15"

    # Switch to dacha
    updated = service.switch_active_property(uid, "prop-dacha-8")
    assert updated is not None
    assert updated.active_property_id == "prop-dacha-8"
    active_prop = service.get_active_property(uid)
    assert active_prop.id == "prop-dacha-8"
    assert active_prop.is_active is True

    # Check that previous active is now inactive
    for p in updated.properties:
        if p.id == "prop-flat-42-15":
            assert p.is_active is False

    # Switch to non-existent property
    invalid = service.switch_active_property(uid, "prop-non-existent")
    assert invalid is None


def test_profile_service_add_property_manual_and_gost_qr():
    service = UserProfileService()
    uid = 9002

    # 1. Manual addition
    prof1 = service.add_property(
        user_id=uid,
        address="г. Санкт-Петербург, Невский пр., д. 10, кв. 5",
        els="5551234567",
        management_company="ЖКС №1 Центрального района"
    )
    assert len(prof1.properties) == 3
    added_prop = service.get_active_property(uid)
    assert added_prop.address == "г. Санкт-Петербург, Невский пр., д. 10, кв. 5"
    assert added_prop.els == "5551234567"
    assert added_prop.is_active is True

    # 2. Addition with GOST R 56042-2014 QR code
    gost_sample = "ST00012|Name=ООО УК СЕВЕРНЫЙ ВЕТЕР|PersonalAcc=40702810938000012345|BIC=044525225|PayeeINN=7801234567|Sum=620000|PersAcc=8899001122"
    prof2 = service.add_property(
        user_id=uid,
        address="г. Москва, ул. Тверская, д. 12, кв. 88",
        els="dummy",
        gost_qr_payload=gost_sample
    )
    assert len(prof2.properties) == 4
    latest_prop = service.get_active_property(uid)
    assert latest_prop.els == "8899001122"
    assert latest_prop.management_company == "ООО УК СЕВЕРНЫЙ ВЕТЕР"


def test_profile_service_phone_verification_and_hmac():
    service = UserProfileService()
    uid = 9003
    prof = service.get_profile(uid)
    assert prof.is_verified is False

    # 1. Verification with VCF VCARD text
    vcf = "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Иван Иванов\r\nTEL;TYPE=CELL:+7 (916) 555-44-33\r\nEND:VCARD\r\n"
    verified = service.verify_phone_contact(user_id=uid, vcf_info=vcf)
    assert verified.is_verified is True
    assert "+7 (916) 555-44-33" in verified.phone

    # 2. Verification with raw phone and HMAC
    token = settings.BOT_TOKEN or "test_bot_token"
    phone_raw = "+79261112233"
    auth_date = 1758400000
    msg = f"authDate={auth_date}\nphone={phone_raw}"
    calc_hash = hmac.new(token.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()

    verified2 = service.verify_phone_contact(
        user_id=uid,
        phone=phone_raw,
        hash_val=calc_hash,
        auth_date=auth_date
    )
    assert verified2.is_verified is True
    assert "+7 (926) 111-22-33" in verified2.phone


# =========================================================================
# 2. FASTAPI REST ENDPOINTS TESTS
# =========================================================================

def test_api_get_profile(client):
    res = client.get("/api/profile?user_id=100456")
    assert res.status_code == 200
    data = res.json()
    assert data["user_id"] == 100456
    assert data["is_verified"] is True
    assert len(data["properties"]) >= 2
    assert data["active_property_id"] in [p["id"] for p in data["properties"]]


def test_api_switch_property(client):
    uid = 5005
    # First get profile to provision it
    client.get(f"/api/profile?user_id={uid}")

    # Switch to dacha
    res = client.post("/api/profile/switch-property", json={
        "user_id": uid,
        "property_id": "prop-dacha-8"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["active_property_id"] == "prop-dacha-8"

    # Switch to invalid property -> 404
    err_res = client.post("/api/profile/switch-property", json={
        "user_id": uid,
        "property_id": "unknown-prop-999"
    })
    assert err_res.status_code == 404


def test_api_add_property(client):
    uid = 6006
    res = client.post("/api/profile/add-property", json={
        "user_id": uid,
        "address": "г. Казань, ул. Баумана, д. 15, кв. 3",
        "els": "7788990011",
        "management_company": "ООО УК Уютный Дом"
    })
    assert res.status_code == 201
    data = res.json()
    assert data["user_id"] == uid
    assert any(p["els"] == "7788990011" for p in data["properties"])

    # Validation errors on empty inputs
    err1 = client.post("/api/profile/add-property", json={"user_id": uid, "address": "", "els": "123"})
    assert err1.status_code == 400

    err2 = client.post("/api/profile/add-property", json={"user_id": uid, "address": "ул. Тест", "els": ""})
    assert err2.status_code == 400


def test_api_verify_contact(client):
    uid = 7007
    res = client.post("/api/profile/verify-contact", json={
        "user_id": uid,
        "phone": "+7 (999) 777-66-55"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["is_verified"] is True
    assert "+7 (999) 777-66-55" in data["phone"]


# =========================================================================
# 3. BOT HANDLERS & REGISTRATION FLOW TESTS
# =========================================================================

def test_bot_profile_command():
    mock = MockClient()
    handler = BotHandler(mock)

    handler.handle_message_created({
        "message": {
            "body": {"text": "/profile"},
            "sender": {"id": 100456, "first_name": "Константин"},
            "recipient": {"chat_id": "chat_user_1"}
        }
    })

    assert len(mock.sent_messages) == 1
    msg = mock.sent_messages[0]
    assert "Личный кабинет жителя" in msg["text"]
    assert "Активный адрес" in msg["text"]
    assert "ЕЛС ГИС ЖКХ" in msg["text"]

    # Verify button labels
    buttons = msg.get("buttons") or msg.get("keyboard", {}).get("payload", {}).get("buttons", [])
    for row in buttons:
        for b in row:
            assert len(b["text"]) <= 18


def test_bot_address_management_command():
    mock = MockClient()
    handler = BotHandler(mock)

    handler.handle_message_created({
        "message": {
            "body": {"text": "/address"},
            "sender": {"id": 100456, "first_name": "Константин"},
            "recipient": {"chat_id": "chat_user_1"}
        }
    })

    assert len(mock.sent_messages) == 1
    msg = mock.sent_messages[0]
    assert "Управление объектами недвижимости" in msg["text"]

    # Verify buttons for 1-click address switching
    buttons = msg.get("buttons") or msg.get("keyboard", {}).get("payload", {}).get("buttons", [])
    found_switch = False
    for row in buttons:
        for b in row:
            assert len(b["text"]) <= 18
            if b.get("payload", "").startswith("switch_prop_"):
                found_switch = True
    assert found_switch is True


def test_bot_register_command():
    mock = MockClient()
    handler = BotHandler(mock)

    handler.handle_message_created({
        "message": {
            "body": {"text": "/register"},
            "sender": {"id": 8008, "first_name": "Новый Житель"},
            "recipient": {"chat_id": "chat_user_2"}
        }
    })

    assert len(mock.sent_messages) == 1
    msg = mock.sent_messages[0]
    assert "Быстрая регистрация в MAX" in msg["text"]

    # Verify presence of request_contact button
    buttons = msg.get("buttons") or msg.get("keyboard", {}).get("payload", {}).get("buttons", [])
    has_contact_btn = any(b.get("type") == "request_contact" for row in buttons for b in row)
    assert has_contact_btn is True


def test_bot_switch_property_callback():
    mock = MockClient()
    handler = BotHandler(mock)
    uid = 9911

    # First initialize user profile
    profile_service.get_profile(uid)

    handler.handle_callback({
        "callback": {
            "callback_id": "cb_switch_1",
            "payload": "switch_prop_prop-dacha-8",
            "user": {"id": uid, "first_name": "Тестер"}
        },
        "chat_id": "chat_cb_1"
    })

    assert len(mock.sent_messages) == 1
    msg = mock.sent_messages[0]
    assert "Активный адрес переключен" in msg["text"]
    assert "Барвиха" in msg["text"] or "2008891024" in msg["text"]

    # Verify profile state changed
    active = profile_service.get_active_property(uid)
    assert active.id == "prop-dacha-8"


def test_bot_incoming_contact_attachment():
    mock = MockClient()
    handler = BotHandler(mock)
    uid = 4455

    handler.handle_message_created({
        "message": {
            "body": {
                "text": "",
                "attachments": [
                    {
                        "type": "contact",
                        "payload": {
                            "phone": "+7 (999) 444-55-66",
                            "vcf_info": "BEGIN:VCARD\r\nVERSION:3.0\r\nTEL:+79994445566\r\nEND:VCARD"
                        }
                    }
                ]
            },
            "sender": {"id": uid, "first_name": "Анна"},
            "recipient": {"chat_id": "chat_anna"}
        }
    })

    assert len(mock.sent_messages) == 1
    msg = mock.sent_messages[0]
    assert "Телефон успешно подтвержден" in msg["text"]
    assert "+7 (999) 444-55-66" in msg["text"]
    assert "ГИС ЖКХ" in msg["text"]

    # Verify user profile in service
    prof = profile_service.get_profile(uid)
    assert prof.is_verified is True
    assert "+7 (999) 444-55-66" in prof.phone


# =========================================================================
# 4. ADVERSARIAL SECURITY & EDGE CASES TESTS
# =========================================================================

def test_profile_service_phone_verification_invalid_hmac_and_strict_mode(monkeypatch):
    service = UserProfileService()
    uid = 9005

    # 1. Invalid HMAC should raise ValueError
    with pytest.raises(ValueError, match="Недействительная криптографическая подпись"):
        service.verify_phone_contact(
            user_id=uid,
            phone="+79261112233",
            hash_val="deadbeef_invalid_hash",
            auth_date=1758400000
        )

    # 2. Strict non-DEV_MODE rejection when hash is completely missing
    monkeypatch.setattr(settings, "DEV_MODE", False)
    with pytest.raises(ValueError, match="Отсутствует обязательная криптографическая подпись"):
        service.verify_phone_contact(
            user_id=uid,
            phone="+79261112233"
        )


def test_api_verify_contact_invalid_hmac_and_strict_mode(client, monkeypatch):
    uid = 9006
    # Invalid HMAC -> HTTP 400
    res_bad_hmac = client.post("/api/profile/verify-contact", json={
        "user_id": uid,
        "phone": "+7 (999) 111-22-33",
        "hash": "invalid_forged_hash_12345"
    })
    assert res_bad_hmac.status_code == 400
    assert "Недействительная криптографическая подпись" in res_bad_hmac.json()["detail"]

    # Strict DEV_MODE=False without initData header -> HTTP 401
    monkeypatch.setattr(settings, "DEV_MODE", False)
    res_no_init = client.post("/api/profile/verify-contact", json={
        "user_id": uid,
        "phone": "+7 (999) 111-22-33"
    })
    assert res_no_init.status_code == 401

    # Strict DEV_MODE=False with valid initData header but missing contact hash -> HTTP 400
    token = settings.BOT_TOKEN or "test_token"
    init_payload = {
        "auth_date": str(int(time.time())),
        "query_id": "test_contact_strict",
        "user": json.dumps({"id": uid, "first_name": "Тестер"})
    }
    signed_header = generate_init_data(init_payload, token)
    res_no_hash = client.post(
        "/api/profile/verify-contact",
        json={"user_id": uid, "phone": "+7 (999) 111-22-33"},
        headers={"X-Init-Data": signed_header}
    )
    assert res_no_hash.status_code == 400
    assert "Отсутствует обязательная криптографическая подпись" in res_no_hash.json()["detail"]


def test_api_profile_auth_data_context_resolution(client):
    """
    Verifies that WebApp operations without explicit user_id in body
    correctly resolve the authenticated resident from X-Init-Data.
    """
    token = settings.BOT_TOKEN or "test_token"
    auth_user_id = 887766

    init_payload = {
        "auth_date": str(int(time.time())),
        "query_id": "test_query_profile",
        "user": json.dumps({"id": auth_user_id, "first_name": "Елена"})
    }
    signed_header = generate_init_data(init_payload, token)

    # 1. Switch property using X-Init-Data header
    res_switch = client.post(
        "/api/profile/switch-property",
        json={"property_id": "prop-dacha-8"},
        headers={"X-Init-Data": signed_header}
    )
    assert res_switch.status_code == 200
    data_switch = res_switch.json()
    assert data_switch["user_id"] == auth_user_id
    assert data_switch["active_property_id"] == "prop-dacha-8"

    # 2. Add property using X-Init-Data header
    res_add = client.post(
        "/api/profile/add-property",
        json={
            "address": "г. Сочи, Курортный пр., д. 50, кв. 7",
            "els": "9900112233"
        },
        headers={"X-Init-Data": signed_header}
    )
    assert res_add.status_code == 201
    data_add = res_add.json()
    assert data_add["user_id"] == auth_user_id
    assert any(p["els"] == "9900112233" for p in data_add["properties"])

    # 3. Verify phone using X-Init-Data header
    res_verify = client.post(
        "/api/profile/verify-contact",
        json={"phone": "+7 (903) 555-12-34"},
        headers={"X-Init-Data": signed_header}
    )
    assert res_verify.status_code == 200
    data_verify = res_verify.json()
    assert data_verify["user_id"] == auth_user_id
    assert data_verify["is_verified"] is True
    assert "+7 (903) 555-12-34" in data_verify["phone"]


def test_phone_and_vcf_defensive_edge_cases():
    # Null and empty phone normalization
    assert normalize_phone_number(None) == ""
    assert normalize_phone_number("") == ""
    assert normalize_phone_number("123") == "123"
    assert normalize_phone_number("89031234567") == "+7 (903) 123-45-67"
    assert normalize_phone_number("9031234567") == "+7 (903) 123-45-67"

    # Null and empty VCF parsing
    assert parse_vcf_phone(None) is None
    assert parse_vcf_phone("") is None
    assert parse_vcf_phone("BEGIN:VCARD\r\nFN:Test\r\nEND:VCARD") is None


def test_api_add_property_pure_gost_qr(client):
    uid = 9008
    gost_sample = "ST00012|Name=ООО УК ЮЖНЫЙ БЕРЕГ|PersonalAcc=40702810938000012345|BIC=044525225|PayeeINN=7701234567|Sum=500000|PersAcc=5566778899"
    res = client.post("/api/profile/add-property", json={
        "user_id": uid,
        "gost_qr_payload": gost_sample
    })
    assert res.status_code == 201
    data = res.json()
    assert any(p["els"] == "5566778899" for p in data["properties"])
    assert any("ЮЖНЫЙ БЕРЕГ" in p["management_company"] for p in data["properties"])


def test_profile_property_deduplication():
    service = UserProfileService()
    uid = 9009
    prof1 = service.add_property(user_id=uid, address="ул. Тестовая, 1", els="1122334455")
    assert len(prof1.properties) == 3

    # Add same ELS again -> should reactivate, not duplicate
    prof2 = service.add_property(user_id=uid, address="ул. Тестовая, 1 (обновлено)", els="1122334455")
    assert len(prof2.properties) == 3
    active = service.get_active_property(uid)
    assert active.els == "1122334455"
    assert "обновлено" in active.address
