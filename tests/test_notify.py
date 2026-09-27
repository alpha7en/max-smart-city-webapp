"""Уведомление об окне подачи: когда приходит, один раз, тихие часы, после подачи — тишина."""
from __future__ import annotations

from datetime import datetime

from app import clock
from app.bot.ctx import Deps
from app.bot.texts import common as C
from app.bot.texts import notify as T
from app.scheduler import notify_tick
from tests import fakes

UID = 5273381
ADDR = {"full_text": "г Москва, ул Арбат, д 47 к 1, кв 32", "region": "Москва", "locality": "Москва",
        "street": "Арбат", "house": "47", "block": "к1", "flat": "32", "status": "unverified", "source": "local"}


def at(y: int, m: int, d: int, h: int = 12) -> datetime:
    return datetime(y, m, d, h, 0, tzinfo=clock.TZ)


async def make_user(repo, now: datetime) -> tuple[dict, int]:
    """Зарегистрированный пользователь с адресом и демо-счётом. → (user, address_id)."""
    u = await repo.ensure_user(UID, fakes.chat_of(UID))
    r = await repo.complete_registration(
        u["id"], full_name="Иванова Анна Сергеевна", phone="+79123456789", phone_verified=True,
        address=ADDR, norm_key="k1", raw_input="Арбат 47к1 кв 32", now=now, labeler=lambda rows: ["Арбат 47к1, кв 32"])
    return await repo.get_user(UID), r["address_id"]


def labels(kb: dict) -> list[list[str]]:
    return [[b["text"] for b in row] for row in kb["payload"]["buttons"]]


async def tick(deps: Deps, when: datetime) -> list[tuple[str, dict]]:
    """Тик планировщика в момент when → [(текст, клавиатура)] отправленных уведомлений."""
    clock.set_now(when)
    deps.api.clear()
    await notify_tick(deps, when)
    return [(kw["text"], kw["keyboard"]) for kw in deps.api.named("send")]


async def sent_kinds(repo) -> list[tuple[str, str]]:
    return [(r["kind"], r["dedup_key"]) for r in await repo._all("SELECT * FROM notifications ORDER BY id")]


async def test_submission_window_notifications(deps, repo, api):
    user, aid = await make_user(repo, now=at(2026, 10, 14))  # счёт до 10 ноября — ещё не срочно
    mid = await repo.create_meter(aid, "cold_water", created_by=user["id"])
    assert await tick(deps, at(2026, 10, 14, 12)) == []  # окно ещё закрыто
    ((text, kb),) = await tick(deps, at(2026, 10, 15, 10))
    assert text.startswith(T.SUBMIT_OPEN.format(month="октябрь", deadline="25 октября"))
    assert T.SUBMIT_NOT_DONE.format(meters="Хол. вода · г Москва, ул Арбат, д 47 к 1, кв 32") in text
    assert labels(kb) == [[T.BTN_SUBMIT], [C.BTN_MENU]]
    assert [row[0]["payload"] for row in kb["payload"]["buttons"]] == ["g|n_submit|", "g|menu|"]
    assert await tick(deps, at(2026, 10, 15, 11)) == []  # один раз
    assert await tick(deps, at(2026, 10, 22, 22)) == []  # тихие часы
    assert await tick(deps, at(2026, 10, 22, 8)) == []
    ((text, _),) = await tick(deps, at(2026, 10, 22, 9))
    assert text.startswith(T.SUBMIT_LAST.format(month="октябрь", deadline="25 октября", left="осталось **3 дня**"))
    assert await tick(deps, at(2026, 10, 24, 12)) == []
    # Всё подано → ничего.
    await repo.add_reading(mid, user["id"], "2026-11", {"t1": 1000}, "manual")
    assert await tick(deps, at(2026, 11, 15, 12)) == []
    assert await sent_kinds(repo) == [("submit", "2026-10:open"), ("submit", "2026-10:last")]
