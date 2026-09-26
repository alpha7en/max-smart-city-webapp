"""Точка входа: FastAPI (API и статика мини-приложения) + бот (long polling) + планировщик."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.bot.ctx import Deps
from app.bot.texts import hackathon_demo as HT
from app.bot.texts.api import MSG
from app.config import Settings, load_settings
from app.integrations.arshin import ArshinClient
from app.integrations.max_api import MaxApi
from app.integrations.recognizer import get_recognizer
from app.repo import Repo
from app.scheduler import run_scheduler
from app.web import api

log = logging.getLogger("app")
STATIC_DIR = Path(__file__).parent / "web" / "static"
COMMANDS = [("start", "Главное меню"), ("demo", "Примеры уведомлений (хакатон)")]


def bot_commands(settings: Settings) -> list[tuple[str, str]]:
    """Список команд для MAX (PATCH /me/commands); /demo_profile — только для хакатона."""
    return COMMANDS + ([("demo_profile", HT.COMMAND_DESCRIPTION)] if settings.hackathon_demo_profile else [])


def _address_service(settings: Settings):
    """AddressService из потока S3; без модуля — None (регистрация адреса недоступна)."""
    try:
        from app.integrations.address_service import get_address_service
    except ImportError:
        log.warning("address service module is missing")
        return None
    return get_address_service(settings)


async def _start_bot(deps: Deps) -> asyncio.Task | None:
    from app.bot.poller import Poller
    from app.bot.router import Router

    try:
        me = await deps.api.get_me()
        log.info("MAX bot: id=%s username=%s", me.get("user_id"), me.get("username"))
        if not deps.bot_username:
            deps.bot_username = me.get("username") or ""
        elif me.get("username") and me["username"] != deps.bot_username:
            log.warning("BOT_USERNAME=%s differs from GET /me username=%s", deps.bot_username, me["username"])
    except Exception as e:  # noqa: BLE001 — бот поднимется, poller будет повторять
        log.error("GET /me failed: %s", e)
    try:
        await deps.api.set_commands(bot_commands(deps.settings))
    except Exception as e:  # noqa: BLE001
        log.warning("set commands failed: %s", e)
    poller = Poller(deps.api, deps.repo, Router(deps))
    return asyncio.create_task(poller.run(), name="poller")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    repo = await Repo.open(settings.db_path)
    api_client = MaxApi(settings.bot_token, settings.max_api_base) if settings.bot_token else None
    arshin = ArshinClient(settings.arshin_mode, settings.arshin_base, repo,
                          fallback_ips=settings.arshin_fallback_ips)
    log.info("ARSHIN_MODE=%s", arshin.mode)
    deps = Deps(api=api_client, repo=repo, settings=settings, recognizer=get_recognizer(settings),
                addresses=_address_service(settings), bot_username=settings.bot_username, arshin=arshin)
    app.state.repo, app.state.deps = repo, deps
    tasks: list[asyncio.Task] = [asyncio.create_task(run_scheduler(deps), name="scheduler")]
    if api_client:
        bot_task = await _start_bot(deps)
        if bot_task:
            tasks.append(bot_task)
    else:
        log.warning("BOT_TOKEN is not set — bot is disabled, only the web part is running")
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        if api_client:
            await api_client.close()
        await arshin.close()
        await repo.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="ЖКХ-бот MAX", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    if settings.miniapp_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.miniapp_origins),
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["X-Max-Init-Data", "Content-Type"],
            allow_credentials=False,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Ошибки API — всегда {code, message}."""
        status = exc.status_code
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            body = exc.detail
        elif status in (400, 422):  # тело не разобралось (битый JSON, не UTF-8) — как невалидный запрос
            status, body = 422, {"code": "bad_request", "message": MSG["bad_request"]}
        else:
            body = {"code": f"http_{exc.status_code}", "message": str(exc.detail)}
        return JSONResponse(body, status_code=status, headers=getattr(exc, "headers", None))

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/app/")

    app.include_router(api.router)
    app.mount("/app", StaticFiles(directory=STATIC_DIR, html=True), name="miniapp")
    return app


app = create_app()
