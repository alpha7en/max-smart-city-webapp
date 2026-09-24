from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from .config import settings
from .image_utils import InvalidImageError
from .llm import LLMError
from .prompts import METER_TYPES
from .recognizer import MeterRecognizer
from .schemas import MeterReading

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.recognizer = MeterRecognizer(settings)
    yield
    await app.state.recognizer.aclose()


app = FastAPI(
    title="Meter Reader",
    description="Распознавание показаний и модели счётчиков воды, электричества, газа и тепла по фото",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "model": settings.model}


@app.post("/recognize", response_model=MeterReading)
async def recognize(
    image: UploadFile = File(..., description="Фото счётчика"),
    meter_type: Optional[str] = Form(
        None, description="Тип, выбранный пользователем: cold_water|hot_water|electricity|gas|heat"
    ),
    tariffs: Optional[str] = Form(None, description="Число тарифов 1..3 (для электричества)"),
) -> MeterReading:
    # Hints are optional and lenient: unknown values fall back to the universal prompt.
    hint_type = meter_type.strip() if meter_type and meter_type.strip() in METER_TYPES else None
    hint_tariffs = int(tariffs) if tariffs and tariffs.strip().isdigit() else None
    data = await image.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file is too large")
    try:
        return await app.state.recognizer.recognize(data, meter_type=hint_type, tariffs=hint_tariffs)
    except InvalidImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
