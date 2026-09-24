"""Long polling MAX: GET /updates → задача на каждый апдейт (опрос не ждёт обработку)."""
from __future__ import annotations

import asyncio
import logging

from app.bot.events import parse_update
from app.bot.router import Router
from app.integrations.max_api import MaxApi, MaxApiError
from app.repo import Repo

log = logging.getLogger(__name__)
MARKER_KEY = "poll_marker"
POLL_TIMEOUT = 25
BACKOFF_START, BACKOFF_MAX = 5, 60


class Poller:
    def __init__(self, api: MaxApi, repo: Repo, router: Router):
        self.api, self.repo, self.router = api, repo, router
        self.marker: int | None = None
        self._tasks: set[asyncio.Task] = set()

    async def load_marker(self) -> None:
        raw = await self.repo.kv_get(MARKER_KEY)
        self.marker = int(raw) if raw else None

    async def poll_once(self) -> int:
        """Один запрос /updates: раздать апдейты задачам, сохранить marker. → число апдейтов."""
        res = await self.api.get_updates(self.marker, timeout=POLL_TIMEOUT)
        updates = res.get("updates") or []
        for upd in updates:
            ev = parse_update(upd)
            if ev is None:
                continue
            task = asyncio.create_task(self._handle(ev))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        marker = res.get("marker")
        if marker is not None and marker != self.marker:
            self.marker = int(marker)
            await self.repo.kv_set(MARKER_KEY, str(self.marker))
        return len(updates)

    async def _handle(self, ev) -> None:
        try:
            await self.router.handle(ev)
        except Exception:
            log.exception("update handling failed user=%s kind=%s", ev.user_id, ev.kind)

    async def run(self) -> None:
        """Бесконечный опрос с backoff 5→60 c при ошибках."""
        await self.load_marker()
        backoff = BACKOFF_START
        log.info("polling started, marker=%s", self.marker)
        while True:
            try:
                await self.poll_once()
                backoff = BACKOFF_START
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                level = logging.ERROR if isinstance(e, MaxApiError) and e.status in (401, 403) else logging.WARNING
                log.log(level, "polling error: %s; retry in %ss", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)

    async def drain(self, timeout: float = 10.0) -> None:
        """Дождаться текущих обработок (при остановке)."""
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=timeout)
