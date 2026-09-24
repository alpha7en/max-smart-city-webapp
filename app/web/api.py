"""API мини-приложения (/api/*). Контракт — SPEC §6 + SPEC_REVIEW D1–D3; сверен с web/static/app.js.

- Авторизация: заголовок X-Max-Init-Data (web/auth.current_user). /api/health объявлен в app/main.py.
- Значения показаний в ответах — числа в единицах счётчика (123.456); на вход — строки, как ввёл пользователь.
- Ошибки — всегда JSON {code, message} с message на русском: HTTPException с detail-словарём → обработчик
  в main.py; невалидный запрос и сбой обработчика ловит _JsonErrors.
- Подача и дашборд — сервисы потоков S2/S4 (адаптеры в конце файла), своей бизнес-логики тут нет.
"""
from __future__ import annotations

import inspect
import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

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
from app.domain import meters as M
from app.repo import ReadingExists, Repo, Row, values_of
from app.web.auth import InitData, current_user

log = logging.getLogger(__name__)
MAX_UPLOAD = photos.MAX_BYTES          # 10 МБ
FORM_OVERHEAD = 64 * 1024              # запас на заголовки multipart при проверке Content-Length
CHUNK = 256 * 1024

# --- Тексты API ---
MSG = {
    "bad_request": "Не получилось прочитать запрос. Обновите страницу и попробуйте ещё раз.",
    "not_found": "Не нашли этот счётчик — возможно, данные уже изменились. Вернитесь на главную.",
    "no_access": "Нет доступа к этому адресу. Попросите собственника открыть доступ в боте.",
    "too_large": "Фото больше 10 МБ. Снимите ещё раз или введите показание вручную.",
    "not_image": "Это не похоже на фото. Сфотографируйте счётчик ещё раз.",
    "no_file": "Не нашли фото в запросе. Сфотографируйте счётчик ещё раз.",
    "internal": "Что-то пошло не так на нашей стороне. Ваши данные на месте — попробуйте ещё раз.",
    # TODO(S2): тексты ниже нужны только временной подаче; после слияния message приходит из SubmitResult.
    "bad_format": "Введите число, например 123,456.",
    "less_than_previous": "Показание меньше прошлого ({prev}). Проверьте цифры.",
    "needs_confirm": "Прирост необычно большой: {delta}. Всё верно?",
    "already_submitted": "За {month} уже передано показание: {prev}. Заменить?",
}
CHAT_SAVED = "Записали показание из мини-приложения.\n\n{title}\n{values}"
CHAT_FLAGGED = "Прирост больше обычного — отметили показание для проверки."
CHAT_MOCK = "Передача в управляющую компанию в MVP смоделирована."
BTN_MORE = "Подать ещё"
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
        "tariffs": m["tariffs"], "address_label": m["address_label"], "serial": m["serial"],
        "verification_due": m["verification_due"], "last": last,
        "submitted_this_period": m.get("last_period") == M.current_period(today),
    }


def _get(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def urgent_kind(kind: Any) -> str:
    """Вид срочного действия для мини-приложения: 'verification' | 'bill' | 'submit'."""
    k = str(kind or "")
    return "verification" if "verif" in k else "bill" if ("bill" in k or "pay" in k) else "submit"


def dashboard_json(d: Any) -> dict:
    """Dashboard (dataclass или dict с lines, urgent{kind,text,days_left}) → JSON контракта."""
    u = _get(d, "urgent")
    urgent = None
    if u:
        urgent = {"kind": urgent_kind(_get(u, "kind")), "text": _get(u, "text"), "days_left": _get(u, "days_left")}
    return {"lines": [str(line) for line in (_get(d, "lines") or [])], "urgent": urgent}


# === Доступ ===

def _deps(request: Request) -> Deps:
    return request.app.state.deps


async def _registered(repo: Repo, init: InitData) -> Row | None:
    user = await repo.get_user(init.max_user_id)
    return user if user and user["registered_at"] else None


async def _meter(repo: Repo, init: InitData, raw_id: Any) -> tuple[Row, Row]:
    """(user, счётчик из user_meters): нет счётчика → 404 not_found, нет доступа → 403 no_access."""
    meter_id = int(raw_id) if str(raw_id or "").isdigit() else 0
    meter = await repo.get_meter(meter_id) if meter_id else None
    if not meter or not meter["active"]:
        raise api_error(404, "not_found")
    user = await _registered(repo, init)
    row = await repo.user_meter(user["id"], meter_id) if user else None
    if not row:
        raise api_error(403, "no_access")
    return user, row


# === Эндпоинты ===

@router.get("/me")
async def me(request: Request, init: InitData = Depends(current_user)) -> dict:
    deps = _deps(request)
    user = await _registered(deps.repo, init)
    if not user:
        return {"registered": False, "user": None, "dashboard": {"lines": [], "urgent": None},
                "meters": [], "addresses": [], "bot_username": deps.bot_username}
    today = clock.today()
    meters = await deps.repo.user_meters(user["id"])
    addresses = await deps.repo.user_addresses(user["id"])
    return {
        "registered": True,
        "user": {"full_name": user["full_name"], "phone": user["phone"],
                 "phone_verified": bool(user["phone_verified"])},
        "dashboard": dashboard_json(await dashboard(deps, user["id"], today)),
        "meters": [meter_json(m, today) for m in meters],
        "addresses": [{"label": a["label"], "access": a["access"], "role": a["role"]} for a in addresses],
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


@router.post("/readings")
async def post_reading(body: ReadingIn, request: Request, background: BackgroundTasks,
                       init: InitData = Depends(current_user)) -> dict:
    deps = _deps(request)
    user, meter = await _meter(deps.repo, init, body.meter_id)
    values = {k: str(v) for k, v in body.values.items() if k in M.FIELDS and v is not None}
    res = await submit_reading(
        deps.repo, user_id=user["id"], meter_id=meter["id"], draft=None, values=values, source="miniapp",
        recognized=None, confirm=body.confirm, replace=body.replace, today=clock.today(),
    )
    if res.status not in ("accepted", "flagged"):
        raise api_error(ERROR_STATUS.get(res.status, 422), res.status, res.message)
    reading = await deps.repo.get_reading(res.reading_id)
    background.add_task(notify_chat, deps, user, meter, reading)
    return {"status": res.status, "reading": reading_json(reading)}


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
    return {"values": units(rec.values), "serial": rec.serial, "confidence": rec.confidence, "stub": rec.stub}


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

def chat_text(meter: Row, reading: Row) -> str:
    labels = M.field_labels(meter["type"], meter["tariffs"])
    vals = values_of(reading)
    lines = [f"{label + ': ' if label else 'Показание: '}**{fmt.value(vals[f], meter['type'])}**"
             for f, label in labels.items()]
    title = f"{M.TYPE_LABELS[meter['type']]} · {fmt.esc(meter['address_label'])}"
    parts = [CHAT_SAVED.format(title=title, values="\n".join(lines))]
    if reading["status"] == "flagged":
        parts.append(CHAT_FLAGGED)
    parts.append(CHAT_MOCK)
    return "\n\n".join(parts)


async def notify_chat(deps: Deps, user: Row, meter: Row, reading: Row) -> None:
    """Сообщение в чат после подачи из мини-приложения. Ошибка отправки только логируется."""
    if deps.api is None:
        return
    target = {"chat_id": user["chat_id"]} if user.get("chat_id") else {"user_id": user["max_user_id"]}
    try:
        await deps.api.send(chat_text(meter, reading), **target,
                            keyboard=K.kb([K.gbtn(BTN_MORE, "submit"), K.gbtn(C.BTN_MENU, "menu")]))
    except Exception as e:  # noqa: BLE001
        log.warning("miniapp reading %s: chat notify failed: %s", reading["id"], e)


# === Адаптеры к потокам S2 (подача) и S4 (дашборд) ===
# TODO(S2)/TODO(S4): после слияния удалить временные реализации _submit_fallback/_dashboard_fallback и
# блоки except ImportError — останутся только импорты.

@dataclass
class _SubmitResult:
    status: str
    reading_id: int | None = None
    meter_id: int | None = None
    previous: dict | None = None
    delta: dict | None = None
    message: str = ""


async def _submit_fallback(repo: Repo, *, user_id: int, meter_id: int | None, draft: dict | None,
                           values: dict[str, str | int], source: str, recognized: dict | None,
                           confirm: bool = False, replace: bool = False, today: date) -> _SubmitResult:
    """TODO(S2): временная подача поверх repo.submit_reading (порядок проверок — SPEC_REVIEW C5)."""
    meter = await repo.user_meter(user_id, meter_id) if meter_id else None
    if not meter:
        return _SubmitResult("no_access", message=MSG["no_access"])
    mtype = meter["type"]
    try:
        parsed = {f: M.parse_value(str(values.get(f, "")), mtype) for f in M.fields_for(meter["tariffs"])}
    except M.ValueParseError:
        return _SubmitResult("bad_format", message=MSG["bad_format"])
    period = M.current_period(today)

    def exists(row: Row) -> _SubmitResult:
        prev = values_of(row)
        text = MSG["already_submitted"].format(month=fmt.month_name(period), prev=fmt.value(prev["t1"], mtype))
        return _SubmitResult("already_submitted", previous=prev, message=text)

    current = await repo.reading_for_period(meter_id, period)
    if current and not replace:
        return exists(current)
    prev_row = await repo.last_reading(meter_id, before_period=period)
    prev = values_of(prev_row) if prev_row else None
    months = M.months_between(prev_row["period"], period) if prev_row else 1
    verdict = M.check_plausibility(mtype, parsed, prev, months)
    if verdict == "less":
        text = MSG["less_than_previous"].format(prev=fmt.value(prev["t1"], mtype))
        return _SubmitResult("less_than_previous", previous=prev, message=text)
    if verdict == "too_big" and not confirm:
        delta = {f: parsed[f] - prev[f] for f in parsed if prev.get(f) is not None}
        text = MSG["needs_confirm"].format(delta=f"{fmt.delta(delta['t1'], mtype)} {fmt.unit(mtype)}")
        return _SubmitResult("needs_confirm", previous=prev, delta=delta, message=text)
    status = "flagged" if verdict == "too_big" else "accepted"
    try:
        _, reading_id = await repo.submit_reading(user_id=user_id, period=period, values=parsed, source=source,
                                                  status=status, recognized=recognized, replace=replace,
                                                  meter_id=meter_id)
    except ReadingExists as e:
        return exists(e.reading)
    return _SubmitResult(status, reading_id, meter_id, prev)


async def _dashboard_fallback(repo: Repo, user_id: int, today: date, day_from: int = 15, day_to: int = 25) -> dict:
    """TODO(S4): простой дашборд (окно подачи, демо-счета) до слияния domain/dashboard.load_dashboard."""
    lines = []
    if await repo.user_meters(user_id):
        w = M.submission_window(today, day_from, day_to)
        lines.append(f"Показания за {fmt.month_name(M.current_period(today))} — до {fmt.day_month(w.end)}, "
                     f"осталось {w.days_left} дн." if w.is_open else f"Следующая подача — с {fmt.day_month(w.start)}")
    for b in await repo.unpaid_bills(user_id):
        lines.append(f"Счёт: {fmt.money(b['amount_kop'])} до {fmt.day_month(date.fromisoformat(b['due_date']))} (демо)")
    return {"lines": lines, "urgent": None}


try:
    from app.readings import submit_reading  # S2
except ImportError:  # TODO(S2)
    submit_reading = _submit_fallback

try:
    from app.domain.dashboard import load_dashboard  # S4
except ImportError:  # TODO(S4)
    load_dashboard = _dashboard_fallback


async def dashboard(deps: Deps, user_id: int, today: date) -> Any:
    """Дашборд пользователя (тот же, что в меню бота)."""
    s = deps.settings
    res = load_dashboard(deps.repo, user_id, today, day_from=s.submit_day_from, day_to=s.submit_day_to)
    return await res if inspect.isawaitable(res) else res
