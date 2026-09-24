"""
Main entry point for MAX Smart City Housing Platform.
Hosts:
- REST API (/api/...)
- MAX Mini-App SPA (/ and /app)
- Background MAX Bot Long Polling worker
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api.routes import router as api_router
from app.bot.client import MaxBotClient
from app.bot.handlers import BotHandler
from app.bot.worker import bot_worker

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("max_smart_city")

bot_task: asyncio.Task = None

async def run_bot_polling_loop():
    """
    Background asynchronous loop for MAX Bot long polling updates.
    Delegates to modular BotWorker.
    """
    return await bot_worker.start()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Launching MAX Smart City Platform v%s...", settings.VERSION)
    global bot_task
    if settings.BOT_TOKEN:
        bot_task = await bot_worker.start()
    yield
    # Shutdown
    logger.info("Shutting down MAX Smart City Platform...")
    await bot_worker.stop()

app = FastAPI(
    title="MAX Умный Дом — Платформа ЖКХ и Счетчиков",
    description=(
        "Комплексное решение для трека «Умный город» на Хакатоне MAX:\n\n"
        "• Взаимодействие жителей и управляющих организаций по ПП РФ № 40 от 26.01.2026 г.;\n"
        "• Мгновенный прием показаний приборов учета через пересылку фото в чат-бот;\n"
        "• Зеленый Щит Безопасности: сверка заводских номеров с реестром ФГИС «АРШИН» (102-ФЗ);\n"
        "• Оплата квитанций по ГОСТ Р 56042-2014 с автоматическим расщеплением на спецсчет 40821 (103-ФЗ);\n"
        "• «Ночной дозор»: крауд-диагностика ночного небаланса ОДПУ со скидкой 10% на квартплату (261-ФЗ);\n"
        "• Гостевой доступ для арендаторов без авторизации через ЕСИА."
    ),
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Static Files
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Mount API Router
app.include_router(api_router)

@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/app", methods=["GET", "HEAD"], include_in_schema=False)
async def serve_miniapp():
    """
    Serves the MAX Mini-App single page application.
    """
    index_file = STATIC_DIR / "index.html"
    return FileResponse(str(index_file))

@app.api_route("/styles.css", methods=["GET", "HEAD"], include_in_schema=False)
async def serve_styles():
    css_file = STATIC_DIR / "styles.css"
    return FileResponse(str(css_file), media_type="text/css")

@app.api_route("/app.js", methods=["GET", "HEAD"], include_in_schema=False)
async def serve_app_js():
    js_file = STATIC_DIR / "app.js"
    return FileResponse(str(js_file), media_type="application/javascript")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from fastapi.responses import Response
    return Response(status_code=204)

