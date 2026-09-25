"""Поверка счётчика по ФГИС «Аршин»: проверка после подачи (бот, мини-приложение), фоновое обновление, подписи.

check_meter() — поиск + выбор записи (domain.verification.choose) + запись в БД при высокой уверенности.
Дату от пользователя (verification_source='user') не перезаписываем. Ошибки сети не пишем: перепроверит
планировщик (refresh_tick, раз в сутки, не больше REFRESH_LIMIT счётчиков, с общим троттлингом клиента).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from app.bot.texts import arshin as T
from app.bot.texts.fmt import full_date
from app.domain.verification import Match, Record, card_url, choose, is_demo_id, short_title

log = logging.getLogger(__name__)
WAIT_SECONDS = 6.0          # сколько ждём ответа ФГИС до ответа пользователю
REFRESH_LIMIT = 10          # счётчиков за один фоновый проход
REFRESH_KEY = "arshin_refresh_day"
_BACKGROUND: set[asyncio.Task] = set()

Status = Literal["found", "none", "error", "off", "timeout"]


@dataclass
class Outcome:
    status: Status
    match: Match = field(default_factory=lambda: Match("none"))
    demo: bool = False

    @property
    def level(self) -> str:
        return self.match.level if self.status == "found" else "none"


def is_demo(meter: dict) -> bool:
    return is_demo_id(meter.get("arshin_vri_id"))


def source_label(meter: dict) -> str | None:
    """Подпись источника срока поверки для меню и мини-приложения."""
    if meter.get("verification_source") != "arshin":
        return None
    return T.SOURCE_DEMO if is_demo(meter) else T.SOURCE


def meter_url(meter: dict) -> str | None:
    return card_url(meter.get("arshin_vri_id")) if meter.get("verification_source") == "arshin" else None


def _d(d: date | None) -> str:
    return full_date(d) if d else "—"


def result_line(rec: Record, demo: bool, today: date) -> str:
    """Строка для «Готово»: срок поверки, истёкший срок или «непригоден»."""
    if rec.unfit or rec.valid_date is None:
        return T.UNFIT.format(date=_d(rec.verification_date)) + (f" {T.PICK_DEMO}" if demo else "")
    if rec.valid_date < today:
        return (T.EXPIRED_DEMO if demo else T.EXPIRED).format(date=_d(rec.valid_date))
    return (T.FOUND_DEMO if demo else T.FOUND).format(date=_d(rec.valid_date))


def picked_line(rec: Record, demo: bool) -> str:
    if rec.unfit or rec.valid_date is None:
        return T.PICKED_UNFIT.format(date=_d(rec.verification_date))
    return (T.PICKED_DEMO if demo else T.PICKED).format(date=_d(rec.valid_date))


def option_text(rec: Record) -> str:
    """Кнопка варианта (≤ 40): «СГВ-15 · до 18.10.2029»."""
    if rec.unfit or rec.valid_date is None:
        return T.OPTION_UNFIT.format(title=short_title(rec))
    return T.OPTION.format(title=short_title(rec), date=_d(rec.valid_date))


def _client(deps: Any):
    c = getattr(deps, "arshin", None)
    return c if c is not None and c.enabled else None


async def check_meter(deps: Any, meter_id: int, today: date, now: datetime,
                      brand: str | None = None, model: str | None = None) -> Outcome:
    """Найти запись о поверке и (при высокой уверенности) записать срок. Не бросает."""
    client, repo = _client(deps), deps.repo
    meter = await repo.get_meter(meter_id)
    if client is None or not meter or not meter["serial"]:
        return Outcome("off")
    try:
        if brand is None and model is None:
            brand, model = await repo.meter_photo_hint(meter_id)
        res = await client.lookup(meter["serial"], meter["type"], today, now)
        out = Outcome(res.status, choose(res.records, meter["type"], brand, model), res.demo)
        if out.status in ("found", "none"):
            await _apply(repo, meter_id, out, now)
        return out
    except Exception:  # noqa: BLE001
        log.exception("arshin check failed meter=%s", meter_id)
        return Outcome("error")


async def _apply(repo: Any, meter_id: int, out: Outcome, now: datetime) -> None:
    meter = await repo.get_meter(meter_id)  # свежая строка: пока ждали ФГИС, могли ввести дату из паспорта
    if meter is None:
        return
    if out.level == "high" and meter["verification_source"] != "user":
        await apply_record(repo, meter_id, out.match.best, now)
    else:
        await repo.mark_arshin_checked(meter_id, now)


async def apply_record(repo: Any, meter_id: int, rec: Record, now: datetime) -> None:
    """Запись подтверждена (высокая уверенность или пользователь выбрал её): срок и source='arshin'.
    Последняя поверка «непригоден» — срок не ставим."""
    due = None if rec.unfit or rec.valid_date is None else rec.valid_date.isoformat()
    await repo.set_arshin(meter_id, due=due, vri_id=rec.vri_id, mit_title=rec.mit_title or None, checked_at=now)


async def check_within(deps: Any, meter_id: int, today: date, now: datetime, brand: str | None = None,
                       model: str | None = None, timeout: float | None = None) -> Outcome:
    """check_meter не дольше timeout; не успели — ответ «timeout», проверка доделается в фоне."""
    task = asyncio.create_task(check_meter(deps, meter_id, today, now, brand, model))
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout or WAIT_SECONDS)
    except TimeoutError:
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)
        log.info("arshin check meter=%s continues in background", meter_id)
        return Outcome("timeout")


def background_check(deps: Any, meter_id: int, today: date, now: datetime) -> None:
    """Проверка без ожидания (подача из мини-приложения)."""
    if _client(deps) is None:
        return
    task = asyncio.create_task(check_meter(deps, meter_id, today, now))
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


async def refresh_tick(deps: Any, now: datetime, limit: int = REFRESH_LIMIT) -> int:
    """Раз в сутки: перепроверить счётчики без даты из ФГИС или с устаревшей проверкой. Только live:
    демо-данные в базу массово не пишем. → сколько проверили."""
    client, repo = _client(deps), deps.repo
    if client is None or client.demo or await repo.kv_get(REFRESH_KEY) == now.date().isoformat():
        return 0
    await repo.kv_set(REFRESH_KEY, now.date().isoformat())
    done = 0
    for m in await repo.arshin_refresh_candidates(now, limit):
        out = await check_meter(deps, m["id"], now.date(), now)
        done += 1
        if out.status == "error":  # ФГИС недоступен — не тратим остальные запросы
            break
    return done
