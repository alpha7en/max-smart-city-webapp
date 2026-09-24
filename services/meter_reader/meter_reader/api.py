from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile

from .config import settings
from .image_utils import InvalidImageError
from .llm import LLMError
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
    description="Распознавание показаний и модели счётчиков воды, электричества и газа по фото",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "model": settings.model}


@app.post("/recognize", response_model=MeterReading)
async def recognize(image: UploadFile = File(..., description="Фото счётчика")) -> MeterReading:
    data = await image.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file is too large")
    try:
        return await app.state.recognizer.recognize(data)
    except InvalidImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
