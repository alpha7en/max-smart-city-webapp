"""Распознавание показаний по фото: HTTP-клиент внешнего микросервиса или демо-заглушка.

Контракт микросервиса (уточнит владелец; маппинг — только в HttpRecognizer._parse):
POST {RECOGNIZER_URL} multipart: file=<image>, meter_type, tariffs
→ 200 JSON {"values": {"t1": 123.456, "t2": ..., "t3": ...}, "serial": "...", "confidence": 0.93}
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.domain.meters import FIELDS, MeterType, fields_for

log = logging.getLogger(__name__)


@dataclass
class Recognition:
    values: dict[str, int | None] = field(default_factory=dict)  # {'t1','t2','t3'} в тысячных
    serial: str | None = None
    confidence: float = 0.0
    stub: bool = False
    error: str | None = None


class Recognizer(Protocol):
    async def recognize(self, image_path: str, meter_type: str, tariffs: int,
                        *, hint: dict | None = None) -> Recognition:
        """Не бросает: ошибка → Recognition(confidence=0, error=...).
        hint (необязательно): {'prev': {'t1',...}, 'serial': str|None, 'caption': str|None} — нужен заглушке."""
        ...


def _thousandths(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int((Decimal(str(v).replace(",", ".")) * 1000).to_integral_value())
    except (InvalidOperation, ValueError):
        return None


class HttpRecognizer:
    def __init__(self, url: str, timeout: float = 20.0, client: httpx.AsyncClient | None = None):
        self.url = url
        self.timeout = timeout
        self._client = client

    @staticmethod
    def _parse(data: dict, tariffs: int) -> Recognition:
        raw = data.get("values") or {}
        allowed = fields_for(tariffs)
        values = {f: _thousandths(raw.get(f)) if f in allowed else None for f in FIELDS}
        conf = float(data.get("confidence") or 0.0)
        if values.get("t1") is None:
            conf = 0.0
        return Recognition(values, data.get("serial") or None, conf, stub=False)

    async def recognize(self, image_path: str, meter_type: str, tariffs: int,
                        *, hint: dict | None = None) -> Recognition:
        try:
            content = Path(image_path).read_bytes()
            files = {"file": (Path(image_path).name, content, "image/jpeg")}
            form = {"meter_type": meter_type, "tariffs": str(tariffs)}
            if self._client:
                resp = await self._client.post(self.url, files=files, data=form, timeout=self.timeout)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(self.url, files=files, data=form)
            resp.raise_for_status()
            return self._parse(resp.json(), tariffs)
        except Exception as e:  # noqa: BLE001 — контракт: не бросаем
            log.warning("recognizer failed: %s", e)
            return Recognition(confidence=0.0, error=str(e) or type(e).__name__)


# Правдоподобный месячный прирост заглушки (тысячные): (мин, разброс).
_STUB_STEP = {
    MeterType.COLD_WATER: (2_000, 5_000), MeterType.HOT_WATER: (1_000, 3_000),
    MeterType.ELECTRICITY: (80_000, 200_000), MeterType.GAS: (5_000, 20_000),
    MeterType.HEAT: (100, 500),
}
_STUB_BASE = {
    MeterType.COLD_WATER: 100_000, MeterType.HOT_WATER: 60_000, MeterType.ELECTRICITY: 5_000_000,
    MeterType.GAS: 300_000, MeterType.HEAT: 10_000,
}


class StubRecognizer:
    """ДЕМО: цифры не распознаются, а подставляются (прошлое + прирост, детерминированно от фото)."""

    async def recognize(self, image_path: str, meter_type: str, tariffs: int,
                        *, hint: dict | None = None) -> Recognition:
        hint = hint or {}
        if "ошибка" in (hint.get("caption") or "").lower():
            return Recognition(confidence=0.0, stub=True, error="demo_error")
        try:
            seed = hashlib.sha256(Path(image_path).read_bytes()).digest()
        except OSError:
            seed = hashlib.sha256(image_path.encode()).digest()
        mt = MeterType(meter_type)
        low, spread = _STUB_STEP[mt]
        prev = hint.get("prev") or {}
        values: dict[str, int | None] = {}
        for i, f in enumerate(fields_for(tariffs if mt == MeterType.ELECTRICITY else 1)):
            step = low + int.from_bytes(seed[i * 4:i * 4 + 4], "big") % spread
            base = prev.get(f) if prev.get(f) is not None else _STUB_BASE[mt] * (1 if i == 0 else 0.4)
            values[f] = int(base) + step
        for f in FIELDS:
            values.setdefault(f, None)
        return Recognition(values, hint.get("serial"), 0.9, stub=True)


def get_recognizer(settings) -> Recognizer:
    """RECOGNIZER_URL задан → HTTP-микросервис, иначе демо-заглушка."""
    url = getattr(settings, "recognizer_url", "")
    return HttpRecognizer(url) if url else StubRecognizer()
