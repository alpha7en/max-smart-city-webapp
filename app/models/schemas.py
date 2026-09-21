"""
Pydantic v2 schemas for API requests, responses and data validation.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime, date
from pydantic import BaseModel, Field, field_validator, model_validator
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

    @model_validator(mode="before")
    @classmethod
    def extract_qr_payload(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"qr_payload": data}
        if isinstance(data, dict):
            if "payload" in data:
                inner = data["payload"]
                if isinstance(inner, dict):
                    val = inner.get("qr_payload") or inner.get("qr_string") or inner.get("qr_data")
                    if val:
                        return {"qr_payload": val}
                elif isinstance(inner, str):
                    return {"qr_payload": inner}
            for container_key in ("data", "body"):
                if container_key in data and isinstance(data[container_key], dict):
                    val = data[container_key].get("qr_payload") or data[container_key].get("qr_string")
                    if val:
                        return {"qr_payload": val}
            val = data.get("qr_payload") or data.get("qr_string") or data.get("qr_data")
            if isinstance(val, str):
                return {"qr_payload": val}
        return data

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

class TicketStatusUpdateRequest(BaseModel):
    status: TicketStatus = Field(..., description="Новый статус заявки")
    comment: Optional[str] = Field(None, description="Комментарий диспетчера или мастера")
    assigned_master: Optional[str] = Field(None, description="Назначенный мастер")

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, v: Any) -> Any:
        if isinstance(v, str):
            v_lower = v.lower().strip()
            aliases = {
                "completed": TicketStatus.COMPLETED,
                "done": TicketStatus.COMPLETED,
                "closed": TicketStatus.RESOLVED,
            }
            if v_lower in aliases:
                return aliases[v_lower]
        return v

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

# ----------------- UK Inspector ARM (Обходчик УК) Schemas -----------------

class InspectorActCreateRequest(BaseModel):
    meter_id: str = Field(default="meter-khvs-1", description="Идентификатор прибора учета")
    reading_value: float = Field(default=142.385, description="Контрольное показание, снятое обходчиком")
    address: str = Field(default="г. Москва, ул. Тверская, д. 7, кв. 14", description="Адрес объекта")
    inspector_name: str = Field(default="Контролер Службы Учета Водоснабжения Смирнов В. И.", description="ФИО контролера УК")
    photo_base64: Optional[str] = Field(None, description="Фотофиксация прибора учета / пломбы")
    gps_coordinates: Optional[str] = Field("55.7558° N, 37.6173° E", description="Геолокация обходчика")

class InspectorActResponse(BaseModel):
    # Contract fields (SCOPE.md / PROJECT.md)
    act_id: Optional[str] = Field(None, description="Уникальный идентификатор цифрового акта")
    timestamp: str = Field(..., description="ISO-8601 таймстемп фиксации осмотра")
    gps: str = Field("55.7558° N, 37.6173° E", description="GPS координаты фиксации")
    photo_hash_sha256: Optional[str] = Field(None, description="Криптографический SHA-256 хеш фотофиксации или акта")
    meter_id: str = Field("meter-khvs-1", description="Идентификатор прибора учета")
    reading_value: float = Field(142.385, description="Зафиксированное контрольное показание")
    status: str = Field("success", description="Статус операции")
    export_1c_ready: bool = Field(True, description="Флаг готовности к автоматической выгрузке в 1С:ЖКХ")

    # Backward compatibility fields (WebApp Mini-App & Legacy routes)
    act_number: str = Field(..., description="Уникальный номер цифрового акта")
    act_title: str = Field("Электронный акт контрольного осмотра ПУ", description="Наименование документа")
    inspector_name: str
    address: str
    gps_coordinates: str = Field("55.7558° N, 37.6173° E (Метка подтверждена)", description="Подтвержденная геометка")
    meter_reading: str
    crypto_hash: str = Field(..., description="SHA-256 криптографический хеш акта")
    billing_export_status: str = Field("EXPORTED_TO_1C_ZHKH", description="Статус синхронизации с 1С:ЖКХ")
    legal_significance: str = Field(
        "Заверен усиленной квалифицированной ЭЦП УК (63-ФЗ)",
        description="Юридическая сила документа"
    )

    def model_post_init(self, __context):
        if self.act_id is None:
            self.act_id = self.act_number
        if self.photo_hash_sha256 is None:
            self.photo_hash_sha256 = self.crypto_hash


# ----------------- User Profile & Linked Properties Schemas -----------------

class UserProperty(BaseModel):
    id: str = Field(..., description="Уникальный идентификатор объекта недвижимости")
    address: str = Field(..., description="Адрес помещения")
    els: str = Field(..., description="Единый лицевой счет (ЕЛС) ГИС ЖКХ / Лицевой счет")
    management_company: str = Field("ООО УК Столица-Сервис", description="Управляющая организация")
    is_active: bool = Field(False, description="Флаг: является ли объект активным в данный момент")
    role: str = Field("owner", description="Роль пользователя: owner (собственник) или tenant (арендатор)")
    linked_at: datetime = Field(default_factory=datetime.now, description="Дата и время привязки объекта")

class UserProfile(BaseModel):
    user_id: int = Field(..., description="Идентификатор пользователя в MAX")
    phone: Optional[str] = Field(None, description="Номер телефона пользователя")
    first_name: str = Field("Иван", description="Имя пользователя")
    last_name: Optional[str] = Field("Иванов", description="Фамилия пользователя")
    username: Optional[str] = Field(None, description="Никнейм в MAX")
    is_verified: bool = Field(False, description="Подтвержден ли номер через MAX request_contact")
    active_property_id: str = Field(..., description="ID текущего выбранного объекта")
    properties: List[UserProperty] = Field(default_factory=list, description="Список привязанных объектов")

class ProfileSwitchPropertyRequest(BaseModel):
    property_id: str = Field(..., description="ID объекта для активации")
    user_id: Optional[int] = Field(None, description="ID пользователя MAX (опционально)")

class ProfileAddPropertyRequest(BaseModel):
    address: Optional[str] = Field(None, description="Адрес нового помещения (опционально при передаче gost_qr_payload)")
    els: Optional[str] = Field(None, description="Единый лицевой счет (10 цифр) или номер ЛС (опционально при передаче gost_qr_payload)")
    management_company: Optional[str] = Field("ООО УК Столица-Сервис", description="Управляющая компания")
    role: Optional[str] = Field("owner", description="Роль: owner (собственник) или tenant (арендатор)")
    user_id: Optional[int] = Field(None, description="ID пользователя MAX")
    gost_qr_payload: Optional[str] = Field(None, description="Опциональный сырой ГОСТ QR-код для автозаполнения")

class ProfileVerifyContactRequest(BaseModel):
    phone: Optional[str] = Field(None, description="Номер телефона пользователя")
    vcf_info: Optional[str] = Field(None, description="VCF контакт из MAX Bot API")
    hash: Optional[str] = Field(None, description="Криптографическая подпись HMAC-SHA256")
    auth_date: Optional[int] = Field(None, description="Отметка времени auth_date")
    user_id: Optional[int] = Field(None, description="ID пользователя MAX")



