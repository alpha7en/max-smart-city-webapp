"""
Meter business logic:
- Red roller filtering (cuts off decimal fraction liters to prevent 1000x overbilling)
- Monotonicity verification (new >= previous)
- Anomaly detection (consumption > 25 m³ or sudden spikes)
- In-memory database of meters for hackathon demonstration
"""

from typing import List, Optional, Dict
from datetime import date, timedelta
from app.models.domain import MeterType, VerificationStatus
from app.models.schemas import MeterBase, MeterReadingSubmitRequest, MeterReadingValidationResult

# In-memory demo fixtures
INITIAL_METERS: Dict[str, MeterBase] = {
    "meter-khvs-1": MeterBase(
        id="meter-khvs-1",
        meter_type=MeterType.COLD_WATER,
        serial_number="2809142",
        name="ХВС (Холодная вода)",
        installation_place="Санузел",
        last_reading_value=142.0,
        last_reading_date=date(2026, 8, 20),
        verification_date_valid_until=date(2029, 10, 18),
        unit="м³",
        decimal_digits=3
    ),
    "meter-gvs-1": MeterBase(
        id="meter-gvs-1",
        meter_type=MeterType.HOT_WATER,
        serial_number="3910844",
        name="ГВС (Горячая вода)",
        installation_place="Санузел",
        last_reading_value=98.0,
        last_reading_date=date(2026, 8, 20),
        verification_date_valid_until=date(2028, 4, 12),
        unit="м³",
        decimal_digits=3
    ),
    "meter-el-1": MeterBase(
        id="meter-el-1",
        meter_type=MeterType.ELECTRICITY_MULTI,
        serial_number="01458291",
        name="Электроэнергия (Меркурий 208)",
        installation_place="Щит на лестничной клетке",
        last_reading_value=1840.0,
        last_reading_date=date(2026, 8, 20),
        verification_date_valid_until=date(2032, 11, 5),
        unit="кВт*ч",
        decimal_digits=1
    ),
    "meter-heat-1": MeterBase(
        id="meter-heat-1",
        meter_type=MeterType.HEAT,
        serial_number="7741209",
        name="Отопление (Теплосчетчик)",
        installation_place="Коридор",
        last_reading_value=14.2,
        last_reading_date=date(2026, 8, 20),
        verification_date_valid_until=date(2027, 9, 30),
        unit="Гкал",
        decimal_digits=2
    )
}

class MeterService:
    def __init__(self):
        self._meters: Dict[str, MeterBase] = dict(INITIAL_METERS)
        self._history: List[Dict] = []

    def get_all_meters(self) -> List[MeterBase]:
        return list(self._meters.values())

    def get_meter(self, meter_id: str) -> Optional[MeterBase]:
        return self._meters.get(meter_id)

    def filter_red_rollers(self, raw_value: float, meter_type: MeterType) -> float:
        """
        Prevents the 1000x error by cutting off red decimal roller digits.
        If a water meter has 5 black digits and 3 red digits (liters),
        reading '142789' means 142 m³ and 789 liters.
        """
        if meter_type in [MeterType.COLD_WATER, MeterType.HOT_WATER]:
            # If raw value looks like unscaled 8 digits (e.g. > 10,000 when prev was ~100)
            if raw_value > 10000:
                return float(int(raw_value) // 1000)
        return raw_value

    def validate_and_submit_reading(self, req: MeterReadingSubmitRequest) -> MeterReadingValidationResult:
        meter = self.get_meter(req.meter_id)
        if not meter:
            return MeterReadingValidationResult(
                is_valid=False,
                consumption=0.0,
                previous_value=0.0,
                current_value=req.reading_value,
                anomaly_detected=True,
                message=f"Прибор учета с ID '{req.meter_id}' не найден в реестре ЕЛС",
                red_roller_filtered=False
            )

        original_val = req.reading_value
        filtered_val = self.filter_red_rollers(original_val, meter.meter_type)
        red_roller_filtered = (filtered_val != original_val)

        prev_val = meter.last_reading_value
        consumption = round(filtered_val - prev_val, 3)

        # 1. Monotonicity check
        if filtered_val < prev_val:
            return MeterReadingValidationResult(
                is_valid=False,
                consumption=consumption,
                previous_value=prev_val,
                current_value=filtered_val,
                anomaly_detected=True,
                message=(
                    f"Ошибка монотонности: новое значение ({filtered_val}) меньше "
                    f"предыдущего ({prev_val}). Проверьте правильность введенных цифр."
                ),
                red_roller_filtered=red_roller_filtered
            )

        # 2. Consumption anomaly check
        anomaly = False
        message = "Показания успешно приняты и зафиксированы в ГИС ЖКХ."

        if meter.meter_type in [MeterType.COLD_WATER, MeterType.HOT_WATER]:
            if consumption > 25.0:
                anomaly = True
                message = (
                    f"Внимание: Рассчитанный расход ({consumption} {meter.unit}) превышает "
                    f"среднемесячную норму более чем в 3 раза! Проверьте, нет ли скрытой утечки."
                )
        elif meter.meter_type in [MeterType.ELECTRICITY_SINGLE, MeterType.ELECTRICITY_MULTI]:
            if consumption > 1000.0:
                anomaly = True
                message = f"Внимание: Расход электроэнергии ({consumption} кВт*ч) аномально высок."

        # Update meter state if valid
        meter.last_reading_value = filtered_val
        meter.last_reading_date = date.today()
        self._history.append({
            "meter_id": meter.id,
            "reading_value": filtered_val,
            "date": date.today().isoformat(),
            "channel": req.submission_channel
        })

        return MeterReadingValidationResult(
            is_valid=True,
            consumption=consumption,
            previous_value=prev_val,
            current_value=filtered_val,
            anomaly_detected=anomaly,
            message=message,
            red_roller_filtered=red_roller_filtered
        )

# Global singleton instance
meter_service = MeterService()
