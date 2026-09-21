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
    ),
    "meter-gas-1": MeterBase(
        id="meter-gas-1",
        meter_type=MeterType.GAS,
        serial_number="5540912",
        name="Газоснабжение (ВК-G4)",
        installation_place="Кухня",
        last_reading_value=340.0,
        last_reading_date=date(2026, 8, 20),
        verification_date_valid_until=date(2030, 6, 15),
        unit="м³",
        decimal_digits=3
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

    def filter_red_rollers(self, raw_value: float, meter_type: MeterType, prev_value: Optional[float] = None) -> float:
        """
        Prevents the 1000x error by cutting off red decimal roller digits.
        If a water or gas meter has 5 black digits and 3 red digits (liters),
        reading '142789' means 142 m³ and 789 liters.
        """
        if meter_type in [MeterType.COLD_WATER, MeterType.HOT_WATER, MeterType.GAS]:
            scaled = float(int(raw_value) // 1000)
            if prev_value is not None and prev_value >= 0:
                # If entering unscaled integer with 3 extra decimal digits, e.g.:
                # prev was 0.0 (new meter), user enters 1250 -> scaled is 1.0 (diff 1.0 vs diff 1250.0)
                # prev was 4.0, user enters 5789 -> scaled is 5.0 (diff 1.0 vs diff 5785.0)
                # prev was 142.0, user enters 145200 -> scaled is 145.0 (diff 3.0 vs diff 145058.0)
                if raw_value >= 1000 and abs(scaled - prev_value) < abs(raw_value - prev_value) and (raw_value - prev_value > 100):
                    return scaled
                # If legitimate high meter reading e.g. prev was 10500, user enters 10505
                if prev_value > 1000 and raw_value >= prev_value and (raw_value - prev_value < 500):
                    return raw_value
            # Fallback when prev_value is None: unscaled integer > 10000
            if raw_value > 10000:
                return scaled
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
        prev_val = meter.last_reading_value
        filtered_val = self.filter_red_rollers(original_val, meter.meter_type, prev_value=prev_val)
        red_roller_filtered = (filtered_val != original_val)
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
        elif meter.meter_type == MeterType.GAS:
            if consumption > 50.0:
                anomaly = True
                message = f"Внимание: Расход газа ({consumption} м³) превышает среднемесячную норму."

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
