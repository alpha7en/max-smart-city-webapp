"""
Unit tests for MeterService: red roller filtering, monotonicity check, anomaly detection.
"""

from app.services.meter_service import MeterService
from app.models.domain import MeterType
from app.models.schemas import MeterReadingSubmitRequest

def test_red_roller_filtering():
    service = MeterService()
    # If user entered 142789 (8 digits unscaled where 789 are liters)
    raw = 142789.0
    filtered = service.filter_red_rollers(raw, MeterType.COLD_WATER)
    assert filtered == 142.0

def test_red_roller_no_filter_on_regular_value():
    service = MeterService()
    # Regular value e.g. 142.5 should not be altered
    assert service.filter_red_rollers(142.5, MeterType.COLD_WATER) == 142.5

def test_submit_valid_reading():
    service = MeterService()
    meter = service.get_meter("meter-khvs-1")
    initial_val = meter.last_reading_value

    req = MeterReadingSubmitRequest(
        meter_id="meter-khvs-1",
        reading_value=initial_val + 4.2,
        submission_channel="test"
    )
    res = service.validate_and_submit_reading(req)
    assert res.is_valid is True
    assert res.consumption == 4.2
    assert res.current_value == initial_val + 4.2
    assert res.anomaly_detected is False

def test_submit_monotonicity_failure():
    service = MeterService()
    meter = service.get_meter("meter-khvs-1")
    prev_val = meter.last_reading_value

    # Submit smaller value
    req = MeterReadingSubmitRequest(
        meter_id="meter-khvs-1",
        reading_value=prev_val - 10.0,
        submission_channel="test"
    )
    res = service.validate_and_submit_reading(req)
    assert res.is_valid is False
    assert "Ошибка монотонности" in res.message

def test_submit_consumption_anomaly():
    service = MeterService()
    meter = service.get_meter("meter-khvs-1")
    prev_val = meter.last_reading_value

    # Submit huge consumption (e.g. +45 m³)
    req = MeterReadingSubmitRequest(
        meter_id="meter-khvs-1",
        reading_value=prev_val + 45.0,
        submission_channel="test"
    )
    res = service.validate_and_submit_reading(req)
    assert res.is_valid is True
    assert res.anomaly_detected is True
    assert "превышает среднемесячную норму" in res.message
