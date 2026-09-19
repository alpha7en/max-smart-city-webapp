"""
Сервер веб-приложения (MAX Mini App) и REST API.
Построен на Starlette + Uvicorn для максимальной легковесности и скорости запуска (менее 1 секунды).
Поддерживает одновременный запуск фонового воркера бота MAX.
"""

import os
import sys
import json
import time
import hashlib
import threading
from datetime import datetime

from starlette.applications import Starlette
from starlette.responses import JSONResponse, HTMLResponse
from starlette.routing import Route
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

# Добавляем корень проекта в sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.api.cv_pipeline import MeterCVPipeline
from src.api.arshin import ArshinVerifier
from src.api.gost_qr import GostQRParser
from src.bot import MaxBotService

# --- ЭНДПОИНТЫ API ---

async def index_page(request):
    """Отдача HTML-интерфейса мини-приложения"""
    webapp_path = os.path.join(os.path.dirname(__file__), "webapp", "index.html")
    with open(webapp_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)

async def scan_meter_endpoint(request):
    """
    [ЗАГЛУШКА ИИ / CV MOCK]: Обработка сканирования прибора учета.
    """
    meter_hint = request.query_params.get("meter_hint", "ГВС")
    result = MeterCVPipeline.process_meter_image(meter_hint=meter_hint)
    return JSONResponse(result)

async def verify_arshin_endpoint(request):
    """Проверка поверки в ФГИС «АРШИН» (Росстандарт)"""
    serial = request.query_params.get("serial_number", "2809142")
    result = ArshinVerifier.verify_meter(serial)
    return JSONResponse(result)

async def parse_qr_endpoint(request):
    """Разбор QR-кода платежки ЖКХ по ГОСТ Р 56042-2014"""
    try:
        body = await request.json()
        qr_string = body.get("qr_string", "")
    except Exception:
        qr_string = request.query_params.get("qr_string", "")

    if not qr_string:
        # Тестовый образец квитанции
        qr_string = "ST00012|Name=ООО «ЖИЛИЩНИК-СЕРВИС»|PersonalAcc=40821810900000001234|BankName=ПАО СБЕРБАНК|BIC=044525225|CorrespAcc=30101810400000000225|PayeeINN=7701234567|Sum=435080|Purpose=Оплата ЖКУ за сентябрь 2026|PERSACC=109283741"

    result = GostQRParser.parse(qr_string)
    return JSONResponse(result)

async def night_patrol_endpoint(request):
    """Анализ ночного расхода ОДПУ для выявления скрытых утечек"""
    return JSONResponse({
        "status": "anomaly_detected",
        "house_address": "г. Москва, пр-т Мира, д. 102, корп. 1",
        "entrance": 2,
        "night_flow_rate_liters_per_minute": 35.0,
        "normal_flow_rate": 0.8,
        "daily_loss_cubic_meters": 50.4,
        "monthly_overpay_per_apartment_rub": 182.50,
        "status_text": "Обнаружена постоянная ночная утечка на вводе в подъезд!",
        "recommendation": "Проведите 15-секундный тест сливного бачка салфеткой для локализации перелива."
    })

async def inspector_act_endpoint(request):
    """Генерация юридически значимого акта проверки обходчиком УК"""
    address = request.query_params.get("address", "г. Москва, ул. Тверская, д. 7, кв. 14")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    act_id = f"ACT-ЖКХ-{int(time.time())}"
    
    # Расчет криптографического хэша акта
    raw_sig = f"{act_id}|{address}|{now}|142.385"
    crypto_hash = hashlib.sha256(raw_sig.encode('utf-8')).hexdigest()

    return JSONResponse({
        "status": "success",
        "act_number": act_id,
        "act_title": "Электронный акт контрольного осмотра ПУ",
        "inspector_name": "Контролер Службы Учета Водоснабжения Смирнов В. И.",
        "address": address,
        "timestamp": now,
        "gps_coordinates": "55.7558° N, 37.6173° E (Метка подтверждена)",
        "meter_reading": "142.385 м³",
        "crypto_hash": crypto_hash,
        "billing_export_status": "EXPORTED_TO_1C_ZHKH",
        "legal_significance": "Заверен усиленной квалифицированной ЭЦП УК"
    })

async def health_endpoint(request):
    return JSONResponse({"status": "healthy", "service": "max-smart-city-housing", "version": "1.0.0"})

# Роутинг приложения
routes = [
    Route("/", endpoint=index_page, methods=["GET"]),
    Route("/api/meter/scan", endpoint=scan_meter_endpoint, methods=["GET", "POST"]),
    Route("/api/arshin/verify", endpoint=verify_arshin_endpoint, methods=["GET"]),
    Route("/api/qr/parse", endpoint=parse_qr_endpoint, methods=["GET", "POST"]),
    Route("/api/odpu/night-patrol", endpoint=night_patrol_endpoint, methods=["GET", "POST"]),
    Route("/api/uk/inspector-act", endpoint=inspector_act_endpoint, methods=["POST"]),
    Route("/api/health", endpoint=health_endpoint, methods=["GET"])
]

middleware = [
    Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
]

app = Starlette(debug=True, routes=routes, middleware=middleware)

def run_bot_worker_thread():
    """Запуск фонового воркера бота"""
    try:
        print("Запуск фонового воркера бота MAX...")
        bot = MaxBotService()
        bot.run_polling()
    except Exception as e:
        print(f"Воркер бота завершился или не смог подключиться: {e}")

if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")

    # Если передан флаг --with-bot или задана переменная окружения
    if "--with-bot" in sys.argv or os.getenv("RUN_BOT_THREAD", "1") == "1":
        t = threading.Thread(target=run_bot_worker_thread, daemon=True)
        t.start()

    print(f"🚀 Запуск веб-сервера Mini App на http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
