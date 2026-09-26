"""ТОЛЬКО ДЛЯ ХАКАТОНА: демонстрационный профиль для проверяющих. Не часть основного функционала.

После регистрации (registration.finish) бот спрашивает «Сгенерировать демонстрационный профиль?».
«Да» добавляет пользователю вымышленные адреса со счётчиками и историей показаний за полгода, чтобы
жюри сразу увидело заполненный дашборд, «Мои счётчики», срочную поверку, демо-счета и мини-приложение,
не вводя всё руками. Команда /demo_profile делает то же для уже зарегистрированных.

Включается HACKATHON_DEMO_PROFILE (Settings.hackathon_demo_profile). Убрать после хакатона: этот модуль,
texts/hackathon_demo.py и yaml/hackathon_demo.yaml, импорт в flows/__init__.py, вызов offer() в
registration.finish, repo.backdate_reading, переменную в config.py и .env.example, tests/test_hackathon_demo.py.

Адреса демо-профиля свои у каждого пользователя (norm_key 'hackathon-demo:<user_id>:<n>'): так
пользователь всегда их собственник, а двое проверяющих не делят один адрес по модели прав.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta

from app.bot import keyboards as K
from app.bot.ctx import Ctx
from app.bot.flows.menu import MENU, send_menu
from app.bot.router import on_command, on_global
from app.bot.texts import hackathon_demo as T
from app.bot.texts.fmt import plural
from app.domain.meters import current_period, shift_period
from app.integrations.address_service import AddressService

DEMO_PROFILE = "hackathon_demo"   # глобальная кнопка g|hackathon_demo
HISTORY_MONTHS = 6                # показаний за прошлые месяцы у каждого счётчика


@dataclass(frozen=True)
class DemoMeter:
    type: str
    start: tuple[int, ...]        # показание полгода назад по тарифам, в тысячных долях
    monthly: tuple[int, ...]      # средний расход за месяц, в тысячных долях
    verification_days: int        # через сколько дней срок поверки (≤ 60 — срочное в дашборде)
    this_month: bool = False      # подано ли уже за текущий месяц


@dataclass(frozen=True)
class DemoAddress:
    text: str
    meters: tuple[DemoMeter, ...]


DEMO_ADDRESSES = (
    DemoAddress("г. Москва, ул. Тверская, д. 12, кв. 45", (
        DemoMeter("cold_water", (412_350,), (5_400,), 3 * 365),
        DemoMeter("hot_water", (238_120,), (3_600,), 25),  # скоро поверка — видно срочное действие
        DemoMeter("electricity", (12_450_300, 5_120_750), (180_000, 95_000), 8 * 365),
    )),
    DemoAddress("Московская обл., г. Истра, ул. Садовая, д. 3", (
        DemoMeter("electricity", (8_730_400,), (240_000,), 5 * 365, this_month=True),
        DemoMeter("gas", (1_520_340,), (38_000,), 6 * 365, this_month=True),
    )),
)
_ADDRESSES = AddressService()  # локальный разбор без сети: адреса вымышленные, сверять не с чем


def enabled(ctx: Ctx) -> bool:
    return ctx.settings.hackathon_demo_profile


def _norm_key(user_id: int, n: int) -> str:
    return f"hackathon-demo:{user_id}:{n}"


async def offer(ctx: Ctx) -> None:
    """Вопрос после регистрации вместо меню (строки ctx.note уходят в это же сообщение)."""
    await ctx.reply(T.OFFER, K.kb(K.gbtn(T.BTN_YES, DEMO_PROFILE), K.gbtn(T.BTN_NO, MENU)))


async def generate(ctx: Ctx) -> tuple[int, int] | None:
    """Адреса, счётчики и история показаний одной транзакцией. → (адресов, счётчиков); уже есть → None."""
    repo, uid = ctx.repo, ctx.user["id"]
    if await repo.find_address(_norm_key(uid, 0)):
        return None
    rnd = random.Random(uid)  # у каждого пользователя свои, но повторяемые цифры
    period = current_period(ctx.now.date())
    meters = 0
    async with repo.tx():
        for n, addr in enumerate(DEMO_ADDRESSES):
            (c,) = _ADDRESSES.parse_local(addr.text)
            res = await repo.add_user_address(uid, c.to_dict(), _norm_key(uid, n), None, ctx.now.date())
            for m in addr.meters:
                due = (ctx.now + timedelta(days=m.verification_days)).date().isoformat()
                mid = await repo.create_meter(res["address_id"], m.type, len(m.start), None, uid, due, "user")
                await _history(ctx, rnd, mid, m, period)
                meters += 1
    return len(DEMO_ADDRESSES), meters


async def _history(ctx: Ctx, rnd: random.Random, meter_id: int, m: DemoMeter, period: str) -> None:
    values = list(m.start)
    months = range(-HISTORY_MONTHS, 1 if m.this_month else 0)
    for shift in months:
        p = shift_period(period, shift)
        vals = {f"t{i + 1}": v for i, v in enumerate(values)}
        rid = await ctx.repo.add_reading(meter_id, ctx.user["id"], p, vals, "photo")
        if shift:  # прошлые месяцы — будто подавали 20-го числа; текущий — сейчас
            y, mon = map(int, p.split("-"))
            await ctx.repo.backdate_reading(rid, ctx.now.replace(year=y, month=mon, day=20, hour=10, minute=0))
        values = [v + inc * rnd.randint(80, 120) // 100 for v, inc in zip(values, m.monthly, strict=True)]


@on_global(DEMO_PROFILE)
async def create(ctx: Ctx) -> None:
    """«Да, сгенерировать»: профиль и меню-дашборд с итогом над ним."""
    if not enabled(ctx):
        await send_menu(ctx)
        return
    counts = await generate(ctx)
    if counts is None:
        await send_menu(ctx, header=T.ALREADY)
        return
    a, m = counts
    await send_menu(ctx, header=T.CREATED.format(
        addresses=f"{a} {plural(a, 'адрес', 'адреса', 'адресов')}",
        meters=f"{m} {plural(m, 'счётчик', 'счётчика', 'счётчиков')}",
    ))


@on_command("/demo_profile")
async def command(ctx: Ctx) -> None:
    if not enabled(ctx):
        await send_menu(ctx)
        return
    await offer(ctx)

