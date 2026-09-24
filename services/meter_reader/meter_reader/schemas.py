from typing import Literal, Optional

from pydantic import BaseModel, Field

MeterType = Literal["hot_water", "cold_water", "electricity", "gas", "unknown"]


class MeterReading(BaseModel):
    meter_type: MeterType = Field(description="Тип счётчика")
    reading: Optional[float] = Field(description="Показание (расход) в единицах unit")
    reading_text: Optional[str] = Field(description="Показание в виде строки, как на счётчике")
    integer_digits: Optional[str] = Field(description="Целая часть (чёрные барабаны)")
    fraction_digits: Optional[str] = Field(description="Дробная часть (красные барабаны)")
    unit: Optional[str] = Field(description="Единица измерения: m3 или kWh")
    tariff: Optional[str] = None
    brand: Optional[str] = Field(description="Производитель")
    model: Optional[str] = Field(description="Модель счётчика")
    serial_number: Optional[str] = None
    confidence: Optional[float] = Field(description="Уверенность модели 0..1")
    type_evidence: Optional[str] = None
