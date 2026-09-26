"""Уведомления и /demo: N1 окно подачи, N2 поверка, N3 счёт, N4 устаревшие кнопки, N5 /demo, N6 рассылка."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

from app import clock
from app.bot.ctx import Deps
from app.bot.router import Router
from app.bot.texts import common as C
from app.bot.texts import menu as MT
from app.bot.texts import notify as T
from app.integrations.max_api import MaxApiError
from app.main import COMMANDS
from app.scheduler import notify_tick
from tests import fakes
from tests.conftest import Chat
from tests.test_menu import UID, add_meter, hooks, labels, make_user, rows  # noqa: F401 — hooks — фикстура


def at(y: int, m: int, d: int, h: int = 12) -> datetime:
    return datetime(y, m, d, h, 0, tzinfo=clock.TZ)


async def tick(deps: Deps, when: datetime) -> list[tuple[str, dict]]:
    """Тик планировщика в момент when → [(текст, клавиатура)] отправленных уведомлений."""
    clock.set_now(when)
    deps.api.clear()
    await notify_tick(deps, when)
    return [(kw["text"], kw["keyboard"]) for kw in deps.api.named("send")]


async def sent_kinds(repo) -> list[tuple[str, str]]:
    return [(r["kind"], r["dedup_key"]) for r in await repo._all("SELECT * FROM notifications ORDER BY id")]


# --- N1: окно подачи ---

async def test_submission_window_notifications(deps, repo, api):
    user, aid = await make_user(repo, now=at(2026, 10, 14))  # счёт до 10 ноября — ещё не срочно
    mid = await add_meter(repo, user, aid)
    assert await tick(deps, at(2026, 10, 14, 12)) == []  # окно ещё закрыто
    ((text, kb),) = await tick(deps, at(2026, 10, 15, 10))
    assert text.startswith(T.SUBMIT_OPEN.format(month="октябрь", deadline="25 октября"))
    assert T.SUBMIT_NOT_DONE.format(meters="Хол. вода · Арбат 47к1, кв 32") in text
    assert labels(kb) == [[T.BTN_SUBMIT], [C.BTN_MENU]]
    assert [b[0]["payload"] for b in rows(kb)] == ["g|n_submit|", "g|menu|"]
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


# --- N2: поверка 30/7/1/просрочка ---

async def test_verification_notifications(deps, repo, api):
    user, aid = await make_user(repo)
    due = date(2026, 12, 20)
    mid = await add_meter(repo, user, aid, period="2026-10", verif=due)
    await repo.add_reading(mid, user["id"], "2026-11", {"t1": 124000}, "manual")  # подано — окно не мешает
    bill = (await repo.unpaid_bills(user["id"]))[0]
    await repo.set_bill_status(bill["id"], "paid")

    assert await tick(deps, at(2026, 11, 19)) == []  # 31 день
    ((text, kb),) = await tick(deps, at(2026, 11, 20))
    assert text.startswith(T.VERIFICATION_SOON.format(meter="холодной воды (Арбат 47к1, кв 32)", date="20 декабря",
                                                      left="осталось **30 дней**"))
    assert T.VERIFICATION_WHY in text and "смоделирована" not in text
    assert labels(kb) == [[T.BTN_VERIFY], [C.BTN_MENU]]
    assert rows(kb)[0][0]["payload"] == f"g|verify|{mid}"
    assert await tick(deps, at(2026, 11, 25)) == []
    await repo.add_reading(mid, user["id"], "2026-12", {"t1": 125000}, "manual")
    ((text, _),) = await tick(deps, at(2026, 12, 13))
    assert "осталось **7 дней**" in text
    ((text, _),) = await tick(deps, at(2026, 12, 19))
    assert "остался **1 день**" in text
    assert await tick(deps, at(2026, 12, 20)) == []  # тот же этап «1»
    ((text, _),) = await tick(deps, at(2026, 12, 21))
    assert text.startswith(T.VERIFICATION_OVERDUE.format(meter="холодной воды (Арбат 47к1, кв 32)", date="20 декабря"))
    assert await tick(deps, at(2026, 12, 28)) == []
    stages = [k.split(":")[-1] for kind, k in await sent_kinds(repo) if kind == "verification"]
    assert stages == ["30", "7", "1", "overdue"]


# --- N3: счёт 5/1 ---

async def test_bill_notifications(deps, repo, api):
    user, aid = await make_user(repo)  # демо-счёт за сентябрь до 10 ноября
    mid = await add_meter(repo, user, aid)
    await repo.add_reading(mid, user["id"], "2026-11", {"t1": 1000}, "manual")
    (bill,) = await repo.unpaid_bills(user["id"])
    assert await tick(deps, at(2026, 11, 4)) == []
    ((text, kb),) = await tick(deps, at(2026, 11, 5))
    assert text.startswith(T.BILL_DUE.format(month="сентябрь", address="Арбат 47к1, кв 32",
                                             amount="5 918 ₽", date="10 ноября", left="осталось **5 дней**"))
    assert T.BILL_DEMO in text
    assert labels(kb) == [[T.BTN_PAY], [C.BTN_MENU]] and rows(kb)[0][0]["payload"] == f"g|pay|{bill['id']}"
    assert await tick(deps, at(2026, 11, 6)) == []
    ((text, _),) = await tick(deps, at(2026, 11, 9))
    assert "остался **1 день**" in text
    assert await tick(deps, at(2026, 11, 11)) == []  # просрочку по счёту не шлём
    await repo.set_bill_status(bill["id"], "paid")
    assert await tick(deps, at(2026, 11, 10)) == []


# --- N4: кнопки уведомлений, когда действие уже выполнено ---

async def test_notice_buttons_when_already_done(chat, api, repo, hooks):  # noqa: F811
    user, aid = await make_user(repo)
    mid = await add_meter(repo, user, aid)
    await chat.payload("g|n_submit|")  # не подано → начинаем подачу
    assert hooks == ["submission.start"]

    await repo.add_reading(mid, user["id"], "2026-10", {"t1": 1000}, "manual")
    api.clear()
    await chat.payload("g|n_submit|", mid="mid.notice")
    assert api.named("answer")[0]["message"] is None  # уведомление не затираем
    (sent,) = api.named("send")
    assert sent["text"].startswith(T.ALREADY_SUBMITTED.format(month="октябрь"))
    assert sent["text"].endswith(f"{MT.FOOTER}\n\n> {MT.BILL_NOTE}") and hooks == ["submission.start"]

    (bill,) = await repo.unpaid_bills(user["id"])
    await repo.set_bill_status(bill["id"], "paid")
    await chat.payload(f"g|pay|{bill['id']}")
    assert api.last_text().startswith(T.ALREADY_PAID + "\n\n")

    await repo.set_verification(mid, "2030-03-15", "user")
    await chat.payload(f"g|verify|{mid}")
    assert api.last_text().startswith(T.VERIFICATION_UPDATED + "\n\n")
    await chat.payload("g|verify|999")
    assert api.last_text().startswith(T.METER_GONE)


# --- N5: /demo ---

async def test_demo_notifications(chat, api, repo):
    user, aid = await make_user(repo)
    await chat.text("/demo")
    text, kb = api.outgoing()[-1]
    assert text == T.DEMO and labels(kb) == [[T.BTN_DEMO_SUBMIT, T.BTN_DEMO_VERIFY, T.BTN_DEMO_BILL], [C.BTN_MENU]]

    await chat.press(T.BTN_DEMO_VERIFY)  # счётчиков нет
    text, kb = api.outgoing()[-1]
    assert text == T.DEMO_ADD_METER_FIRST and labels(kb) == [[T.BTN_SUBMIT], [C.BTN_MENU]]

    mid = await add_meter(repo, user, aid)
    await chat.press(T.BTN_DEMO_VERIFY)
    text, kb = api.outgoing()[-1]
    assert "до **8 ноября**, осталось **20 дней**" in text and text.endswith(f"> {T.VERIFICATION_MODEL}")
    assert rows(kb)[0][0]["payload"] == f"g|verify|{mid}"
    meter = await repo.get_meter(mid)
    assert (meter["verification_due"], meter["verification_source"]) == ("2026-11-08", "model")

    await chat.press(T.BTN_DEMO_SUBMIT)
    assert api.last_text().startswith(T.SUBMIT_OPEN.format(month="октябрь", deadline="25 октября"))
    await chat.press(T.BTN_DEMO_BILL)
    assert "5 918 ₽" in api.last_text() and T.BILL_DEMO in api.last_text()
    assert await sent_kinds(repo) == []  # демо не пишет дедуп

    await chat.text("/start")  # срочная кнопка после демо-поверки
    assert labels(api.outgoing()[-1][1])[:2] == [[MT.BTN_PAY.format(amount="5 918 ₽")],
                                                 [MT.URGENT_VERIFICATION.format(n="20 дней")]]


async def test_demo_keeps_user_verification_date_and_disabled(chat, api, repo, deps):
    user, aid = await make_user(repo)
    mid = await add_meter(repo, user, aid, verif=date(2027, 1, 10))
    await chat.payload("g|demo|verify")
    assert (await repo.get_meter(mid))["verification_due"] == "2027-01-10"
    assert "до **10 января**" in api.last_text()

    off = Chat(Router(replace(deps, settings=replace(deps.settings, demo_mode=False))), api)
    await off.text("/demo")
    assert api.last_text().startswith(C.UNKNOWN_COMMAND)
    assert ("start", "Главное меню") in COMMANDS and ("demo", "Примеры уведомлений") in COMMANDS


# --- N6: кому и как рассылаем ---

class FlakyApi(fakes.FakeMaxApi):
    def __init__(self, fail: dict[int, int]):
        super().__init__()
        self.fail = fail  # max_user_id → HTTP-статус ошибки

    async def send(self, text, *, user_id=None, **kw):
        if user_id in self.fail:
            raise MaxApiError(self.fail[user_id], "err", "boom")
        return await super().send(text, user_id=user_id, **kw)


async def test_broadcast_skips_unregistered_and_survives_errors(deps, repo):
    api = FlakyApi({1: 500, 2: 403})
    deps = replace(deps, api=api)
    for uid, key in ((1, "k1"), (2, "k2"), (3, "k3")):
        user, aid = await make_user(repo, uid, norm_key=key)
        await add_meter(repo, user, aid)
    await repo.ensure_user(4, fakes.chat_of(4))  # не зарегистрирован
    sent = await tick(deps, at(2026, 10, 19))
    assert len(sent) == 1 and api.named("send")[0]["user_id"] == 3
    marked = {r["user_id"] for r in await repo._all("SELECT user_id FROM notifications")}
    ids = {u["max_user_id"]: u["id"] for u in await repo.registered_users()}
    assert marked == {ids[2], ids[3]}  # 5xx — повторим позже, 4xx — не долбим
    api.fail = {}
    assert [kw["user_id"] for kw in api.named("send")] == [3]
    await tick(deps, at(2026, 10, 19, 13))
    assert [kw["user_id"] for kw in api.named("send")] == [1]
