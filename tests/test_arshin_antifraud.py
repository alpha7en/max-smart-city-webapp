"""
Unit tests for FGIS Arshin verification & Green Security Shield Anti-Fraud.
"""

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
