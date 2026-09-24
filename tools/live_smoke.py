"""Живая проверка MAX Bot API тем же клиентом и теми же кнопками, что использует бот.

Запуск из корня репозитория (нужен доступ к platform-api2.max.ru, т. е. из РФ):
    python -m tools.live_smoke --dry-run      # показать запросы, без сети и без токена
    python -m tools.live_smoke                # /me, /subscriptions, /me/commands
    python -m tools.live_smoke --yes          # + тестовые сообщения на MAX_TEST_USER_ID
    python -m tools.live_smoke --yes --listen 120   # + ждать нажатий/контакта/фото (бот остановить!)
    python -m tools.live_smoke --check-init-data '<initData из мини-приложения>'

Настройки: переменные окружения и .env (окружение главнее). Токен не печатается.
Коды выхода: 0 — без FAIL, 1 — есть FAIL, 2 — ошибка настройки.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import itertools
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # запуск как `python tools/live_smoke.py`
    sys.path.insert(0, str(ROOT))

from app.bot import keyboards as K  # noqa: E402
from app.bot.events import parse_update  # noqa: E402
from app.bot.texts.fmt import esc  # noqa: E402
from app.config import Settings, load_settings  # noqa: E402
from app.integrations.max_api import MaxApi, MaxApiError  # noqa: E402
from app.main import COMMANDS  # noqa: E402
from app.web.auth import InitDataError, validate_init_data  # noqa: E402

MINIAPP_URL = "https://alpha7en.github.io/max-smart-city-webapp/"  # закреплён в MAX, не менять
FLOW = "smoke"  # payload тестовых callback-кнопок: для работающего бота это «устаревшая кнопка»
SAVE_UPDATES = ROOT / "data" / "live_updates.jsonl"  # data/ в .gitignore
HINTS = {
    "CERTIFICATE": "TLS: нет сертификата Минцифры? Проверьте app/integrations/certs/russian_trusted_ca.pem",
    "401": "токен неверный или отозван (business.max.ru → бот → токен)",
    "not.found": "open_app: web_app должен быть username бота, не URL",
    "proto.payload": "кнопка не прошла валидацию MAX (link — только публичный http(s)-URL)",
}


@dataclass
class Step:
    name: str
    status: str  # OK | FAIL | WARN | SKIP
    detail: str = ""


class Report:
    def __init__(self) -> None:
        self.steps: list[Step] = []

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.steps.append(Step(name, status, detail))
        print(f"{status:<4}  {name}" + (f" — {detail}" if detail else ""), flush=True)

    async def run(self, name: str, fn: Callable[[], Any]) -> Any:
        """Выполнить шаг: OK с деталями из результата или FAIL с кодом и текстом ошибки MAX."""
        try:
            res = await fn()
        except MaxApiError as e:
            self.add(name, "FAIL", explain(e))
            return None
        except ValueError as e:  # локальная валидация кнопок (app/bot/keyboards.py)
            self.add(name, "FAIL", f"локальная проверка: {e}")
            return None
        detail = res[1] if isinstance(res, tuple) else ""
        self.add(name, "OK", detail)
        return res[0] if isinstance(res, tuple) else res

    @property
    def failed(self) -> bool:
        return any(s.status == "FAIL" for s in self.steps)

    def table(self) -> str:
        rows = [("#", "Шаг", "Итог", "Детали")]
        rows += [(str(i), s.name, s.status, s.detail) for i, s in enumerate(self.steps, 1)]
        w = [max(len(r[c]) for r in rows) for c in range(3)]
        lines = [f"{r[0]:>{w[0]}}  {r[1]:<{w[1]}}  {r[2]:<{w[2]}}  {r[3]}".rstrip() for r in rows]
        lines.insert(1, "-" * min(120, max(len(x) for x in lines)))
        return "\n".join(lines)


def explain(e: MaxApiError) -> str:
    text = f"{e.status} {e.code}: {e.message}".strip()
    hints = [h for key, h in HINTS.items() if key in text]
    return text + (f" ({hints[0]})" if hints else "")


# --- Настройки ---

def read_env_file(path: Path) -> dict[str, str]:
    """Простой разбор .env: KEY=VALUE, комментарии, кавычки, `export`."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        env[key.strip()] = value
    return env


def load_env(path: Path) -> dict[str, str]:
    env = read_env_file(path)
    env.update(os.environ)  # окружение главнее файла (как в docker compose)
    return env


# --- Тестовые сообщения: по одному на каждый вид кнопок/разметки проекта ---

def exact(text: str, n: int) -> str:
    if len(text) != n:
        raise ValueError(f"label must be {n} chars: {text!r} ({len(text)})")
    return text


def test_messages(bot_username: str) -> list[tuple[str, str, Callable[[], dict | None]]]:
    """(шаг, текст, фабрика клавиатуры). Клавиатуры собираются через app/bot/keyboards.py."""
    def app_btn(text: str, payload: str | None = None) -> dict:
        btn = K.open_app(text, bot_username, payload)
        if btn is None:
            raise ValueError("нет username бота (BOT_USERNAME или GET /me) — open_app не собрать")
        return btn

    return [
        ("markdown + экранирование",
         "**Smoke 1.** Жирный и *курсив*.\nСимволы ниже должны быть видны как есть, без разметки:\n"
         + esc("a*b _c_ [x](y) `code` ~z~ \\ 5*3"),
         lambda: None),
        ("callback, 2 в ряд по 24",
         "Smoke 2. Две callback-кнопки в ряд по 24 символа. Видны ли подписи целиком?",
         lambda: K.kb([K.callback(exact("Подать показания (24 зн)", 24), FLOW, "a"),
                       K.callback(exact("Мои счётчики · 2 (24 зн)", 24), FLOW, "b")])),
        ("подписи 24/32/40, по одной",
         "Smoke 3. Предельные подписи: общая 24, срочная 32, счётчик/адрес 40.",
         lambda: K.kb(K.callback(exact("Подать показания (24 зн)", 24), FLOW, "c"),
                      K.callback(exact("Запишитесь на поверку: 5 (32 зн)", 32), FLOW, "d"),
                      K.callback(exact("Хол. вода · Арбат 47к1, кв 32 №2 (40 зн)", 40), FLOW, "e"))),
        ("request_contact + callback",
         "Smoke 4. Кнопка «Отправить мой номер» (как на шаге телефона регистрации).",
         lambda: K.kb(K.request_contact("Отправить мой номер"), K.callback("Назад", FLOW, "back"))),
        ("open_app с payload",
         "Smoke 5. Мини-приложение с payload smoke_1 (в мини-приложении это start_param).",
         lambda: K.kb(app_btn("Мини-приложение", "smoke_1"))),
        ("link (внешняя ссылка)",
         "Smoke 6. Кнопка-ссылка откроется в браузере, не внутри MAX.",
         lambda: K.kb(K.link("Открыть в браузере", MINIAPP_URL))),
        ("меню: callback + open_app",
         "Smoke 7. Форма главного меню: глобальные кнопки и мини-приложение.",
         lambda: K.kb(K.callback("Подать показания", FLOW, "m"),
                      [K.callback("Мои счётчики", FLOW, "m"), K.callback("Профиль", FLOW, "m")],
                      app_btn("Мини-приложение"))),
    ]


# --- Шаги ---

@dataclass
class Me:
    username: str
    bot_id: int | None


async def check_me(api: MaxApi, settings: Settings, report: Report) -> Me:
    me = await report.run("GET /me", lambda: _me(api))
    if me is None:
        return Me(settings.bot_username, None)
    username = me.get("username") or ""
    name = "BOT_USERNAME = username из /me"
    if settings.bot_username and username and settings.bot_username != username:
        report.add(name, "FAIL", f"в .env {settings.bot_username}, в MAX {username} — open_app уйдёт в 404")
    elif not settings.bot_username:
        report.add(name, "WARN", f"не задан, бот возьмёт {username} из /me")
    else:
        report.add(name, "OK", username)
    return Me(settings.bot_username or username, me.get("user_id"))


async def _me(api: MaxApi) -> tuple[dict, str]:
    me = await api.get_me()
    name = me.get("first_name") or me.get("name") or ""
    return me, f"id={me.get('user_id')} username={me.get('username')} name={name!r}"


async def check_subscriptions(api: MaxApi, report: Report) -> None:
    res = await report.run("GET /subscriptions", lambda: _subs(api))
    if res:
        urls = ", ".join(str(s.get("url")) for s in res)
        report.add("вебхуков нет (нужен polling)", "FAIL",
                   f"подписки: {urls} — long polling не получит апдейты; удалить: DELETE /subscriptions?url=…")
    elif res is not None:
        report.add("вебхуков нет (нужен polling)", "OK")


async def _subs(api: MaxApi) -> tuple[list, str]:
    res = await api._request("GET", "/subscriptions")  # в клиенте бота метода нет — он не нужен
    subs = res.get("subscriptions") or []
    return subs, f"{len(subs)} шт."


async def send_messages(api: MaxApi, user_id: int, bot_username: str, report: Report) -> list[str]:
    mids: list[str] = []
    chat_id: int | None = None
    for name, text, make_kb in test_messages(bot_username):
        async def step(text: str = text, make_kb: Callable[[], dict | None] = make_kb) -> tuple[dict, str]:
            msg = await api.send(text, user_id=user_id, keyboard=make_kb())
            return msg, f"mid={(msg.get('body') or {}).get('mid')}"
        msg = await report.run(f"POST /messages: {name}", step)
        if msg:
            mids.append((msg.get("body") or {}).get("mid") or "")
            chat_id = chat_id or (msg.get("recipient") or {}).get("chat_id")
    mids = [m for m in mids if m]
    if chat_id:
        await report.run("POST /chats/{id}/actions typing_on", lambda: api.typing(chat_id))
    else:
        report.add("POST /chats/{id}/actions typing_on", "SKIP", "нет chat_id из ответа /messages")
    if len(mids) >= 2:
        await report.run("PUT /messages: текст + новая клавиатура", lambda: api.edit(
            mids[0], "**Smoke 1 (изменено через PUT).** Текст заменён, добавлена кнопка.",
            K.kb(K.callback("Кнопка после правки", FLOW, "edited"))))
        await report.run("PUT /messages: только убрать кнопки", lambda: api.edit(mids[1], keyboard=None))
    else:
        report.add("PUT /messages", "SKIP", "не хватило отправленных сообщений")
    return mids


async def delete_messages(api: MaxApi, mids: list[str], report: Report) -> None:
    async def step() -> tuple[None, str]:
        errors = []
        for mid in mids:
            try:
                await api.delete(mid)
            except MaxApiError as e:
                errors.append(f"{mid}: {explain(e)}")
        if errors:
            raise MaxApiError(0, "delete", "; ".join(errors))
        return None, f"удалено {len(mids)}"
    await report.run("DELETE /messages (тестовые)", step)


# --- Режим прослушивания: проверка того, что приходит от живого клиента ---

async def listen(api: MaxApi, settings: Settings, user_id: int | None, bot_id: int | None,
                 seconds: int, report: Report) -> None:
    print(f"\nСлушаем апдейты {seconds} с. Бот должен быть остановлен (docker compose stop app) —"
          " иначе апдейты заберёт он.\nВ чате с ботом: нажмите callback-кнопку, «Отправить мой номер»,"
          " пришлите фото счётчика, при желании /start.\n", flush=True)
    SAVE_UPDATES.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + seconds
    marker: int | None = None
    seen = 0
    with SAVE_UPDATES.open("a", encoding="utf-8") as saved:
        while (left := int(deadline - time.monotonic())) > 0:
            try:
                res = await api.get_updates(marker, timeout=min(25, max(1, left)))
            except MaxApiError as e:
                report.add("GET /updates", "FAIL", explain(e))
                return
            if res.get("marker") is not None:
                marker = int(res["marker"])
            for upd in res.get("updates") or []:
                seen += 1
                saved.write(json.dumps(upd, ensure_ascii=False) + "\n")
                await inspect_update(api, settings, user_id, bot_id, upd, report)
    report.add("GET /updates (long polling)", "OK" if seen else "WARN",
               f"апдейтов: {seen}" + (f", сохранены в {SAVE_UPDATES.relative_to(ROOT)}" if seen else
                                      " — ничего не пришло за отведённое время"))


async def inspect_update(api: MaxApi, settings: Settings, user_id: int | None, bot_id: int | None,
                         upd: dict, report: Report) -> None:
    t = upd.get("update_type")
    ev = parse_update(upd)
    if ev is None:
        report.add(f"апдейт {t}", "WARN", "parse_update вернул None (не из диалога или неизвестный тип)")
        return
    if user_id and ev.user_id != user_id:
        report.add(f"апдейт {t}: user_id", "WARN", f"пришёл от {ev.user_id}, ждали {user_id}")
    if ev.kind == "callback":
        who = "FAIL" if ev.user_id == bot_id else "OK"  # нажавший — callback.user, не бот
        report.add("callback: нажавший = callback.user", who, f"user={ev.user_id} payload={ev.payload!r}")
        await report.run("POST /answers с пустым телом {}", lambda: api.answer(ev.callback_id or ""))
    elif ev.kind == "contact":
        vcf = ev.contact_vcf or ""
        has_tel = "TEL" in vcf.upper()
        report.add("contact: vcf_info с TEL", "OK" if has_tel and ev.contact_phone else "FAIL",
                   f"телефон {mask(ev.contact_phone)}" if ev.contact_phone else f"TEL в vcf: {has_tel}")
        report.add("contact: max_info.user_id = отправитель",
                   "OK" if ev.contact_owner_id == ev.user_id else "WARN",
                   f"max_info.user_id={ev.contact_owner_id}")
        expected = hmac.new(settings.bot_token.encode(), vcf.encode(), hashlib.sha256).hexdigest()
        match = bool(ev.contact_hash) and hmac.compare_digest(expected, ev.contact_hash or "")
        report.add("contact: hash = HMAC(token, vcf_info)", "OK" if match else "WARN",
                   "совпадает" if match else "не совпадает (проверка в боте мягкая, только лог)")
    elif ev.kind == "photo":
        types = [a.get("type") for a in (upd.get("message") or {}).get("body", {}).get("attachments") or []]
        report.add("фото: вложение распознано", "OK" if ev.photo_url else "FAIL",
                   f"типы={types}, фото={ev.photo_count}, url={'есть' if ev.photo_url else 'нет'}")
        if ev.photo_url:
            async def dl() -> tuple[None, str]:
                data, ctype = await api.download(ev.photo_url or "")
                return None, f"{len(data)} байт, {ctype or 'без content-type'}"
            await report.run("фото: скачивание по payload.url", dl)
    elif ev.kind == "start":
        report.add(f"{t}: start", "OK", f"payload={ev.start_payload!r}")
    else:
        report.add(f"{t}: {ev.kind}", "OK", f"текст {len(ev.text or '')} симв.")


def mask(phone: str | None) -> str:
    return (phone[:2] + "*" * (len(phone) - 4) + phone[-2:]) if phone and len(phone) > 4 else "—"


# --- initData мини-приложения ---

def check_init_data(raw: str, settings: Settings, report: Report) -> None:
    try:
        data = validate_init_data(raw.strip(), settings.bot_token, settings.init_data_ttl)
    except InitDataError as e:
        report.add("initData: подпись и срок", "FAIL", str(e))
        return
    age = int(time.time()) - data.auth_date
    report.add("initData: подпись и срок", "OK", f"user_id={data.max_user_id}, auth_date {age} с назад")
    report.add("initData: start_param", "OK" if data.start_param else "WARN",
               repr(data.start_param) if data.start_param else "нет (открыто не кнопкой с payload?)")


# --- Сборка ---

def dry_client(bot_username: str) -> httpx.AsyncClient:
    """httpx-клиент, который печатает запросы и отвечает правдоподобными заглушками."""
    counter = itertools.count(1)

    def handler(req: httpx.Request) -> httpx.Response:
        query = f"?{req.url.query.decode()}" if req.url.query else ""
        print(f"      → {req.method} {req.url.path}{query}")  # заголовки (токен) не печатаем
        if req.content:
            print(f"        {req.content.decode()[:600]}")
        path = req.url.path
        if path == "/me" and req.method == "GET":
            data: dict = {"user_id": 0, "username": bot_username or "your_bot", "first_name": "dry-run"}
        elif path == "/subscriptions":
            data = {"subscriptions": []}
        elif path == "/messages" and req.method == "POST":
            data = {"message": {"recipient": {"chat_id": 1}, "body": {"mid": f"mid.dry{next(counter)}"}}}
        else:
            data = {"success": True}
        return httpx.Response(200, json=data)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def confirm(user_id: int, count: int) -> bool:
    print(f"\nОтправим {count} тестовых сообщений пользователю MAX {user_id}"
          " (потом удалим, если нет --keep). Продолжить? [y/N] ", end="", flush=True)
    if not sys.stdin.isatty():
        print("\nНет терминала для подтверждения — добавьте --yes.")
        return False
    return input().strip().lower() in {"y", "yes", "д", "да"}


async def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Живая проверка MAX Bot API (см. .claude/skills/live-smoke).")
    p.add_argument("--dry-run", action="store_true", help="печатать запросы без отправки (сеть и токен не нужны)")
    p.add_argument("--yes", action="store_true", help="не спрашивать подтверждение перед отправкой")
    p.add_argument("--keep", action="store_true", help="не удалять тестовые сообщения")
    p.add_argument("--listen", type=int, default=0, metavar="SEC",
                   help="после отправки слушать апдейты SEC секунд (бот должен быть остановлен)")
    p.add_argument("--check-init-data", metavar="RAW", help="проверить строку initData из мини-приложения")
    p.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = p.parse_args(argv)

    env = load_env(args.env_file)
    settings = load_settings(env)
    report = Report()
    if args.dry_run:
        print("DRY-RUN: запросы только печатаются, ответы MAX — заглушки.")
    print(f"Настройки: {args.env_file if args.env_file.exists() else 'только окружение (нет .env)'}")

    if not settings.bot_token and not args.dry_run:
        print("BOT_TOKEN не задан. Скопируйте .env.example в .env и впишите токен"
              " (business.max.ru → бот → токен) или запустите с --dry-run.")
        return 2
    report.add("BOT_TOKEN", "OK" if settings.bot_token else "SKIP",
               "задан" if settings.bot_token else "не задан (dry-run)")

    if args.check_init_data:
        check_init_data(args.check_init_data, settings, report)
        print("\n" + report.table())
        return 1 if report.failed else 0

    raw_uid = env.get("MAX_TEST_USER_ID", "").strip()
    if raw_uid and not raw_uid.isdigit():
        print(f"MAX_TEST_USER_ID должен быть числом, сейчас {raw_uid!r}.")
        return 2
    user_id = int(raw_uid) if raw_uid else None
    if user_id is None and args.dry_run:
        user_id = 1
        print("MAX_TEST_USER_ID не задан — в dry-run показываем отправку на user_id=1.")

    client = dry_client(settings.bot_username) if args.dry_run else None
    api = MaxApi(settings.bot_token or "DRY-RUN", settings.max_api_base, client=client)
    mids: list[str] = []
    try:
        me = await check_me(api, settings, report)
        if me.bot_id is None:
            print("\nGET /me не прошёл — дальше проверять нечего (сеть, TLS или токен).")
            print("\n" + report.table())
            return 1
        await check_subscriptions(api, report)
        await report.run("PATCH /me/commands", lambda: api.set_commands(COMMANDS))

        if user_id is None:
            report.add("тестовые сообщения", "SKIP", "задайте MAX_TEST_USER_ID в .env (владелец: 5273381)")
        elif args.dry_run or args.yes or confirm(user_id, len(test_messages(me.username))):
            mids = await send_messages(api, user_id, me.username, report)
        else:
            report.add("тестовые сообщения", "SKIP", "отменено")

        if args.listen and args.dry_run:
            report.add("прослушивание апдейтов", "SKIP", "в dry-run не слушаем")
        elif args.listen:
            await listen(api, settings, user_id, me.bot_id, args.listen, report)

        if args.keep and mids:
            report.add("DELETE /messages (тестовые)", "SKIP", "--keep")
        elif mids:
            await delete_messages(api, mids, report)
    finally:
        await api.close()

    print("\n" + report.table())
    if not args.dry_run and user_id:
        print("\nПосмотрите сообщения в чате глазами: жирный/курсив без звёздочек, экранированные символы"
              " видны, подписи 24/32/40 не обрезаны, open_app открывает мини-приложение внутри MAX.")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
