"""
Pydantic v2 schemas for API requests, responses and data validation.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime, date
from pydantic import BaseModel, Field, field_validator
from app.models.domain import (
    MeterType,
    TariffZone,
    VerificationStatus,
    TicketPriority,
    TicketStatus,
    UserRole
)

# ----------------- Meter Schemas -----------------

class MeterBase(BaseModel):
    id: str = Field(..., description="Уникальный идентификатор прибора учета")
    meter_type: MeterType = Field(..., description="Тип счетчика (ХВС, ГВС, свет, газ, тепло)")
    serial_number: str = Field(..., description="Заводской номер прибора учета")
    name: str = Field(..., description="Название счетчика для пользователя")
    installation_place: str = Field("Квартира", description="Место установки (Кухня, Санузел и т.д.)")
    last_reading_value: float = Field(..., description="Предыдущие показания")
    last_reading_date: date = Field(..., description="Дата предыдущей передачи показаний")
    verification_date_valid_until: date = Field(..., description="Срок действия поверки по ФГИС АРШИН")
    unit: str = Field("м³", description="Единица измерения (м³, кВт*ч, Гкал)")
    decimal_digits: int = Field(3, description="Количество разрядов литров (красные ролики)")

class MeterReadingSubmitRequest(BaseModel):
    meter_id: str = Field(..., description="ID прибора учета")
    reading_value: float = Field(..., description="Текущее значение расхода")
    reading_value_t2: Optional[float] = Field(None, description="Показания ночного тарифа Т2 (для многотарифных)")
    reading_value_t3: Optional[float] = Field(None, description="Показания полупика Т3 (для трехтарифных)")
    photo_base64: Optional[str] = Field(None, description="Опциональное фото прибора для аудита")
    submission_channel: str = Field("max_miniapp", description="Канал передачи: max_bot, max_miniapp, inspector_app")
    comment: Optional[str] = Field(None, description="Комментарий")

class MeterReadingValidationResult(BaseModel):
    is_valid: bool = Field(..., description="Прошли ли показания базовую и бизнес-проверку")
    consumption: float = Field(..., description="Рассчитанный расход за период")
    previous_value: float = Field(..., description="Предыдущее значение")
    current_value: float = Field(..., description="Принятое значение")
    anomaly_detected: bool = Field(False, description="Обнаружен ли подозрительно высокий или отрицательный расход")
    message: str = Field(..., description="Понятное пользователю объяснение")
    red_roller_filtered: bool = Field(False, description="Были ли отсечены красные ролики (литры)")

# ----------------- AI Vision Stub Schemas -----------------

class AiVisionScanRequest(BaseModel):
    image_base64: Optional[str] = Field(None, description="Изображение в формате Base64")
    image_url: Optional[str] = Field(None, description="URL изображения, если загружено в MAX CDN")
    device_type_hint: Optional[MeterType] = Field(None, description="Подсказка типа счетчика")

class BoundingBox(BaseModel):
    x: float
    y: float
    width: float
    height: float
    label: str
    confidence: float

class AiVisionScanResponse(BaseModel):
    is_mock: bool = Field(True, description="Флаг заглушки (Mock AI inference)")
    mock_notice: str = Field(
        "[ЗАГЛУШКА / AI VISION STUB]: Модель распознавания циферблата и OCR заводского номера",
        description="Явное уведомление о заглушке для экспертов хакатона"
    )
    detected_meter_type: MeterType
    recognized_reading: float = Field(..., description="Распознанное основное показание (целая часть)")
    recognized_serial_number: str = Field(..., description="Распознанный заводской номер счетчика")
    confidence: float = Field(0.96, description="Уверенность OCR-модели (0.0 - 1.0)")
    red_rollers_detected: bool = Field(True, description="Обнаружены ли красные ролики (дробные литры)")
    raw_reading_with_fractions: float = Field(..., description="Сырое значение до отсечения литров")
    perspective_rectified: bool = Field(True, description="Применено ли геометрическое выравнивание угла съемки")
    bounding_boxes: List[BoundingBox] = Field(default_factory=list)

# ----------------- FGIS Arshin & Anti-Fraud Schemas -----------------

class ArshinCheckRequest(BaseModel):
    serial_number: str = Field(..., description="Заводской номер прибора учета")
    meter_type: Optional[MeterType] = None

class ArshinCheckResponse(BaseModel):
    serial_number: str
    organization_name: str = Field(..., description="Аккредитованная поверочная организация")
    verification_date: date = Field(..., description="Дата проведения последней поверки")
    valid_until: date = Field(..., description="Срок действия поверки")
    status: VerificationStatus
    is_fraud_warning: bool = Field(..., description="Флаг: листовка с требованием немедленной поверки является обманом")
    shield_color: str = Field("green", description="Цвет защитного бейджа: green, yellow, red")
    safety_message: str = Field(..., description="Текст Зеленого Щита Безопасности")
    fgis_arshin_url: str = Field(..., description="Официальная ссылка на запись в ФГИС АРШИН")

# ----------------- GOST R 56042-2014 & Billing Schemas -----------------

class GostQrParseRequest(BaseModel):
    qr_payload: str = Field(..., description="Строка QR-кода стандарта ST00012")

class SplitPaymentRecipient(BaseModel):
    recipient_name: str = Field(..., description="Наименование поставщика (Водоканал, ТЭК, УК)")
    inn: str
    account_40821: str = Field(..., description="Спецсчет 40821 или транзитный расчетный счет")
    bik: str
    amount_rubles: float = Field(..., description="Сумма, направляемая напрямую данному поставщику")
    purpose: str = Field(..., description="Назначение платежа (ХВС, Отопление, Содержание жилья)")

class GostQrParseResponse(BaseModel):
    is_valid_gost: bool = Field(..., description="Соответствует ли ГОСТ Р 56042-2014")
    format_version: str = Field("ST00012", description="Идентификатор стандарта")
    recipient_name: str
    inn: str
    kpp: Optional[str] = None
    cor_account: Optional[str] = None
    bank_bik: str
    payee_account: str = Field(..., description="Расчетный счет получателя")
    is_account_checksum_valid: bool = Field(..., description="Контрольный ключ счета ЦБ РФ")
    personal_account: str = Field(..., description="Лицевой счет плательщика (PersAcc / ЕЛС)")
    period: Optional[str] = None
    total_amount_rubles: float
    is_40821_split_supported: bool = Field(True, description="Поддержка прямого расщепления по 103-ФЗ / 59-ФЗ")
    split_details: List[SplitPaymentRecipient] = Field(default_factory=list)

# ----------------- Night Flow (Ночной дозор) Schemas -----------------

class NightFlowCheckRequest(BaseModel):
    entrance_id: int = Field(1, description="Номер подъезда")
    night_reading_before_bed: Optional[float] = Field(None, description="Показания счетчика перед сном (ХВС)")
    morning_reading: Optional[float] = Field(None, description="Показания счетчика после пробуждения")
    napkin_test_result: Optional[str] = Field(None, description="'wet' если салфетка намокла, 'dry' если сухая")

class NightFlowDiagnosticResponse(BaseModel):
    entrance_odpu_flow_liters_per_hour: float = Field(..., description="Ночной расход на вводе в дом (02:30 - 04:30)")
    normal_threshold_liters_per_hour: float = Field(100.0, description="Норма ночного фона")
    leak_detected_in_building: bool
    apartment_leak_detected: bool
    apartment_difference_liters: float
    diagnosis_verdict: str
    reward_eligible: bool = Field(False, description="Право на скидку 10% на услуги УК за найденную утечку")
    recommendation: str

# ----------------- Tickets (Заявки в УК по ПП РФ № 40) -----------------

class TicketCreateRequest(BaseModel):
    category: str = Field(..., description="Категория (сантехника, электрика, лифт, уборка, кровля)")
    description: str = Field(..., description="Описание неисправности")
    priority: TicketPriority = Field(TicketPriority.URGENT, description="Срочность")
    address: str = Field("ул. Ленина, д. 42, кв. 15", description="Адрес")
    photo_urls: List[str] = Field(default_factory=list)

class TicketResponse(BaseModel):
    id: str
    created_at: datetime
    category: str
    description: str
    priority: TicketPriority
    status: TicketStatus
    sla_hours: int = Field(..., description="Срок выполнения по ПП РФ № 40")
    assigned_master: Optional[str] = None
    status_history: List[Dict[str, Any]] = Field(default_factory=list)

# ----------------- Guest / Tenant Access Schemas -----------------

class GuestAccessGenerateRequest(BaseModel):
    property_id: str
    tenant_name: str
    tenant_phone: Optional[str] = None
    duration_days: int = Field(30, description="Срок действия временного доступа")

class GuestAccessResponse(BaseModel):
    guest_token: str
    direct_max_link: str
    property_address: str
    expires_at: datetime
    allowed_actions: List[str]
