"""Клиент ФГИС «Аршин» (Росстандарт): поиск поверки счётчика по заводскому номеру, GET /eapi/vri.

Официальный публичный интерфейс без ключа («Внешние публичные интерфейсы» v2.2). Ограничения:
- больше 2 запросов в секунду с одного IP → 429: держим паузу ≥ 0,6 с между любыми запросами процесса;
- без year или verification_date_start поиск идёт только по текущему году: всегда передаём
  verification_date_start = сегодня − МПИ − 1 год, а на 400/408 («превышен лимит на поиск») перебираем годы;
- rows ≤ 100; даты приходят и в ISO, и в dd.mm.yyyy (domain.verification.parse_date).
На одну проверку — не больше MAX_REQUESTS запросов (вместе с повтором). Один повтор на 429/5xx/таймаут.
Circuit breaker: BREAKER_FAILS сбоев подряд → BREAKER_PAUSE не ходим в сеть. Наружу не бросает.

Режимы (ARSHIN_MODE): live — настоящий API; fixtures — демо-записи в формате документации (arshin_demo.json,
в текстах помечены «демо-данные ФГИС»); off — проверка выключена. Кэш — таблица arshin_cache (repo).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx

from app.domain.serials import normalize_serial
from app.domain.verification import (
    MPI_YEARS,
    Record,
    parse_date,
    parse_item,
    parse_list,
    search_start,
    serial_variants,
    type_fit,
)
from app.integrations.max_api import ssl_context

log = logging.getLogger(__name__)
DEFAULT_BASE = "https://fgis.gost.ru/fundmetrology/eapi"
USER_AGENT = "max-smart-city-bot/1.0 (+https://github.com/alpha7en/max-smart-city-webapp)"
MIN_INTERVAL = 0.6          # с между запросами (лимит API — 2 в секунду)
MAX_REQUESTS = 4            # на одну проверку, вместе с повтором
ROWS = 100                  # максимум API
YEARS_BACK = 6              # перебор по годам при 400/408
RETRY_DELAY = 1.0
RETRY_AFTER_MAX = 5.0
BREAKER_FAILS, BREAKER_PAUSE = 3, 600.0
TTL = {"found": timedelta(days=30), "none": timedelta(days=3)}
DEMO_FILE = Path(__file__).with_name("arshin_demo.json")

Mode = Literal["live", "fixtures", "off"]
Status = Literal["found", "none", "error", "off"]


@dataclass
class Lookup:
    status: Status
    records: list[Record] = field(default_factory=list)
    demo: bool = False


class ArshinClient:
    """Один на процесс (main.lifespan). repo — для кэша (None — без кэша)."""

    def __init__(self, mode: str = "live", base: str = DEFAULT_BASE, repo: Any = None, *,
                 transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 monotonic: Callable[[], float] = time.monotonic, timeout: float = 10.0):
        self.mode: Mode = mode if mode in ("live", "fixtures", "off") else "live"  # type: ignore[assignment]
        self.repo = repo
        self._base, self._transport, self._timeout = base.rstrip("/"), transport, timeout
        self._http: httpx.AsyncClient | None = None  # создаём при первом запросе (off/fixtures сеть не трогают)
        self._sleep, self._clock = sleep, monotonic
        self._lock = asyncio.Lock()
        self._last = -MIN_INTERVAL
        self._fails, self._open_until = 0, 0.0
        self.requests = 0  # для тестов и логов

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    @property
    def demo(self) -> bool:
        return self.mode == "fixtures"

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()

    @property
    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self._base, transport=self._transport, timeout=httpx.Timeout(self._timeout, connect=5.0),
                verify=ssl_context() if self._transport is None else True,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        return self._http

    # --- Публичное ---

    async def lookup(self, serial: str | None, meter_type: str, today: date, now: datetime | None = None) -> Lookup:
        """Записи о поверке прибора с этим номером (без выбора — см. domain.verification.choose)."""
        if not self.enabled:
            return Lookup("off")
        norm = normalize_serial(serial)
        if not norm:
            return Lookup("none")
        if self.demo:
            recs = demo_records(serial or "", meter_type, today)
            return Lookup("found" if recs else "none", recs, demo=True)
        key = f"{meter_type}:{norm}"
        try:
            if cached := await self._cache_get(key, now):
                return cached
            if self._clock() < self._open_until:
                return Lookup("error")
            res = await self._search(serial or "", meter_type, today)
        except Exception:  # noqa: BLE001 — интеграция не должна ронять бота
            log.exception("arshin lookup failed")
            res = Lookup("error")
        self._track(res.status != "error")
        if res.status in TTL:
            await self._cache_put(key, res, now)
        log.info("arshin lookup %s %s: %s, %d records, %d requests total", meter_type, norm[-4:], res.status,
                 len(res.records), self.requests)
        return res

    async def details(self, vri_id: str, budget: list[int] | None = None) -> dict | None:
        """Карточка /vri/{vri_id} → result (miInfo, vriInfo) или None."""
        st, data = await self._get(f"/vri/{vri_id}", {}, budget or [2])
        return data.get("result") if st == "ok" and isinstance(data, dict) else None

    # --- Поиск ---

    async def _search(self, serial: str, meter_type: str, today: date) -> Lookup:
        budget = [MAX_REQUESTS]
        start = search_start(meter_type, today).isoformat()
        conclusive, found = False, []
        for v in serial_variants(serial, meter_type):
            st, data = await self._get("/vri", {"mi_number": v, "verification_date_start": start, "rows": ROWS}, budget)
            if st == "bad":  # 400/408 — широкий запрос: по годам, от текущего назад
                for year in range(today.year, today.year - min(YEARS_BACK, MPI_YEARS.get(meter_type, 6)) - 1, -1):
                    st, data = await self._get("/vri", {"mi_number": v, "year": year, "rows": ROWS}, budget)
                    if st != "ok" or parse_list(data):
                        break
            if st == "error":
                return Lookup("found", found) if found else Lookup("error")
            if st != "ok":
                continue
            recs = parse_list(data)
            if recs is None:
                return Lookup("error")
            conclusive = True
            found += [r for r in recs if r.vri_id not in {x.vri_id for x in found}]
            if any(type_fit(r.mit_title, meter_type) >= 0 for r in recs):
                break
        if found:
            return Lookup("found", await self._fill_unknown(found, budget))
        return Lookup("none" if conclusive else "error")

    async def _fill_unknown(self, records: list[Record], budget: list[int]) -> list[Record]:
        """Нет ни срока, ни признака пригодности в списке — смотрим карточку (applicable/inapplicable)."""
        out = []
        for r in records:
            if r.valid_date is None and r.applicable is True and budget[0] > 0:
                res = await self.details(r.vri_id, budget) or {}
                info = res.get("vriInfo") or {}
                if "inapplicable" in info:
                    r = replace(r, applicable=False)
                elif parse_date(info.get("validDate")):
                    r = replace(r, valid_date=parse_date(info.get("validDate")))
            out.append(r)
        return out

    async def _get(self, path: str, params: dict, budget: list[int]) -> tuple[str, Any]:
        """→ ('ok', json) | ('bad', None) — 400/408 | ('error', None) | ('budget', None)."""
        for attempt in (0, 1):
            if budget[0] <= 0:
                return "budget", None
            budget[0] -= 1
            await self._throttle()
            self.requests += 1
            try:
                resp = await self._client.get(path, params=params)
            except httpx.HTTPError as e:
                log.warning("arshin %s: %r", path, e)
                if attempt == 0:
                    await self._sleep(RETRY_DELAY)
                    continue
                return "error", None
            code = resp.status_code
            if code == 429 or code >= 500:
                log.warning("arshin %s: HTTP %s", path, code)
                if attempt == 0:
                    await self._sleep(_retry_after(resp) if code == 429 else RETRY_DELAY)
                    continue
                return "error", None
            if code in (400, 408):
                log.info("arshin %s: HTTP %s %s", path, code, resp.text[:200])
                return "bad", None
            if code == 404:
                return "ok", {"result": {"items": []}}
            if code != 200:
                return "error", None
            try:
                return "ok", resp.json()
            except ValueError:  # HTML-заглушка со статусом 200
                log.warning("arshin %s: not JSON", path)
                return "error", None
        return "error", None

    async def _throttle(self) -> None:
        async with self._lock:
            wait = self._last + MIN_INTERVAL - self._clock()
            if wait > 0:
                await self._sleep(wait)
            self._last = self._clock()

    def _track(self, ok: bool) -> None:
        if ok:
            self._fails = 0
            return
        self._fails += 1
        if self._fails >= BREAKER_FAILS:
            self._open_until = self._clock() + BREAKER_PAUSE
            self._fails = 0
            log.warning("arshin: %d failures in a row, pause %d s", BREAKER_FAILS, BREAKER_PAUSE)

    # --- Кэш ---

    async def _cache_get(self, key: str, now: datetime | None) -> Lookup | None:
        if self.repo is None:
            return None
        row = await self.repo.arshin_cache_get(key)
        if not row or row["status"] not in TTL:
            return None
        fetched = datetime.fromisoformat(row["fetched_at"])
        if now is not None and now - fetched >= TTL[row["status"]]:
            return None
        return Lookup(row["status"], [Record.from_dict(d) for d in json.loads(row["payload"] or "[]")])

    async def _cache_put(self, key: str, res: Lookup, now: datetime | None) -> None:
        if self.repo is not None and now is not None:
            payload = json.dumps([r.to_dict() for r in res.records], ensure_ascii=False)
            await self.repo.arshin_cache_put(key, payload, res.status, now.isoformat())


def _retry_after(resp: httpx.Response) -> float:
    try:
        return min(max(float(resp.headers.get("Retry-After", "2")), 0.0), RETRY_AFTER_MAX)
    except ValueError:
        return 2.0


# --- Демо-режим (fixtures) ---

def demo_records(serial: str, meter_type: str, today: date) -> list[Record]:
    """Записи из arshin_demo.json (элементы ответа /eapi/vri, как в документации) с номером пользователя
    и датами от сегодняшнего дня. Номер на «0» — записи нет; на «9» — две записи одного вида (коллизия номеров)."""
    tail = (normalize_serial(serial) or "")[-1:]
    if tail == "0":
        return []
    group = "water" if meter_type in ("cold_water", "hot_water") else meter_type
    items = json.loads(DEMO_FILE.read_text("utf-8"))[group]
    out = []
    for i, item in enumerate(items if tail == "9" else items[:1]):
        vdate = today - timedelta(days=700 + 90 * i)
        valid = add_years(vdate, MPI_YEARS.get(meter_type, 6)) - timedelta(days=1)
        rec = parse_item(item)
        if rec:
            out.append(replace(rec, mi_number=serial, verification_date=vdate, valid_date=valid))
    return out


def add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 февраля
        return d.replace(year=d.year + years, day=28)
