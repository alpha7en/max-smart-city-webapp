"""
Unit tests for Night Flow (Ночной дозор) ODPU leak crowd-diagnostics.
"""

from app.services.night_flow import NightFlowService
from app.models.schemas import NightFlowCheckRequest

def test_night_flow_napkin_wet():
    service = NightFlowService()
    req = NightFlowCheckRequest(entrance_id=1, napkin_test_result="wet")
    res = service.diagnose_leak(req)
    assert res.apartment_leak_detected is True
    assert res.reward_eligible is True
    assert "Утечка обнаружена в вашей квартире" in res.diagnosis_verdict
    assert "скидка 10%" in res.recommendation

def test_night_flow_napkin_dry():
    service = NightFlowService()
    req = NightFlowCheckRequest(entrance_id=1, napkin_test_result="dry")
    res = service.diagnose_leak(req)
    assert res.apartment_leak_detected is False
    assert res.leak_detected_in_building is True
    assert res.reward_eligible is False
    assert "В вашей квартире утечки не обнаружено" in res.diagnosis_verdict

def test_night_flow_bedtime_vs_morning_readings():
    service = NightFlowService()
    req = NightFlowCheckRequest(
        entrance_id=1,
        night_reading_before_bed=142.780,
        morning_reading=142.960  # +180 liters while sleeping
    )
    res = service.diagnose_leak(req)
    assert res.apartment_leak_detected is True
    assert res.apartment_difference_liters == 180.0
    assert res.reward_eligible is True
