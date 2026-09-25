"""API мини-приложения (/api/*). Контракт — SPEC §6 + SPEC_REVIEW D1–D3; сверен с web/static/app.js.

- Авторизация: заголовок X-Max-Init-Data (web/auth.current_user). /api/health объявлен в app/main.py.
- Значения показаний в ответах — числа в единицах счётчика (123.456); на вход — строки, как ввёл пользователь.
- Ошибки — всегда JSON {code, message} с message на русском: HTTPException с detail-словарём → обработчик
  в main.py; невалидный запрос и сбой обработчика ловит _JsonErrors.
- Подача и дашборд — сервисы потоков S2 (адаптер в конце файла) и domain/dashboard; своей бизнес-логики тут нет.
- dashboard в /api/me — Dashboard.to_api(): lines/urgent для текста и структура window/bill/verification/pending
  для экранов (мини-приложение строки не разбирает).
"""
from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import clock
from app.bot import keyboards as K
from app.bot import photos
from app.bot.ctx import Deps
from app.bot.texts import common as C
from app.bot.texts import fmt
from app.bot.texts import submission as TS
from app.bot.texts.api import BTN_MORE, CHAT_FLAGGED, CHAT_MOCK, CHAT_SAVED, MSG
from app.domain import meters as M
from app.domain.dashboard import Dashboard, load_dashboard
from app.domain.serials import clean_serial, usable_serial, validate_serial
from app.integrations.recognizer import Recognition
from app.readings import submit_reading
from app.repo import Repo, Row, values_of
from app.web.auth import InitData, current_user

log = logging.getLogger(__name__)
MAX_UPLOAD = photos.MAX_BYTES          # 10 МБ
FORM_OVERHEAD = 64 * 1024              # запас на заголовки multipart при проверке Content-Length
CHUNK = 256 * 1024

EMPTY_DASHBOARD = {"lines": [], "urgent": None, "window": None, "bill": None, "bills": [], "verification": None,
                   "pending": [], "submitted": 0, "total": 0}
ERROR_STATUS = {"bad_format": 422, "less_than_previous": 422, "needs_confirm": 409,
                "already_submitted": 409, "no_access": 403, "not_found": 404}


def api_error(status: int, code: str, message: str | None = None) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message or MSG.get(code) or MSG["internal"]})


class _JsonErrors(APIRoute):
    """Невалидный запрос → 422 bad_request, сбой обработчика → 500 internal; всегда {code, message}."""

    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def run(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse({"code": "bad_request", "message": MSG["bad_request"]}, 422)
            except StarletteHTTPException:
                raise
            except Exception:
                log.exception("api error: %s %s", request.method, request.url.path)
                return JSONResponse({"code": "internal", "message": MSG["internal"]}, 500)

        return run


router = APIRouter(prefix="/api", route_class=_JsonErrors)


# === Сериализация ===

def units(values: dict[str, int | None]) -> dict[str, float | None]:
    """Тысячные → числа в единицах счётчика: {'t1': 123.456, 't2': None, 't3': None}."""
    return {f: None if values.get(f) is None else values[f] / 1000 for f in M.FIELDS}


def iso(ts: str | None) -> str | None:
    """'YYYY-MM-DD HH:MM:SS' (UTC, как в БД) → ISO 8601 в местной зоне."""
    if not ts:
        return None
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).astimezone(clock.TZ).isoformat()


def reading_json(r: Row) -> dict:
    return {"id": r["id"], "period": r["period"], "values": units(values_of(r)), "source": r["source"],
            "status": r["status"], "created_at": iso(r["created_at"])}


def meter_json(m: Row, today: date) -> dict:
    """m — строка repo.user_meters (с address_label и last_*)."""
    last = None
    if m.get("last_id"):
        last = {"period": m["last_period"], "values": units(values_of(m)), "created_at": iso(m["last_created_at"])}
    return {
        "id": m["id"], "type": m["type"], "type_label": M.TYPE_LABELS[m["type"]], "unit": M.UNITS[m["type"]],
        "tariffs": m["tariffs"], "address_id": m["address_id"], "address_label": m["address_label"],
        "serial": M.format_serial(m["serial"], m["type"]),
        "verification_due": m["verification_due"], "last": last,
        "submitted_this_period": m.get("last_period") == M.current_period(today),
    }


def address_json(a: Row) -> dict:
    """Адрес профиля: короткая подпись, полный текст, доступ; verified=False — адрес не сверен с ФИАС."""
    return {"id": a["id"], "label": a["label"], "full_text": a["full_text"], "access": a["access"],
            "role": a["role"], "verified": a["status"] != "unverified"}


# === Доступ ===

def _deps(request: Request) -> Deps:
    return request.app.state.deps


async def _registered(repo: Repo, init: InitData) -> Row | None:
    user = await repo.get_user(init.max_user_id)
    return user if user and user["registered_at"] else None


async def _meter(repo: Repo, init: InitData, raw_id: Any) -> tuple[Row, Row]:
    """(user, счётчик из user_meters): нет счётчика → 404 not_found, нет доступа → 403 no_access."""
    meter_id = K.parse_id(raw_id)
    meter = await repo.get_meter(meter_id) if meter_id else None
    if not meter or not meter["active"]:
        raise api_error(404, "not_found")
    user = await _registered(repo, init)
    row = await repo.user_meter(user["id"], meter_id) if user else None
    if not row:
        raise api_error(403, "no_access")
    return user, row


async def dashboard(deps: Deps, user_id: int, today: date) -> Dashboard:
    """Дашборд пользователя (тот же, что в меню бота)."""
    s = deps.settings
    return await load_dashboard(deps.repo, user_id, today, day_from=s.submit_day_from, day_to=s.submit_day_to)


# === Эндпоинты ===

@router.get("/me")
async def me(request: Request, init: InitData = Depends(current_user)) -> dict:
    deps = _deps(request)
    user = await _registered(deps.repo, init)
    if not user:
        return {"registered": False, "user": None, "dashboard": EMPTY_DASHBOARD,
                "meters": [], "addresses": [], "bot_username": deps.bot_username}
    today = clock.today()
    meters = await deps.repo.user_meters(user["id"])
    addresses = await deps.repo.user_addresses(user["id"])
    return {
        "registered": True,
        "user": {"full_name": user["full_name"], "phone": user["phone"],
                 "phone_verified": bool(user["phone_verified"])},
        "dashboard": (await dashboard(deps, user["id"], today)).to_api(),
        "meters": [meter_json(m, today) for m in meters],
        "addresses": [address_json(a) for a in addresses],
        "bot_username": deps.bot_username,
    }


@router.get("/meters/{meter_id}")
async def meter_detail(meter_id: str, request: Request, init: InitData = Depends(current_user)) -> dict:
    repo = _deps(request).repo
    _, meter = await _meter(repo, init, meter_id)
    history = await repo.history(meter["id"], limit=12)
    return {**meter_json(meter, clock.today()), "history": [reading_json(r) for r in history]}


class ReadingIn(BaseModel):
    meter_id: int
    values: dict[str, str | int | float | None]
    confirm: bool = False
    replace: bool = False
    # Показание с фото (source='photo') у счётчика без номера принимаем только с номером (serial):
    # на фото его не разобрали — пользователь вводит его сам. Ручной ввод номер не требует.
    source: Literal["photo", "manual"] | None = None
    serial: str | None = None


async def _serial_for(repo: Repo, meter: Row, body: ReadingIn) -> str | None:
    """Номер для счётчика без номера: проверка по типу; с фото без номера → 422 serial_required."""
    if meter["serial"]:
        return None
    raw = (body.serial or "").strip()
    if not raw:
        if body.source == "photo":
            raise api_error(422, "serial_required", TS.API_SERIAL_REQUIRED)
        return None
    if not validate_serial(raw, meter["type"]).usable:
        raise api_error(422, "serial_bad", TS.API_SERIAL_BAD.format(example=TS.SERIAL_EXAMPLES[meter["type"]]))
    serial = clean_serial(raw) or ""
    other = await repo.find_meter_by_serial(meter["address_id"], serial)
    if other and other["id"] != meter["id"]:
        raise api_error(422, "serial_taken", TS.API_SERIAL_TAKEN.format(serial=M.format_serial(serial, meter["type"])))
    return serial


@router.post("/readings")
async def post_reading(body: ReadingIn, request: Request, background: BackgroundTasks,
                       init: InitData = Depends(current_user)) -> dict:
    deps = _deps(request)
    user, meter = await _meter(deps.repo, init, body.meter_id)
    values = {k: str(v) for k, v in body.values.items() if k in M.FIELDS and v is not None}
    serial = await _serial_for(deps.repo, meter, body)
    res = await submit_reading(
        deps.repo, user_id=user["id"], meter_id=meter["id"], draft=None, values=values, source="miniapp",
        recognized=None, confirm=body.confirm, replace=body.replace, today=clock.today(), serial=serial,
    )
    if res.status not in ("accepted", "flagged"):
        raise api_error(ERROR_STATUS.get(res.status, 422), res.status, res.message)
    reading = await deps.repo.get_reading(res.reading_id)
    background.add_task(notify_chat, deps, user, meter, reading, values)
    out = {"status": res.status, "reading": reading_json(reading)}
    if serial:
        out["serial"] = M.format_serial(serial, meter["type"])
    return out


@router.post("/recognize")
async def recognize(request: Request, init: InitData = Depends(current_user)) -> dict:
    deps = _deps(request)
    if _content_length(request) > MAX_UPLOAD + FORM_OVERHEAD:
        raise api_error(413, "too_large")
    try:
        form = await request.form()
    except Exception as e:  # noqa: BLE001 — битый multipart
        raise api_error(400, "bad_request") from e
    try:
        user, meter = await _meter(deps.repo, init, form.get("meter_id"))
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise api_error(422, "no_file")
        if not (upload.content_type or "").lower().startswith("image/"):
            raise api_error(415, "not_image")
        return await _recognize_upload(deps, user, meter, upload)
    finally:
        await form.close()


def _content_length(request: Request) -> int:
    try:
        return int(request.headers.get("content-length") or 0)
    except ValueError:
        return 0


async def _recognize_upload(deps: Deps, user: Row, meter: Row, upload: UploadFile) -> dict:
    """Сохраняет фото во временный файл (0600, ≤10 МБ), распознаёт и всегда удаляет файл."""
    photo_id = uuid.uuid4().hex
    path = deps.settings.photos_dir / f"{photo_id}.jpg"
    try:
        if not await _save(upload, path):
            raise api_error(415, "not_image")
        now = clock.now()
        await deps.repo.add_photo(photo_id, user["id"], str(path), now, now + photos.PHOTO_TTL)
        hint = {"prev": values_of(meter) if meter.get("last_id") else None, "serial": meter["serial"], "caption": None}
        rec = await deps.recognizer.recognize(str(path), meter["type"], meter["tariffs"], hint=hint)
    finally:
        await photos.delete_photo(deps.repo, photo_id)
        path.unlink(missing_ok=True)
    rec.serial = usable_serial(rec.serial, meter["type"])  # год, ГОСТ, Qn — не номер
    # texts — как прочитали ('2168'): в поле ввода кладём их, а не 2168.0 → «2168,00».
    return {"values": units(rec.values), "texts": rec.texts, "serial": M.format_serial(rec.serial, meter["type"]),
            "confidence": rec.confidence, "stub": rec.stub, **recognition_notes(meter, rec)}


def recognition_notes(meter: Row, rec: Recognition) -> dict:
    """Почему не распознали / о чём предупредить: readable, issues (коды), note (пояснение модели),
    missing (поля, которых нет на фото: многотарифный показывает тарифы по очереди — их вводят вручную),
    message (что не так и что делать, обычный текст; при удачном чтении — конкретные предупреждения о фото),
    serial_mismatch + serial_note (номер на фото не тот или не виден), serial_required (у счётчика нет номера
    и на фото его не разобрали — /api/readings с source='photo' примет показание только с serial)."""
    mtype = meter["type"]
    fields = M.fields_for(M.tariffs_of(mtype, meter["tariffs"]))
    readable = rec.readable(fields)
    missing = [f for f in fields if rec.values.get(f) is None] if readable else list(fields)
    parts: list[str] = []
    if not readable:
        lines = TS.issue_lines(rec.issues, rec.note, mtype, markup=False)
        tail = TS.FAILED_TAIL_SERVICE if "service" in rec.issues else TS.FAILED_TAIL
        parts.append(" ".join([*lines, tail]) if lines else TS.API_UNREADABLE)
    else:
        if missing:
            names = dict(zip(M.FIELDS, ("Т1", "Т2", "Т3"), strict=True))
            parts.append(TS.API_PARTIAL.format(fields=", ".join(names[f] for f in missing)))
        if "wrong_type" in rec.issues:
            parts.append(TS.WRONG_TYPE_WARN)
        parts += TS.review_warnings(rec.issues, mtype)
    photo, saved = M.normalize_serial(rec.serial), M.normalize_serial(meter["serial"])
    mismatch = bool(readable and photo and saved and photo != saved)
    note = TS.API_SERIAL_MISMATCH.format(photo=M.format_serial(rec.serial, mtype),
                                         saved=M.format_serial(meter["serial"], mtype)) if mismatch else None
    if readable and saved and not photo:
        note = TS.API_SERIAL_NOT_ON_PHOTO.format(serial=M.format_serial(meter["serial"], mtype))
    return {"readable": readable, "issues": rec.issues, "note": rec.note, "missing": missing,
            "message": " ".join(parts) or None, "serial_mismatch": mismatch, "serial_note": note,
            "serial_required": not saved and not photo}


async def _save(upload: UploadFile, path: Path) -> int:
    """Копирует файл по частям; больше MAX_UPLOAD → 413. → размер в байтах."""
    path.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        while chunk := await upload.read(CHUNK):
            size += len(chunk)
            if size > MAX_UPLOAD:
                raise api_error(413, "too_large")
            f.write(chunk)
    return size


# === Подтверждение в чат (D3) ===

def chat_text(meter: Row, reading: Row, typed: dict[str, str] | None = None) -> str:
    """typed — значения, как их ввели ('2168'): показываем их, если они и записаны (без выдуманных ',00')."""
    labels = M.field_labels(meter["type"], meter["tariffs"])
    vals = values_of(reading)

    def shown(f: str) -> str:
        text = M.typed_text((typed or {}).get(f) or "")
        ok = (typed or {}).get(f) and M.text_matches(text, vals[f], meter["type"])
        return f"{text} {M.UNITS[meter['type']]}" if ok else fmt.value(vals[f], meter["type"])

    lines = [f"{label + ': ' if label else 'Показание: '}**{shown(f)}**" for f, label in labels.items()]
    title = f"{M.TYPE_LABELS[meter['type']]} · {fmt.esc(meter['address_label'])}"
    parts = [CHAT_SAVED.format(title=title, values="\n".join(lines))]
    if reading["status"] == "flagged":
        parts.append(CHAT_FLAGGED)
    parts.append(CHAT_MOCK)
    return "\n\n".join(parts)


async def notify_chat(deps: Deps, user: Row, meter: Row, reading: Row, typed: dict[str, str] | None = None) -> None:
    """Сообщение в чат после подачи из мини-приложения. Ошибка отправки только логируется."""
    if deps.api is None:
        return
    target = {"chat_id": user["chat_id"]} if user.get("chat_id") else {"user_id": user["max_user_id"]}
    try:
        await deps.api.send(chat_text(meter, reading, typed), **target,
                            keyboard=K.kb([K.gbtn(BTN_MORE, "submit"), K.gbtn(C.BTN_MENU, "menu")]))
    except Exception as e:  # noqa: BLE001
        log.warning("miniapp reading %s: chat notify failed: %s", reading["id"], e)
