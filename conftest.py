"""
Root conftest.py for pytest test suite.
Provides pythonpath = . configuration and autouse state reset fixture for meter_service,
preventing in-memory singleton mutation across test runs.
"""

import sys
from pathlib import Path
from datetime import date
import pytest

# Ensure project root is on sys.path (pythonpath = .)
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.models.domain import MeterType
from app.models.schemas import MeterBase
from app.services.meter_service import meter_service, INITIAL_METERS
from app.services.profile_service import profile_service


def get_pristine_meters() -> dict:
    """Returns a brand-new pristine dictionary of initial meters."""
    return {
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


@pytest.fixture(autouse=True)
def reset_meter_service_state():
    """
    Autouse fixture that resets in-memory meter_service state before and after each test.
    Guarantees test isolation without inter-test side effects.
    """
    pristine = get_pristine_meters()
    INITIAL_METERS.clear()
    INITIAL_METERS.update({k: v.model_copy() for k, v in pristine.items()})
    meter_service._meters = {k: v.model_copy() for k, v in pristine.items()}
    meter_service._history = []
    profile_service.reset()
    yield
    pristine = get_pristine_meters()
    INITIAL_METERS.clear()
    INITIAL_METERS.update({k: v.model_copy() for k, v in pristine.items()})
    meter_service._meters = {k: v.model_copy() for k, v in pristine.items()}
    meter_service._history = []
    profile_service.reset()
