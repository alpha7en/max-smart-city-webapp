import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.arshin import ArshinService
from app.models.domain import VerificationStatus

def test_arshin_known_verified_meter():
    service = ArshinService()
    res = service.check_verification("2809142")
    assert res.serial_number == "2809142"
    assert res.status == VerificationStatus.VERIFIED
    assert res.is_fraud_warning is True
    assert res.shield_color == "green"
    assert "Зеленый Щит Безопасности" in res.safety_message
    assert "МОШЕННИЧЕСТВО" in res.safety_message

def test_arshin_fallback_unknown_serial():
    service = ArshinService()
    res = service.check_verification("999999999")
    assert res.status == VerificationStatus.VERIFIED
    assert res.shield_color == "green"
    assert "Поверка НЕ ТРЕБУЕТСЯ" in res.safety_message

def test_arshin_expired_meter_991201():
    service = ArshinService()
    res = service.check_verification("991201", fraud_warning_on_expired=True)
    assert res.serial_number == "991201"
    assert res.status == VerificationStatus.EXPIRED
    assert res.shield_color == "red"
    assert res.is_fraud_warning is True
    assert "ИСТЕК" in res.safety_message
    assert "МОШЕННИК" in res.safety_message.upper()

def test_arshin_gas_meter_5540912():
    service = ArshinService()
    res = service.check_verification("5540912")
    assert res.serial_number == "5540912"
    assert res.status == VerificationStatus.VERIFIED
    assert res.shield_color == "green"
    assert res.is_fraud_warning is True
    assert "Газовый" in res.safety_message or "ВК-G4" in res.safety_message

