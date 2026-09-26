"""ТОЛЬКО ДЛЯ ХАКАТОНА: тестовый профиль для проверяющих. Не часть основного функционала.

Команда /demo_profile работает только у пользователя без профиля (до регистрации или посреди неё):
создаёт вымышленный профиль целиком — случайные ФИО и телефон, 2 случайных адреса, по 2–3 случайных
счётчика с историей показаний за полгода (один — со сроком поверки меньше 30 дней, чтобы было видно срочное
действие) — и показывает меню-дашборд. Если профиль уже есть, бот объясняет, что сначала его нужно удалить
(«Профиль» → «Удалить мои данные»). Команда есть в списке команд бота в MAX (app/main.py: bot_commands).

Включается HACKATHON_DEMO_PROFILE (Settings.hackathon_demo_profile). Убрать после хакатона: этот модуль,
texts/hackathon_demo.py и yaml/hackathon_demo.yaml, импорт в flows/__init__.py, bot_commands в main.py,
repo.backdate_reading, переменную в config.py и .env.example, tests/test_hackathon_demo.py.

Адреса тестового профиля свои у каждого пользователя (norm_key 'hackathon-demo:<user_id>:<n>'): так
пользователь всегда их собственник, а двое проверяющих не делят один адрес по модели прав.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta

from app.bot import keyboards as K
from app.bot.ctx import Ctx
from app.bot.flows.menu import MENU, PROFILE, send_menu
from app.bot.router import call_hook, on_command
from app.bot.texts import common as C
from app.bot.texts import hackathon_demo as T
from app.bot.texts import menu as TM
from app.bot.texts.fmt import esc, plural
from app.domain.meters import current_period, shift_period
from app.integrations.address_service import AddressService

COMMAND = "/demo_profile"
HISTORY_MONTHS = 6    # показаний за прошлые месяцы у каждого счётчика
ADDRESSES = 2
URGENT_DAYS = (10, 28)  # срок поверки одного счётчика: кнопка «Запишитесь на поверку» (≤ 30 дней)

# --- Случайные данные (вымышленные) ---
MALE = ("Александр", "Дмитрий", "Максим", "Сергей", "Андрей", "Алексей", "Иван", "Михаил", "Николай", "Павел")
FEMALE = ("Анна", "Мария", "Елена", "Ольга", "Наталья", "Татьяна", "Ирина", "Екатерина", "Светлана", "Юлия")
SURNAMES = ("Иванов", "Смирнов", "Кузнецов", "Попов", "Соколов", "Лебедев", "Новиков", "Морозов", "Волков",
            "Орлов", "Павлов", "Семёнов", "Егоров", "Козлов", "Степанов", "Никитин")
FATHERS = ("Александров", "Дмитриев", "Сергеев", "Андреев", "Алексеев", "Иванов", "Михайлов", "Николаев",
           "Павлов", "Петров", "Викторов", "Олегов")   # + «ич» / «на»
CITIES = (
    ("г. Москва", ("ул. Тверская", "ул. Арбат", "Ленинский пр-кт", "ул. Профсоюзная", "ул. Новый Арбат",
                   "ул. Большая Полянка", "Кутузовский пр-кт", "ул. Маросейка")),
    ("г. Санкт-Петербург", ("Невский пр-кт", "ул. Садовая", "ул. Марата", "Литейный пр-кт", "ул. Рубинштейна")),
    ("г. Казань", ("ул. Баумана", "ул. Пушкина", "ул. Декабристов")),
    ("г. Екатеринбург", ("ул. Малышева", "ул. Ленина", "ул. Вайнера")),
    ("Московская обл., г. Истра", ("ул. Садовая", "ул. Советская", "ул. Ленина")),
)
# Тип → (показание полгода назад, расход в месяц) в тысячных долях; у света — по тарифам (день, ночь).
METERS = {
    "cold_water": ((50_000, 600_000), (3_000, 8_000)),
    "hot_water": ((30_000, 400_000), (2_000, 5_000)),
    "electricity": ((3_000_000, 30_000_000), (100_000, 300_000)),
    "gas": ((500_000, 5_000_000), (20_000, 60_000)),
}
_ADDRESSES = AddressService()  # локальный разбор без сети: адреса вымышленные, сверять не с чем


@dataclass(frozen=True)
class DemoMeter:
    type: str
    start: tuple[int, ...]         # показание полгода назад по тарифам, в тысячных долях
    monthly: tuple[int, ...]       # средний расход за месяц по тарифам, в тысячных долях
    verification_days: int         # через сколько дней срок поверки
    this_month: bool               # подано ли уже за текущий месяц


@dataclass(frozen=True)
class DemoProfile:
    full_name: str
    phone: str
    addresses: tuple[tuple[str, tuple[DemoMeter, ...]], ...]

    @property
    def meters(self) -> int:
        return sum(len(ms) for _, ms in self.addresses)


def enabled(ctx: Ctx) -> bool:
    return ctx.settings.hackathon_demo_profile


def _norm_key(user_id: int, n: int) -> str:
    return f"hackathon-demo:{user_id}:{n}"


def random_name(rnd: random.Random) -> str:
    female = rnd.random() < 0.5
    first = rnd.choice(FEMALE if female else MALE)
    surname = rnd.choice(SURNAMES) + ("а" if female else "")
    return f"{surname} {first} {rnd.choice(FATHERS)}{'на' if female else 'ич'}"


def random_addresses(rnd: random.Random, n: int = ADDRESSES) -> list[str]:
    """n разных адресов в формате «г. Москва, ул. Арбат, д. 47, кв. 32»."""
    streets = [(city, street) for city, sts in CITIES for street in sts]
    return [f"{city}, {street}, д. {rnd.randint(1, 120)}, кв. {rnd.randint(1, 300)}"
            for city, street in rnd.sample(streets, n)]


def _meter(rnd: random.Random, type: str, verification_days: int) -> DemoMeter:
    (lo, hi), (mlo, mhi) = METERS[type]
    tariffs = 2 if type == "electricity" and rnd.random() < 0.5 else 1
    start = tuple(rnd.randint(lo, hi) // (i + 1) for i in range(tariffs))    # ночной тариф меньше дневного
    monthly = tuple(rnd.randint(mlo, mhi) // (i + 1) for i in range(tariffs))
    return DemoMeter(type, start, monthly, verification_days, this_month=rnd.random() < 0.4)


def random_profile(rnd: random.Random) -> DemoProfile:
    """Случайный профиль; у первого счётчика первого адреса — скорый срок поверки."""
    addresses = []
    for n, text in enumerate(random_addresses(rnd)):
        types = rnd.sample(sorted(METERS), rnd.randint(2, 3))
        meters = tuple(
            _meter(rnd, t, rnd.randint(*URGENT_DAYS) if n == 0 and i == 0 else rnd.randint(365, 6 * 365))
            for i, t in enumerate(types)
        )
        addresses.append((text, meters))
    phone = "+79" + "".join(str(rnd.randint(0, 9)) for _ in range(9))
    return DemoProfile(random_name(rnd), phone, tuple(addresses))


async def create_profile(ctx: Ctx, profile: DemoProfile) -> None:
    """Регистрация, адреса, счётчики и история показаний — одной транзакцией."""
    repo, uid, today = ctx.repo, ctx.user["id"], ctx.now.date()
    period = current_period(today)
    rnd = random.Random(uid)
    async with repo.tx():
        for n, (text, meters) in enumerate(profile.addresses):
            (c,) = _ADDRESSES.parse_local(text)
            if n == 0:
                res = await repo.complete_registration(
                    uid, full_name=profile.full_name, phone=profile.phone, phone_verified=False,
                    address=c.to_dict(), norm_key=_norm_key(uid, n), raw_input=None, now=ctx.now)
            else:
                res = await repo.add_user_address(uid, c.to_dict(), _norm_key(uid, n), None, today)
            for m in meters:
                due = (ctx.now + timedelta(days=m.verification_days)).date().isoformat()
                mid = await repo.create_meter(res["address_id"], m.type, len(m.start), None, uid, due, "user")
                await _history(ctx, rnd, mid, m, period)
    ctx.user = await repo.get_user(ctx.event.user_id)  # теперь зарегистрирован: меню покажет дашборд


async def _history(ctx: Ctx, rnd: random.Random, meter_id: int, m: DemoMeter, period: str) -> None:
    values = list(m.start)
    for shift in range(-HISTORY_MONTHS, 1 if m.this_month else 0):
        p = shift_period(period, shift)
        rid = await ctx.repo.add_reading(meter_id, ctx.user["id"], p, {f"t{i + 1}": v for i, v in enumerate(values)},
                                         "photo")
        if shift:  # прошлые месяцы — будто подавали 20-го числа; текущий — сейчас
            y, mon = map(int, p.split("-"))
            await ctx.repo.backdate_reading(rid, ctx.now.replace(year=y, month=mon, day=20, hour=10, minute=0))
        values = [v + inc * rnd.randint(80, 120) // 100 for v, inc in zip(values, m.monthly, strict=True)]


@on_command(COMMAND, unregistered=True)
async def command(ctx: Ctx) -> None:
    if not enabled(ctx):
        if ctx.registered:
            await send_menu(ctx)
        else:
            await call_hook("registration.begin", ctx)
        return
    if ctx.registered:
        await ctx.reply(T.HAS_PROFILE, K.kb([K.gbtn(TM.BTN_PROFILE, PROFILE), K.gbtn(C.BTN_MENU, MENU)]))
        return
    profile = random_profile(random.Random())
    await create_profile(ctx, profile)
    a, m = len(profile.addresses), profile.meters
    await send_menu(ctx, header=T.CREATED.format(
        name=esc(profile.full_name),
        addresses=f"{a} {plural(a, 'адрес', 'адреса', 'адресов')}",
        meters=f"{m} {plural(m, 'счётчик', 'счётчика', 'счётчиков')}",
    ))
