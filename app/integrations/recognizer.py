"""Распознавание показаний по фото: HTTP-клиент микросервиса services/meter_reader или демо-заглушка.

Контракт микросервиса (services/meter_reader/README.md; маппинг только в HttpRecognizer._parse):
POST {RECOGNIZER_URL} multipart: image=<фото> (+ meter_type, tariffs: по ним сервис берёт промпт типа и отмечает wrong_type)
→ 200 JSON {"meter_type": "hot_water", "reading_text": "00595.825", "integer_digits": "00595",
            "fraction_digits": "825", "tariff": null, "serial_number": "123456", "confidence": 0.95,
            "readable": true, "issues": [], "issue_note": null, ...}
issues — коды из ISSUES (почему не читается / что не так), issue_note — пояснение модели по-русски.
Старые ответы без readable/issues тоже разбираются.

Порог «не распознали» (кроме readable=false и пустого показания):
  — confidence сервиса ниже SERVICE_MIN;
  — есть blurry / digits_not_visible / partially_covered и целых цифр меньше типичного (MIN_WHOLE).
Прочитано, но есть сомнения → свои коды в issues: LOW_CONF (confidence сервиса ниже SERVICE_SURE),
FEW_DIGITS (целых цифр меньше типичного) — бот показывает по ним конкретные предупреждения.
texts — показание как прочитано ('2168'), без дописанных нулей: его и показываем пользователю. 400 не картинка, 413 больше 20 МБ, 502 ошибка Yandex Cloud.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.domain.meters import FIELDS, MeterType, fields_for, reading_text, spec

log = logging.getLogger(__name__)


@dataclass
class Recognition:
    values: dict[str, int | None] = field(default_factory=dict)  # {'t1','t2','t3'} в тысячных
    serial: str | None = None
    confidence: float = 0.0
    stub: bool = False
    error: str | None = None
    issues: list[str] = field(default_factory=list)  # коды ISSUES (+ 'service' — сервис не ответил)
    note: str | None = None                          # пояснение модели (issue_note), как есть
    # Производитель и модель с шильдика: только для БД (readings.recognized_json), пользователю не показываем
    # и с серийным номером не смешиваем.
    brand: str | None = None
    model: str | None = None
    # Как прочитали: только увиденные цифры ('2168', '595,825'); нет — показываем values по формату табло.
    texts: dict[str, str] = field(default_factory=dict)

    def readable(self, fields: tuple[str, ...]) -> bool:
        """Есть что показать пользователю: без ошибки, уверенность не ниже CONF_MIN, хотя бы одно поле."""
        return (not self.error and self.confidence >= CONF_MIN
                and any(self.values.get(f) is not None for f in fields))


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
CONF_MIN = 0.5                            # ниже — «не получилось распознать»
# А вот низкая уверенность сервиса говорит: модель сама сомневается (промпт: нечёткое табло → ≤ 0.5).
SERVICE_MIN, SERVICE_SURE = 0.6, 0.8      # ниже MIN — не распознали; ниже SURE — предупреждаем
# Типичное наименьшее число целых цифр на табло (с ведущими нулями); у тепла табло разные — без правила.
MIN_WHOLE = {MeterType.COLD_WATER: 5, MeterType.HOT_WATER: 5, MeterType.GAS: 5, MeterType.ELECTRICITY: 5}
DOUBT = {"blurry", "digits_not_visible", "partially_covered"}   # вместе с недобором цифр — не распознали
# Коды проблем с фото (контракт services/meter_reader). serial_not_visible показанию не мешает,
# wrong_type — показание можно показать, но с предупреждением.
ISSUES = ("no_meter", "wrong_type", "digits_not_visible", "blurry", "glare", "too_dark", "angle",
          "partially_covered", "multiple_meters", "serial_not_visible", "display_off", "other")
SERVICE = "service"                       # наш код: сервис не ответил / ответил ошибкой
LOW_CONF = "low_confidence"               # наш код: сервис не уверен в цифрах
FEW_DIGITS = "few_digits"                 # наш код: целых цифр меньше, чем обычно у такого счётчика
_VALIDATED = {MeterType.COLD_WATER, MeterType.HOT_WATER}  # на датасетах проверены только водомеры
_TARIFF_FIELD = {"1": "t1", "2": "t2", "3": "t3"}


def _digits(v: Any) -> str:
    return "".join(ch for ch in str(v) if ch.isdigit()) if v is not None else ""


def _split(raw: Any) -> tuple[str, str]:
    """'002168' → ('002168', ''); '595.825' → ('595', '825'): цифры как прочитаны, без дописанных нулей."""
    whole, _, frac = str(raw).replace(",", ".").partition(".")
    return _digits(whole), _digits(frac)


def _summary(data: Any) -> dict:
    """Кратко для лога: без изображения и персональных данных (серийник — только есть/нет)."""
    if not isinstance(data, dict):
        return {"answer": type(data).__name__}
    keys = ("meter_type", "reading_text", "integer_digits", "fraction_digits", "tariff", "confidence",
            "readable", "issues")
    out = {k: data.get(k) for k in keys if k in data}
    out["serial"] = bool(data.get("serial_number"))
    if isinstance(data.get("issue_note"), str):
        out["note"] = data["issue_note"][:120]
    return out


class HttpRecognizer:
    def __init__(self, url: str, timeout: float = 20.0, client: httpx.AsyncClient | None = None):
        self.url = url
        self.timeout = timeout
        self._client = client

    @staticmethod
    def _issues(data: dict) -> tuple[list[str], str | None]:
        raw = data.get("issues")
        codes = [str(c) for c in raw] if isinstance(raw, list) else []
        issues = list(dict.fromkeys(c if c in ISSUES else "other" for c in codes))
        note = data.get("issue_note")
        note = " ".join(str(note).split()) if isinstance(note, str) else ""
        return issues, note or None

    @staticmethod
    def _parse(data: dict, meter_type: str, tariffs: int) -> Recognition:
        values: dict[str, int | None] = dict.fromkeys(FIELDS)
        serial = str(data.get("serial_number") or "").strip() or None
        brand = str(data.get("brand") or "").strip()[:64] or None
        model = str(data.get("model") or "").strip()[:64] or None
        issues, note = HttpRecognizer._issues(data)
        raw = data.get("reading_text") or data.get("reading")
        value = _thousandths(raw)
        try:
            service_conf: float | None = float(data.get("confidence"))
        except (TypeError, ValueError):
            service_conf = None
        whole, frac = _split(raw) if value is not None else ("", "")
        # Целых цифр — по барабанам/сегментам (с ведущими нулями), иначе по тексту показания.
        n_whole = len(_digits(data.get("integer_digits")) or whole)
        few = value is not None and n_whole < MIN_WHOLE.get(meter_type, 0)

        def failed(first: str = "digits_not_visible", *extra: str) -> Recognition:
            codes = list(issues)
            if not any(i != "serial_not_visible" for i in codes):
                codes.insert(0, first)
            codes += [c for c in extra if c not in codes]
            return Recognition(values, serial, 0.0, issues=codes, note=note, brand=brand, model=model)

        if data.get("readable") is False or value is None:
            return failed()
        if service_conf is not None and service_conf < SERVICE_MIN:
            return failed(LOW_CONF)
        if few and DOUBT & set(issues):
            return failed(FEW_DIGITS, FEW_DIGITS)
        # Многотарифный счётчик показывает один тариф за раз: кладём значение в его поле.
        fields = fields_for(tariffs if meter_type == MeterType.ELECTRICITY else 1)
        field_ = _TARIFF_FIELD.get(_digits(data.get("tariff"))[:1], "t1")
        field_ = field_ if field_ in fields else "t1"
        values[field_] = value
        sp = spec(meter_type)
        atypical = len(whole.lstrip("0")) > sp.int_digits or len(frac) > sp.frac_digits
        conf = CONF_OK
        # Тепло (в т.ч. в МВт·ч/ГДж вместо Гкал), свет и газ не проверены — всегда просим проверить цифры.
        if (atypical or few or "wrong_type" in issues or data.get("meter_type") != meter_type
                or meter_type not in _VALIDATED):
            conf = CONF_CHECK
        if few:
            issues.append(FEW_DIGITS)
        if service_conf is not None:
            conf = min(conf, service_conf)
            if service_conf < SERVICE_SURE:
                issues.append(LOW_CONF)
        return Recognition(values, serial, conf, stub=False, issues=issues, note=note, brand=brand, model=model,
                           texts={field_: reading_text(whole, frac)})

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
            data = resp.json()
            log.info("recognizer answer (%s, tariffs=%s): %s", meter_type, tariffs, _summary(data))
            return self._parse(data, meter_type, tariffs)
        except Exception as e:  # noqa: BLE001 — контракт: не бросаем
            log.warning("recognizer failed: %s", e)
            return Recognition(confidence=0.0, error=str(e) or type(e).__name__, issues=[SERVICE])


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
    """ДЕМО: цифры не распознаются, а подставляются (прошлое + прирост, детерминированно от фото).
    Подпись «ошибка» (+ коды из ISSUES, например «ошибка glare») — показать экран неудачи."""

    async def recognize(self, image_path: str, meter_type: str, tariffs: int,
                        *, hint: dict | None = None) -> Recognition:
        hint = hint or {}
        caption = (hint.get("caption") or "").lower()
        if "ошибка" in caption:
            issues = [c for c in ISSUES if c in caption.split()] or ["digits_not_visible"]
            return Recognition(confidence=0.0, stub=True, error="demo_error", issues=issues)
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
