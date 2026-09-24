"""Распознавание показаний по фото: HTTP-клиент микросервиса services/meter_reader или демо-заглушка.

Контракт микросервиса (services/meter_reader/README.md; маппинг только в HttpRecognizer._parse):
POST {RECOGNIZER_URL} multipart: image=<фото> (+ meter_type, tariffs как подсказка, сервис их пока не читает)
→ 200 JSON {"meter_type": "hot_water", "reading_text": "00595.825", "integer_digits": "00595",
            "fraction_digits": "825", "tariff": null, "serial_number": "123456", "confidence": 0.95, ...}
400 не картинка, 413 больше 20 МБ, 502 ошибка Yandex Cloud.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.domain.meters import FIELDS, MeterType, fields_for, spec

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


# Уверенность выставляем сами: у сервиса она почти всегда 0.95 и ничего не говорит.
CONF_OK, CONF_CHECK = 0.9, 0.6            # CHECK попадает в «Проверьте цифры» бота и мини-приложения
_VALIDATED = {MeterType.COLD_WATER, MeterType.HOT_WATER}  # на датасетах проверены только водомеры
_TARIFF_FIELD = {"1": "t1", "2": "t2", "3": "t3"}


def _digits(v: Any) -> str:
    return "".join(ch for ch in str(v) if ch.isdigit()) if v is not None else ""


class HttpRecognizer:
    def __init__(self, url: str, timeout: float = 20.0, client: httpx.AsyncClient | None = None):
        self.url = url
        self.timeout = timeout
        self._client = client

    @staticmethod
    def _parse(data: dict, meter_type: str, tariffs: int) -> Recognition:
        values: dict[str, int | None] = dict.fromkeys(FIELDS)
        serial = str(data.get("serial_number") or "").strip() or None
        value = _thousandths(data.get("reading_text") or data.get("reading"))
        if value is None:
            return Recognition(values, serial, 0.0)
        # Многотарифный счётчик показывает один тариф за раз: кладём значение в его поле.
        fields = fields_for(tariffs if meter_type == MeterType.ELECTRICITY else 1)
        field_ = _TARIFF_FIELD.get(_digits(data.get("tariff"))[:1], "t1")
        values[field_ if field_ in fields else "t1"] = value
        sp = spec(meter_type)
        whole, frac = _digits(data.get("integer_digits")), _digits(data.get("fraction_digits"))
        atypical = len(whole.lstrip("0")) > sp.int_digits or len(frac) > sp.frac_digits
        conf = CONF_OK
        if atypical or data.get("meter_type") != meter_type or meter_type not in _VALIDATED:
            conf = CONF_CHECK
        try:
            conf = min(conf, float(data.get("confidence")))
        except (TypeError, ValueError):
            pass
        return Recognition(values, serial, conf, stub=False)

    async def recognize(self, image_path: str, meter_type: str, tariffs: int,
                        *, hint: dict | None = None) -> Recognition:
        try:
            content = Path(image_path).read_bytes()
            files = {"image": (Path(image_path).name, content, "image/jpeg")}
            form = {"meter_type": meter_type, "tariffs": str(tariffs)}
            if self._client:
                resp = await self._client.post(self.url, files=files, data=form, timeout=self.timeout)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(self.url, files=files, data=form)
            resp.raise_for_status()
            return self._parse(resp.json(), meter_type, tariffs)
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
