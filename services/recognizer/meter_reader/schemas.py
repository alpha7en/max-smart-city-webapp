from typing import Literal, Optional

from pydantic import BaseModel, Field

MeterType = Literal["hot_water", "cold_water", "electricity", "gas", "heat", "unknown"]
Unit = Literal["m3", "kWh", "Gcal", "MWh", "GJ"]


class MeterReading(BaseModel):
    meter_type: MeterType = Field(description="Тип счётчика")
    reading: Optional[float] = Field(description="Показание (расход) в единицах unit")
    reading_text: Optional[str] = Field(description="Показание в виде строки, как на счётчике")
    integer_digits: Optional[str] = Field(description="Целая часть (чёрные барабаны)")
    fraction_digits: Optional[str] = Field(description="Дробная часть (красные барабаны)")
    unit: Optional[Unit] = Field(description="Единица измерения: m3, kWh, Gcal, MWh или GJ")
    tariff: Optional[str] = None
    brand: Optional[str] = Field(description="Производитель")
    model: Optional[str] = Field(description="Модель счётчика")
    serial_number: Optional[str] = None
    confidence: Optional[float] = Field(description="Уверенность модели 0..1")
    type_evidence: Optional[str] = None
    readable: bool = Field(default=False, description="Основное показание уверенно прочитано")
    issues: list[str] = Field(default_factory=list, description="Коды проблем с фото (фиксированный набор)")
    issue_note: Optional[str] = Field(default=None, description="Короткое пояснение проблемы на русском, ≤120 символов")
