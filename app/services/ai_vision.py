"""
AI Vision and Meter Recognition Service.
NOTE: Marked explicitly as MOCK / STUB per hackathon requirements.
Simulates YOLOv8 dial detection + TorchOk/EasyOCR digit reading + perspective rectification.
"""

from typing import Optional, List
from app.models.domain import MeterType
from app.models.schemas import (
    AiVisionScanRequest,
    AiVisionScanResponse,
    BoundingBox
)

class AiVisionService:
    def scan_meter_image(self, request: AiVisionScanRequest) -> AiVisionScanResponse:
        """
        Simulates AI inference for meter dial detection and OCR.
        Explicitly marked as [ЗАГЛУШКА / AI VISION STUB].
        """
        # Determine meter type
        target_type = request.device_type_hint or MeterType.COLD_WATER

        if target_type == MeterType.COLD_WATER:
            raw_value = 142.789
            integer_reading = 142.0
            serial = "2809142"
            boxes = [
                BoundingBox(x=120.0, y=85.0, width=280.0, height=75.0, label="black_digit_rollers_m3", confidence=0.98),
                BoundingBox(x=400.0, y=85.0, width=160.0, height=75.0, label="red_digit_rollers_liters", confidence=0.95),
                BoundingBox(x=150.0, y=210.0, width=220.0, height=40.0, label="serial_number_plate", confidence=0.94),
            ]
        elif target_type == MeterType.HOT_WATER:
            raw_value = 98.412
            integer_reading = 98.0
            serial = "3910844"
            boxes = [
                BoundingBox(x=115.0, y=80.0, width=270.0, height=70.0, label="black_digit_rollers_m3", confidence=0.97),
                BoundingBox(x=385.0, y=80.0, width=155.0, height=70.0, label="red_digit_rollers_liters", confidence=0.94),
                BoundingBox(x=140.0, y=200.0, width=210.0, height=38.0, label="serial_number_plate", confidence=0.96),
            ]
        elif target_type in [MeterType.ELECTRICITY_SINGLE, MeterType.ELECTRICITY_MULTI]:
            raw_value = 1840.4
            integer_reading = 1840.0
            serial = "01458291"
            boxes = [
                BoundingBox(x=90.0, y=60.0, width=320.0, height=80.0, label="lcd_digits_kwh", confidence=0.99),
                BoundingBox(x=130.0, y=190.0, width=240.0, height=45.0, label="barcode_and_serial", confidence=0.97),
            ]
        else:
            raw_value = 14.25
            integer_reading = 14.2
            serial = "7741209"
            boxes = [
                BoundingBox(x=100.0, y=70.0, width=290.0, height=75.0, label="lcd_heat_gcal", confidence=0.95),
                BoundingBox(x=140.0, y=180.0, width=200.0, height=40.0, label="serial_number_plate", confidence=0.93),
            ]

        return AiVisionScanResponse(
            is_mock=True,
            mock_notice=(
                "[ЗАГЛУШКА / AI VISION STUB]: Модель компьютерного зрения детекции циферблата "
                "и OCR показаний приборов учета (YOLOv8 + TorchOk pipeline). "
                "В рабочей версии выполняет нейросетевую сегментацию и считывание барабанов."
            ),
            detected_meter_type=target_type,
            recognized_reading=integer_reading,
            recognized_serial_number=serial,
            confidence=0.968,
            red_rollers_detected=(target_type in [MeterType.COLD_WATER, MeterType.HOT_WATER]),
            raw_reading_with_fractions=raw_value,
            perspective_rectified=True,
            bounding_boxes=boxes
        )

ai_vision_service = AiVisionService()
