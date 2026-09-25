"""Общие фикстуры: замороженные часы, временная БД, FakeMaxApi, роутер и «чат» с ботом."""
from __future__ import annotations

from datetime import datetime

import pytest

from app import clock
from app.bot.ctx import Deps
from app.bot.events import parse_update
from app.bot.router import Router
from app.config import Settings
from app.integrations.recognizer import StubRecognizer
from app.repo import Repo
from tests import fakes

TOKEN = "test-bot-token"
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=clock.TZ)  # окно подачи открыто, до 25-го 6 дн.


@pytest.fixture(autouse=True)
def frozen_clock():
    clock.set_now(NOW)
    yield NOW
    clock.set_now(None)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(bot_token=TOKEN, bot_username="test_bot", data_dir=tmp_path, arshin_mode="off")  # сеть не трогаем


@pytest.fixture
async def repo(settings):
    r = await Repo.open(settings.db_path)
    yield r
    await r.close()


@pytest.fixture
def api() -> fakes.FakeMaxApi:
    return fakes.FakeMaxApi()


@pytest.fixture
def deps(api, repo, settings) -> Deps:
    return Deps(api=api, repo=repo, settings=settings, recognizer=StubRecognizer(), bot_username="test_bot")


@pytest.fixture
def router(deps) -> Router:
    return Router(deps)


class Chat:
    """Диалог одного пользователя с ботом через настоящий роутер."""

    def __init__(self, router: Router, api: fakes.FakeMaxApi, uid: int = 5273381):
        self.router, self.api, self.uid = router, api, uid

    async def feed(self, update: dict) -> None:
        ev = parse_update(update)
        assert ev is not None
        await self.router.handle(ev)

    async def text(self, text: str) -> None:
        await self.feed(fakes.message_created(self.uid, text))

    async def photo(self, url: str = "https://i.oneme.ru/i?r=photo1", data: bytes = b"\xff\xd8jpeg") -> None:
        self.api.files[url] = data
        await self.feed(fakes.message_created(self.uid, None, [fakes.image(url)]))

    async def press(self, text: str, mid: str = "mid.bot.x") -> None:
        await self.feed(fakes.message_callback(self.uid, self.api.button(text)["payload"], mid))

    async def payload(self, payload: str, mid: str = "mid.bot.x") -> None:
        await self.feed(fakes.message_callback(self.uid, payload, mid))


@pytest.fixture
def chat(router, api) -> Chat:
    return Chat(router, api)
