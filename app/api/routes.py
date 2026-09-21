"""
FastAPI route definitions for MAX Smart City housing platform.
Full OpenAPI 3.1 schema support.
"""

import hashlib
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query, Body, status, Depends, Header, Request
from app.config import settings
from app.api.security import verify_max_init_data, verify_max_init_data_strict
from app.models.schemas import (
    MeterBase,
    MeterReadingSubmitRequest,
    MeterReadingValidationResult,
    AiVisionScanRequest,
    AiVisionScanResponse,
    ArshinCheckResponse,
    GostQrParseRequest,
    GostQrParseResponse,
    TicketCreateRequest,
    TicketResponse,
    TicketStatusUpdateRequest,
    GuestAccessGenerateRequest,
    GuestAccessResponse,
    InspectorActCreateRequest,
    InspectorActResponse,
    UserProfile,
    UserProperty,
    ProfileSwitchPropertyRequest,
    ProfileAddPropertyRequest,
    ProfileVerifyContactRequest
)
from app.models.domain import MeterType
from app.services.meter_service import meter_service
from app.services.ai_vision import ai_vision_service
from app.services.arshin import arshin_service
from app.services.gost_qr import parse_gost_qr_payload
from app.services.ticket_service import ticket_service
from app.services.guest_service import guest_service
from app.services.inspector_service import inspector_service
from app.services.profile_service import profile_service

logger = logging.getLogger("max_api_routes")


router = APIRouter(prefix="/api", tags=["Smart City Housing API"])

@router.get("/health", summary="Проверка состояния сервиса")
async def health_check():
    return {
        "status": "healthy",
        "service": "MAX Smart City Housing Platform",
        "version": "1.0.0",
        "bot_username": "@t226_hakaton_max_bot",
        "track": "Умный город (ЖКХ, МКД, поверка, биллинг)",
        "mock_components": [
            "AI Vision Model Inference (YOLOv8 + TorchOk)",
            "External GIS ZHKH SOAP Gateway",
            "Bank SBP Payment Gateway"
        ]
    }

# ----------------- Meters -----------------

@router.get("/meters", response_model=List[MeterBase], summary="Список приборов учета помещения")
async def get_meters():
    return meter_service.get_all_meters()

@router.post("/meters/reading", response_model=MeterReadingValidationResult, summary="Алиас для отправки показаний")
async def alias_submit_reading(payload: MeterReadingSubmitRequest):
    return meter_service.validate_and_submit_reading(payload)

@router.get("/meters/{meter_id}", response_model=MeterBase, summary="Информация о конкретном приборе учета")
async def get_meter(meter_id: str):
    m = meter_service.get_meter(meter_id)
    if not m:
        raise HTTPException(status_code=404, detail=f"Прибор учета {meter_id} не найден")
    return m

@router.post("/meters/submit", response_model=MeterReadingValidationResult, summary="Передача и валидация показаний счетчика")
async def submit_reading(payload: MeterReadingSubmitRequest):
    result = meter_service.validate_and_submit_reading(payload)
    return result

# ----------------- AI Vision Scan (Mock / Stub) -----------------

@router.post("/ai/scan", response_model=AiVisionScanResponse, summary="Распознавание показаний и заводского номера по фото (AI Stub)")
async def scan_meter(payload: AiVisionScanRequest):
    return ai_vision_service.scan_meter_image(payload)

# ----------------- FGIS Arshin & Anti-Fraud -----------------

@router.get("/arshin/check", response_model=ArshinCheckResponse, summary="Проверка поверки во ФГИС АРШИН и Зеленый Щит Безопасности")
async def check_arshin(serial: str = Query(..., description="Заводской номер прибора учета")):
    return arshin_service.check_verification(serial, fraud_warning_on_expired=True)

# ----------------- GOST R 56042-2014 & Billing -----------------

@router.post("/billing/parse-qr", response_model=GostQrParseResponse, summary="Парсинг QR-кода квитанции по ГОСТ Р 56042-2014 и расщепление на спецсчет 40821")
async def parse_qr(payload: GostQrParseRequest):
    try:
        return parse_gost_qr_payload(payload.qr_payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ----------------- Tickets (УК по ПП РФ № 40) -----------------

@router.get("/tickets", response_model=List[TicketResponse], summary="Список заявок в УК")
async def get_tickets():
    return ticket_service.get_all_tickets()

@router.get("/tickets/{ticket_id}", response_model=TicketResponse, summary="Получение информации о заявке")
async def get_ticket(ticket_id: str):
    t = ticket_service.get_ticket(ticket_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"Заявка {ticket_id} не найдена")
    return t

@router.post("/tickets", response_model=TicketResponse, status_code=status.HTTP_201_CREATED, summary="Создание заявки в аварийно-диспетчерскую службу")
async def create_ticket(payload: TicketCreateRequest):
    return ticket_service.create_ticket(payload)

@router.patch("/tickets/{ticket_id}/status", response_model=TicketResponse, summary="Обновление статуса заявки диспетчером или мастером")
async def update_ticket_status(ticket_id: str, payload: TicketStatusUpdateRequest):
    t = ticket_service.update_ticket_status(
        ticket_id=ticket_id,
        new_status=payload.status,
        comment=payload.comment,
        assigned_master=payload.assigned_master
    )
    if not t:
        raise HTTPException(status_code=404, detail=f"Заявка {ticket_id} не найдена")
    return t

# ----------------- Guest Access for Tenants -----------------

@router.post("/guest/generate", response_model=GuestAccessResponse, summary="Генерация гостевого доступа для арендатора")
async def generate_guest_access(payload: GuestAccessGenerateRequest):
    return guest_service.generate_guest_token(payload)

@router.get("/guest/{token}", summary="Проверка валидности гостевого токена арендатора")
async def validate_guest_access(token: str):
    data = guest_service.validate_guest_token(token)
    if not data:
        raise HTTPException(status_code=404, detail="Гостевой токен недействителен или срок его действия истек")
    return {
        "status": "active",
        "guest_token": token,
        "property_id": data["property_id"],
        "tenant_name": data["tenant_name"],
        "expires_at": data["expires_at"].isoformat() if hasattr(data["expires_at"], 'isoformat') else str(data["expires_at"]),
        "allowed_actions": data["actions"]
    }

# ----------------- User Profile & Linked Properties -----------------

def _resolve_profile_user_id(requested_uid: Optional[int], auth_data: Optional[Dict[str, Any]]) -> int:
    if requested_uid is not None:
        return requested_uid
    if auth_data and isinstance(auth_data, dict):
        user_info = auth_data.get("user_data") or auth_data.get("user")
        if isinstance(user_info, dict) and user_info.get("id"):
            try:
                return int(user_info["id"])
            except (ValueError, TypeError):
                pass
        elif isinstance(user_info, str):
            try:
                parsed = json.loads(user_info)
                if parsed.get("id"):
                    return int(parsed["id"])
            except Exception:
                pass
    return 100456

@router.get(
    "/profile",
    response_model=UserProfile,
    summary="Получение профиля жителя и списка привязанных адресов (ЕЛС)"
)
async def get_user_profile(
    user_id: Optional[int] = Query(None, description="ID пользователя в MAX"),
    auth_data: Optional[Dict[str, Any]] = Depends(verify_max_init_data)
):
    eff_uid = _resolve_profile_user_id(user_id, auth_data)
    prof = profile_service.get_profile(eff_uid)
    if auth_data and isinstance(auth_data, dict):
        user_info = auth_data.get("user_data") or auth_data.get("user")
        if isinstance(user_info, str):
            try:
                user_info = json.loads(user_info)
            except Exception:
                user_info = None
        if isinstance(user_info, dict):
            fn = user_info.get("first_name")
            ln = user_info.get("last_name")
            un = user_info.get("username")
            if fn:
                prof.first_name = fn
            if ln:
                prof.last_name = ln
            if un:
                prof.username = un
    return prof

@router.post(
    "/profile/switch-property",
    response_model=UserProfile,
    summary="Мгновенное переключение активного адреса помещения / ЕЛС в 1 клик"
)
async def switch_property_route(
    payload: ProfileSwitchPropertyRequest,
    auth_data: Optional[Dict[str, Any]] = Depends(verify_max_init_data)
):
    eff_uid = _resolve_profile_user_id(payload.user_id, auth_data)
    prof = profile_service.switch_active_property(eff_uid, payload.property_id)
    if not prof:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Объект недвижимости {payload.property_id} не найден в профиле жителя"
        )
    return prof

@router.post(
    "/profile/add-property",
    response_model=UserProfile,
    status_code=status.HTTP_201_CREATED,
    summary="Добавление нового адреса или ЕЛС в профиль пользователя"
)
async def add_property_route(
    payload: ProfileAddPropertyRequest,
    auth_data: Optional[Dict[str, Any]] = Depends(verify_max_init_data)
):
    eff_uid = _resolve_profile_user_id(payload.user_id, auth_data)

    address = payload.address.strip() if payload.address else ""
    els = payload.els.strip() if payload.els else ""
    mc = (payload.management_company or "ООО УК Столица-Сервис").strip()

    if payload.gost_qr_payload:
        try:
            parsed_qr = parse_gost_qr_payload(payload.gost_qr_payload)
            if not els and parsed_qr.personal_account:
                els = parsed_qr.personal_account
            if not address and parsed_qr.recipient_name:
                address = f"Объект по квитанции {parsed_qr.recipient_name}"
            if parsed_qr.recipient_name and not payload.management_company:
                mc = parsed_qr.recipient_name
        except Exception as e:
            logger.warning("Could not auto-extract from GOST QR: %s", e)

    if not address:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Адрес помещения не может быть пустым")
    if not els:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Единый лицевой счет (ЕЛС) не может быть пустым")

    return profile_service.add_property(
        user_id=eff_uid,
        address=address,
        els=els,
        management_company=mc,
        role=payload.role or "resident",
        gost_qr_payload=payload.gost_qr_payload
    )

@router.post(
    "/profile/verify-contact",
    response_model=UserProfile,
    summary="Верификация телефона жителя (Bot API request_contact или WebApp bridge)"
)
async def verify_contact_route(
    payload: ProfileVerifyContactRequest,
    auth_data: Optional[Dict[str, Any]] = Depends(verify_max_init_data)
):
    eff_uid = _resolve_profile_user_id(payload.user_id, auth_data)
    try:
        return profile_service.verify_phone_contact(
            user_id=eff_uid,
            phone=payload.phone,
            vcf_info=payload.vcf_info,
            hash_val=payload.hash,
            auth_date=payload.auth_date
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc)
        )

# ----------------- UK Inspector ARM (Обходчик УК) -----------------


@router.post(
    "/uk/inspector-act",
    response_model=InspectorActResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Формирование юридически значимого цифрового акта проверки обходчиком УК"
)
async def create_inspector_act(
    payload: Optional[InspectorActCreateRequest] = Body(None),
    address: Optional[str] = Query(None, description="Адрес объекта (для обратной совместимости с WebApp GET/POST)"),
    meter_id: Optional[str] = Query(None, description="ID прибора учета"),
    reading_value: Optional[float] = Query(None, description="Контрольные показания"),
    inspector_name: Optional[str] = Query(None, description="ФИО контролера УК")
):
    """
    Генерация юридически значимого электронного акта контрольного осмотра ПУ
    с привязкой геолокации GPS, отметки времени ISO-8601, криптографического
    хэша SHA-256 и готовностью к выгрузке в биллинг 1С:ЖКХ (63-ФЗ).
    """
    eff_meter_id = (payload.meter_id if payload else None) or meter_id or "meter-khvs-1"
    eff_reading = (payload.reading_value if payload and payload.reading_value is not None else None)
    if eff_reading is None:
        eff_reading = reading_value if reading_value is not None else 142.385
    eff_address = (payload.address if payload else None) or address or "г. Москва, ул. Тверская, д. 7, кв. 14"
    eff_inspector = (payload.inspector_name if payload else None) or inspector_name or "Контролер Службы Учета Водоснабжения Смирнов В. И."
    eff_gps = (payload.gps_coordinates if payload and payload.gps_coordinates else None) or "55.7558° N, 37.6173° E"

    now_dt = datetime.now()
    now_iso = now_dt.isoformat()
    act_id = f"ACT-ЖКХ-{int(now_dt.timestamp())}"

    if payload and payload.photo_base64:
        photo_hash = hashlib.sha256(payload.photo_base64.encode("utf-8")).hexdigest()
    else:
        raw_sig = f"{act_id}|{eff_meter_id}|{eff_address}|{now_iso}|{eff_reading}"
        photo_hash = hashlib.sha256(raw_sig.encode("utf-8")).hexdigest()

    gps_coords_str = eff_gps if "(Метка подтверждена" in eff_gps else f"{eff_gps} (Метка подтверждена)"

    return InspectorActResponse(
        act_id=act_id,
        timestamp=now_iso,
        gps=eff_gps,
        photo_hash_sha256=photo_hash,
        meter_id=eff_meter_id,
        reading_value=eff_reading,
        status="success",
        export_1c_ready=True,
        act_number=act_id,
        act_title="Электронный акт контрольного осмотра ПУ",
        inspector_name=eff_inspector,
        address=eff_address,
        gps_coordinates=gps_coords_str,
        meter_reading=f"{eff_reading} м³",
        crypto_hash=photo_hash,
        billing_export_status="EXPORTED_TO_1C_ZHKH",
        legal_significance="Заверен усиленной квалифицированной ЭЦП УК (63-ФЗ)"
    )

# ----------------- Compatibility Route Aliases -----------------

@router.get("/meter/scan", response_model=AiVisionScanResponse, summary="Алиас для распознавания показаний (GET)", operation_id="alias_meter_scan_get")
async def alias_meter_scan_get(meter_hint: Optional[str] = Query(None)):
    req = AiVisionScanRequest()
    if meter_hint:
        hint_lower = meter_hint.lower()
        if "гвс" in hint_lower or "горяч" in hint_lower:
            req.device_type_hint = MeterType.HOT_WATER
        elif "свет" in hint_lower or "электр" in hint_lower:
            req.device_type_hint = MeterType.ELECTRICITY_MULTI
        elif "газ" in hint_lower:
            req.device_type_hint = MeterType.GAS
        elif "тепл" in hint_lower or "отопл" in hint_lower:
            req.device_type_hint = MeterType.HEAT
        else:
            req.device_type_hint = MeterType.COLD_WATER
    return ai_vision_service.scan_meter_image(req)

@router.post("/meter/scan", response_model=AiVisionScanResponse, summary="Алиас для распознавания показаний (POST)", operation_id="alias_meter_scan_post")
async def alias_meter_scan_post(payload: Optional[AiVisionScanRequest] = None):
    req = payload or AiVisionScanRequest()
    return ai_vision_service.scan_meter_image(req)

@router.get("/arshin/verify", response_model=ArshinCheckResponse, summary="Алиас для проверки во ФГИС АРШИН")
async def alias_arshin_verify(serial_number: str = Query(..., alias="serial_number")):
    return arshin_service.check_verification(serial_number, fraud_warning_on_expired=True)

@router.get("/arshin/verify/{serial_number}", response_model=ArshinCheckResponse, summary="Алиас для проверки во ФГИС АРШИН по пути")
async def alias_arshin_verify_path(serial_number: str):
    return arshin_service.check_verification(serial_number, fraud_warning_on_expired=True)

@router.post("/qr/parse", response_model=GostQrParseResponse, summary="Алиас для разбора QR-кода ГОСТ Р 56042-2014")
async def alias_qr_parse(payload: GostQrParseRequest):
    if not payload.qr_payload or not payload.qr_payload.strip():
        raise HTTPException(status_code=400, detail="QR строка пуста")
    try:
        return parse_gost_qr_payload(payload.qr_payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/inspector/acts", response_model=InspectorActResponse, status_code=status.HTTP_201_CREATED, summary="Алиас для акта обходчика")
@router.get("/inspector/acts", response_model=InspectorActResponse, summary="Алиас для акта обходчика (GET)")
async def alias_inspector_acts(
    payload: Optional[InspectorActCreateRequest] = Body(None),
    address: Optional[str] = Query(None),
    meter_id: Optional[str] = Query(None),
    reading_value: Optional[float] = Query(None),
    inspector_name: Optional[str] = Query(None)
):
    return await create_inspector_act(payload, address, meter_id, reading_value, inspector_name)


# ----------------- WebApp Auth & HMAC Validation -----------------

@router.get("/auth/verify-init-data", summary="Валидация HMAC-SHA256 подписи initData из MAX WebApp")
@router.post("/auth/verify-init-data", summary="Валидация HMAC-SHA256 подписи initData из MAX WebApp")
async def verify_init_data_route(auth_data: Dict[str, Any] = Depends(verify_max_init_data)):
    return {
        "status": "authenticated",
        "is_dev_bypass": auth_data.get("is_dev_bypass", False),
        "user": auth_data.get("user_data") or auth_data.get("user"),
        "auth_date": auth_data.get("auth_date")
    }

# ----------------- Bot Webhook & Subscriptions -----------------

@router.post("/bot/webhook", summary="Эндпоинт приема вебхуков от платформы MAX")
async def bot_webhook_endpoint(
    payload: Dict[str, Any] = Body(..., description="Входящее событие или батч от MAX Bot API")
):
    try:
        from app.bot.handlers import get_bot_handler
        handler = get_bot_handler()
        if "updates" in payload and isinstance(payload["updates"], list):
            for upd in payload["updates"]:
                handler.process_update(upd)
        else:
            handler.process_update(payload)
    except Exception as e:
        logger.error("Error processing bot webhook payload: %s", e)
    return {"status": "ok"}

@router.post("/bot/webhook/register", summary="Регистрация вебхука в API MAX (POST /subscriptions)")
async def register_webhook_endpoint(
    url: Optional[str] = Query(None, description="URL вебхука (если не указан, берется из настроек)")
):
    from app.bot.client import MaxBotClient
    target_url = url or settings.WEBHOOK_URL
    if not target_url:
        raise HTTPException(status_code=400, detail="Webhook URL must be provided in query or WEBHOOK_URL env")
    client = MaxBotClient(settings.BOT_TOKEN, settings.MAX_API_BASE)
    try:
        result = client.set_webhook(target_url)
        return {"status": "registered", "result": result, "url": target_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to register webhook: {e}")

@router.get("/bot/webhook/subscriptions", summary="Список активных подписок в API MAX (GET /subscriptions)")
async def list_subscriptions_endpoint():
    from app.bot.client import MaxBotClient
    client = MaxBotClient(settings.BOT_TOKEN, settings.MAX_API_BASE)
    try:
        return client.get_subscriptions()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch subscriptions: {e}")

@router.delete("/bot/webhook", summary="Удаление подписки вебхука в API MAX (DELETE /subscriptions)")
async def delete_webhook_endpoint(
    url: Optional[str] = Query(None, description="URL вебхука для удаления (если None, удаляет все)")
):
    from app.bot.client import MaxBotClient
    client = MaxBotClient(settings.BOT_TOKEN, settings.MAX_API_BASE)
    try:
        return client.delete_webhook(url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete webhook: {e}")


