import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

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

def test_gas_meter_retrieval_and_rollers():
    service = MeterService()
    meter = service.get_meter("meter-gas-1")
    assert meter is not None
    assert meter.meter_type == MeterType.GAS
    assert meter.serial_number == "5540912"
    assert meter.decimal_digits == 3

    # Test red roller filtering for gas meter: e.g. 345812 unscaled -> 345.0
    filtered = service.filter_red_rollers(345812.0, MeterType.GAS, prev_value=340.0)
    assert filtered == 345.0

def test_gas_meter_consumption_and_anomaly():
    service = MeterService()
    meter = service.get_meter("meter-gas-1")
    prev_val = meter.last_reading_value

    # 1. Normal consumption (e.g. +12 m3)
    req1 = MeterReadingSubmitRequest(
        meter_id="meter-gas-1",
        reading_value=prev_val + 12.0,
        submission_channel="test"
    )
    res1 = service.validate_and_submit_reading(req1)
    assert res1.is_valid is True
    assert res1.consumption == 12.0
    assert res1.anomaly_detected is False

    # 2. Anomaly consumption (e.g. +65 m3 > 50 m3 threshold)
    req2 = MeterReadingSubmitRequest(
        meter_id="meter-gas-1",
        reading_value=res1.current_value + 65.0,
        submission_channel="test"
    )
    res2 = service.validate_and_submit_reading(req2)
    assert res2.is_valid is True
    assert res2.anomaly_detected is True
    assert "превышает среднемесячную норму" in res2.message

