"""Диплинк из мини-приложения: bot_started с payload открывает нужный экран или сценарий бота."""
from __future__ import annotations

import pytest

from app.bot.states import S
from app.bot.texts import common as C
from app.bot.texts import menu as MT
from app.bot.texts import profile as PT
from app.bot.texts import registration as RT
from tests import fakes
from tests.conftest import Chat
from tests.test_registration import UID, labels, last_kb, register, session


async def start(chat: Chat, payload: str | None) -> None:
    await chat.feed(fakes.bot_started(UID, payload))


async def test_profile_payload_opens_profile(chat, api, repo):
    await register(chat)
    api.clear()
    await start(chat, "profile")
    assert api.last_text().startswith(PT.PROFILE.split("\n")[0]) and "Иванова Анна Сергеевна" in api.last_text()
    assert (await session(repo)).state == S.IDLE


@pytest.mark.parametrize(("payload", "state"), [
    ("add_address", S.PROFILE_ADDR_INPUT),
    ("phone", S.PROFILE_PHONE),
    ("add_meter", S.SUB_NEW_TYPE),
    ("submit", S.SUB_AWAIT_PHOTO),
    ("delete_data", S.PROFILE_DELETE_CONFIRM),
])
async def test_payload_starts_scenario(chat, api, repo, payload, state):
    await register(chat)
    api.clear()
    await start(chat, payload)
    assert (await session(repo)).state == state
    if payload == "add_address":
        assert api.last_text() == PT.ASK_ADDRESS
    if payload == "delete_data":
        assert api.last_text() == PT.DELETE_ASK  # удаляем только после подтверждения в чате
        assert await repo.get_user(UID) is not None


async def test_payload_cancels_running_scenario(chat, api, repo):
    await register(chat)
    await start(chat, "add_meter")
    assert (await session(repo)).state == S.SUB_NEW_TYPE
    await start(chat, "profile")
    assert (await session(repo)).state == S.IDLE and api.last_text().startswith(PT.PROFILE.split("\n")[0])


@pytest.mark.parametrize("payload", [None, "", "unknown", "g|profile|"])
async def test_unknown_or_empty_payload_shows_menu(chat, api, repo, payload):
    await register(chat)
    api.clear()
    await start(chat, payload)
    assert (await session(repo)).state == S.IDLE
    assert not api.last_text().startswith(PT.PROFILE.split("\n")[0])
    assert MT.BTN_PROFILE in labels(last_kb(api))  # меню-дашборд


async def test_unregistered_payload_starts_registration(chat, api, repo):
    await start(chat, "profile")
    assert (await session(repo)).state == S.REG_NAME
    assert any(RT.ASK_NAME.split("\n")[0] in t for t in api.texts())


async def test_payload_during_registration_continues(chat, api, repo):
    await chat.text("привет")
    assert (await session(repo)).state == S.REG_NAME
    api.clear()
    await start(chat, "add_meter")
    assert (await session(repo)).state == S.REG_NAME
    assert api.texts()[0] == C.CONTINUE_REG
