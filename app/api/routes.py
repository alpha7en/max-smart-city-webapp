"""
FastAPI route definitions for MAX Smart City housing platform.
Full OpenAPI 3.1 schema support.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, Body, status
from app.models.schemas import (
    MeterBase,
    MeterReadingSubmitRequest,
    MeterReadingValidationResult,
    AiVisionScanRequest,
    AiVisionScanResponse,
    ArshinCheckResponse,
    GostQrParseRequest,
    GostQrParseResponse,
    NightFlowCheckRequest,
    NightFlowDiagnosticResponse,
    TicketCreateRequest,
    TicketResponse,
    GuestAccessGenerateRequest,
    GuestAccessResponse
)
from app.services.meter_service import meter_service
from app.services.ai_vision import ai_vision_service
from app.services.arshin import arshin_service
from app.services.gost_qr import parse_gost_qr_payload
from app.services.night_flow import night_flow_service
from app.services.ticket_service import ticket_service
from app.services.guest_service import guest_service

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
    return arshin_service.check_verification(serial)

# ----------------- GOST R 56042-2014 & Billing -----------------

@router.post("/billing/parse-qr", response_model=GostQrParseResponse, summary="Парсинг QR-кода квитанции по ГОСТ Р 56042-2014 и расщепление на спецсчет 40821")
async def parse_qr(payload: GostQrParseRequest):
    try:
        return parse_gost_qr_payload(payload.qr_payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ----------------- Night Flow (Ночной дозор) -----------------

@router.post("/night-flow/diagnose", response_model=NightFlowDiagnosticResponse, summary="Диагностика ночного небаланса ОДПУ и утечек в квартире")
async def diagnose_night_flow(payload: NightFlowCheckRequest):
    return night_flow_service.diagnose_leak(payload)

# ----------------- Tickets (УК по ПП РФ № 40) -----------------

@router.get("/tickets", response_model=List[TicketResponse], summary="Список заявок в УК")
async def get_tickets():
    return ticket_service.get_all_tickets()

@router.post("/tickets", response_model=TicketResponse, status_code=status.HTTP_201_CREATED, summary="Создание заявки в аварийно-диспетчерскую службу")
async def create_ticket(payload: TicketCreateRequest):
    return ticket_service.create_ticket(payload)

# ----------------- Guest Access for Tenants -----------------

@router.post("/guest/generate", response_model=GuestAccessResponse, summary="Генерация гостевого доступа для арендатора")
async def generate_guest_access(payload: GuestAccessGenerateRequest):
    return guest_service.generate_guest_token(payload)
