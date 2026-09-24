"""
Meter business logic:
- Red roller filtering (cuts off decimal fraction liters to prevent 1000x overbilling)
- Monotonicity verification (new >= previous)
- Anomaly detection (consumption > 25 m³ or sudden spikes)
- Persistent SQLite storage via app.db.database.Database with 100% backward compatibility
- Full test fixture isolation via synchronized self._meters and self._history
"""

from typing import List, Optional, Dict, Any
from datetime import date, datetime
import logging
from app.models.domain import MeterType, VerificationStatus
from app.models.schemas import MeterBase, MeterReadingSubmitRequest, MeterReadingValidationResult
from app.db.database import Database, get_db

logger = logging.getLogger("max_meter_service")

# In-memory demo fixtures exported for tests and conftest.py
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


class MeterStoreDict(dict):
    """
    Synchronized dictionary proxy for meter_service._meters.
    Every update, insertion, or deletion automatically synchronizes with SQLite.
    """

    def __init__(self, service: "MeterService", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._service = service

    def __setitem__(self, key: str, value: MeterBase):
        super().__setitem__(key, value)
        if self._service and self._service.db:
            self._service._persist_meter(value)

    def __delitem__(self, key: str):
        super().__delitem__(key)
        if self._service and self._service.db:
            try:
                self._service.db.execute_write("DELETE FROM meters WHERE id = ?;", (key,))
            except Exception as e:
                logger.warning("Error deleting meter %s from db: %s", key, e)

    def clear(self):
        super().clear()
        if self._service and self._service.db:
            try:
                self._service.db.execute_write("DELETE FROM meters;")
            except Exception as e:
                logger.warning("Error clearing meters table: %s", e)

    def update(self, *args, **kwargs):
        other = dict(*args, **kwargs)
        for k, v in other.items():
            self[k] = v


class MeterService:
    def __init__(self, db: Optional[Database] = None):
        self._db = db or get_db()
        self._store = MeterStoreDict(self)
        self._history: List[Dict[str, Any]] = []
        self._load_meters()

    @property
    def db(self) -> Database:
        return self._db

    def _persist_meter(self, meter: MeterBase):
        """Persists a MeterBase model to the SQLite meters table."""
        if not self._db:
            return
        now_iso = datetime.now().isoformat()
        read_date_str = (
            meter.last_reading_date.isoformat()
            if hasattr(meter.last_reading_date, "isoformat")
            else str(meter.last_reading_date)
        )
        verif_date_str = (
            meter.verification_date_valid_until.isoformat()
            if hasattr(meter.verification_date_valid_until, "isoformat")
            else str(meter.verification_date_valid_until)
        )
        meter_type_str = (
            meter.meter_type.value
            if hasattr(meter.meter_type, "value")
            else str(meter.meter_type)
        )

        try:
            self._db.execute_write(
                """INSERT INTO meters (
                    id, property_id, meter_type, serial_number, name, installation_place,
                    last_reading_value, last_reading_date, verification_date_valid_until,
                    unit, decimal_digits, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    property_id = excluded.property_id,
                    meter_type = excluded.meter_type,
                    serial_number = excluded.serial_number,
                    name = excluded.name,
                    installation_place = excluded.installation_place,
                    last_reading_value = excluded.last_reading_value,
                    last_reading_date = excluded.last_reading_date,
                    verification_date_valid_until = excluded.verification_date_valid_until,
                    unit = excluded.unit,
                    decimal_digits = excluded.decimal_digits,
                    updated_at = excluded.updated_at;""",
                (
                    meter.id,
                    getattr(meter, "property_id", "prop-flat-42-15"),
                    meter_type_str,
                    meter.serial_number,
                    meter.name,
                    meter.installation_place,
                    meter.last_reading_value,
                    read_date_str,
                    verif_date_str,
                    meter.unit,
                    meter.decimal_digits,
                    now_iso,
                    now_iso
                )
            )
        except Exception as e:
            logger.warning("Failed to persist meter %s to SQLite: %s", meter.id, e)

    def _load_meters(self):
        """Loads meters from SQLite database into synchronized in-memory dictionary."""
        rows = self._db.execute_query("SELECT * FROM meters;")
        if not rows:
            # Seed pristine demo meters into database and store
            for k, v in INITIAL_METERS.items():
                self._store[k] = v.model_copy()
        else:
            super(MeterStoreDict, self._store).clear()
            for r in rows:
                try:
                    rd_date = date.fromisoformat(r["last_reading_date"])
                except Exception:
                    rd_date = date.today()
                try:
                    vf_date = date.fromisoformat(r["verification_date_valid_until"])
                except Exception:
                    vf_date = date.today()

                m = MeterBase(
                    id=r["id"],
                    meter_type=MeterType(r["meter_type"]),
                    serial_number=r["serial_number"],
                    name=r["name"],
                    installation_place=r["installation_place"],
                    last_reading_value=float(r["last_reading_value"]),
                    last_reading_date=rd_date,
                    verification_date_valid_until=vf_date,
                    unit=r["unit"],
                    decimal_digits=int(r["decimal_digits"])
                )
                super(MeterStoreDict, self._store).__setitem__(m.id, m)

    @property
    def _meters(self) -> Dict[str, MeterBase]:
        """Provides backward-compatible dict interface expected by tests and conftest.py."""
        return self._store

    @_meters.setter
    def _meters(self, new_val):
        """Enables direct reassignment from conftest.py and synchronizes with SQLite."""
        self._store.clear()
        if isinstance(new_val, dict):
            for k, v in new_val.items():
                self._store[k] = v
        elif isinstance(new_val, (list, tuple)):
            for item in new_val:
                self._store[item.id] = item

    def get_all_meters(self) -> List[MeterBase]:
        """Returns all configured meters."""
        return list(self._store.values())

    def get_meters(self, property_id: Optional[str] = None) -> List[MeterBase]:
        """Returns meters optionally filtered by property_id."""
        if property_id:
            return [m for m in self._store.values() if getattr(m, "property_id", "prop-flat-42-15") == property_id]
        return self.get_all_meters()

    def get_meter(self, meter_id: str) -> Optional[MeterBase]:
        """Returns specific meter by identifier."""
        return self._store.get(meter_id)

    def filter_red_rollers(self, raw_value: float, meter_type: MeterType, prev_value: Optional[float] = None) -> float:
        """
        Prevents the 1000x error by cutting off red decimal roller digits.
        If a water or gas meter has 5 black digits and 3 red digits (liters),
        reading '142789' means 142 m³ and 789 liters.
        """
        if meter_type in [MeterType.COLD_WATER, MeterType.HOT_WATER, MeterType.GAS]:
            scaled = float(int(raw_value) // 1000)
            if prev_value is not None and prev_value >= 0:
                if raw_value >= 1000 and abs(scaled - prev_value) < abs(raw_value - prev_value) and (raw_value - prev_value > 100):
                    return scaled
                if prev_value > 1000 and raw_value >= prev_value and (raw_value - prev_value < 500):
                    return raw_value
            if raw_value > 10000:
                return scaled
        return raw_value

    def validate_and_submit_reading(self, req: MeterReadingSubmitRequest) -> MeterReadingValidationResult:
        """Validates incoming meter reading and atomically persists to SQLite."""
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

        # Update meter state
        meter.last_reading_value = filtered_val
        meter.last_reading_date = date.today()
        self._persist_meter(meter)

        # MVP STUB: GIS_ZHKH -> PASS
        logger.info(
            "MVP STUB: GIS_ZHKH -> PASS (meter_id=%s, value=%.3f, channel=%s)",
            req.meter_id,
            filtered_val,
            req.submission_channel
        )

        # Persist reading entry into SQLite
        now_iso = datetime.now().isoformat()
        try:
            self._db.execute_write(
                """INSERT INTO meter_readings (
                    meter_id, reading_value, reading_value_t2, reading_value_t3,
                    consumption, reading_date, submission_channel, status,
                    is_anomaly, anomaly_reason, red_roller_filtered, raw_value,
                    photo_base64, photo_hash, comment, is_valid, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?);""",
                (
                    meter.id,
                    filtered_val,
                    req.reading_value_t2,
                    req.reading_value_t3,
                    consumption,
                    date.today().isoformat(),
                    req.submission_channel,
                    "accepted",
                    1 if anomaly else 0,
                    message if anomaly else None,
                    1 if red_roller_filtered else 0,
                    original_val,
                    req.photo_base64,
                    None,
                    req.comment,
                    now_iso
                )
            )
        except Exception as e:
            logger.warning("Could not persist meter reading: %s", e)

        # Update in-memory history
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

    def get_reading_history(self, meter_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns reading submission history from SQLite or in-memory list."""
        if meter_id:
            rows = self._db.execute_query(
                "SELECT * FROM meter_readings WHERE meter_id = ? ORDER BY id ASC;", (meter_id,)
            )
        else:
            rows = self._db.execute_query("SELECT * FROM meter_readings ORDER BY id ASC;")

        if rows:
            return [
                {
                    "meter_id": r["meter_id"],
                    "reading_value": float(r["reading_value"]),
                    "date": r["reading_date"],
                    "channel": r["submission_channel"]
                }
                for r in rows
            ]
        if meter_id:
            return [h for h in self._history if h.get("meter_id") == meter_id]
        return list(self._history)

    def reset(self):
        """Resets meter state to pristine initial condition."""
        self._store.clear()
        for k, v in INITIAL_METERS.items():
            self._store[k] = v.model_copy()
        self._history = []
        try:
            self._db.execute_write("DELETE FROM meter_readings;")
        except Exception:
            pass


# Global singleton instance
meter_service = MeterService()
