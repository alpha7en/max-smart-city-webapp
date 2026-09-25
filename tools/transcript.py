"""Пример диалога с ботом: настоящий роутер + FakeMaxApi, печатает markdown.

    python -m tools.transcript > docs/DIALOG_EXAMPLE.md

Сеть и токен не нужны: MAX подменён FakeMaxApi из tests/fakes.py, адреса разбираются локально
(без DaData), распознавание — демо-заглушка, ФГИС «Аршин» — демо-данные (ARSHIN_MODE=fixtures). Время заморожено: 19.10.2026 12:00 МСК (окно подачи открыто).
"""
from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime
from pathlib import Path

from app import clock
from app.bot.ctx import Deps
from app.bot.flows import invite
from app.bot.events import parse_update
from app.bot.router import Router
from app.config import Settings
from app.integrations.arshin import ArshinClient
from app.integrations.recognizer import StubRecognizer
from app.repo import Repo
from tests import fakes

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=clock.TZ)
BOT = "t226_hakaton_max_bot"
ANNA, OLEG, MARIA = 5273381, 5273382, 5273383
# uid → (имя, фамилия в профиле MAX, «кому» в подписи сообщения)
PEOPLE = {ANNA: ("Анна", "Иванова", "Анне"), OLEG: ("Олег", "Петров", "Олегу"),
          MARIA: ("Мария", "Смирнова", "Марии")}
INVITE_TOKEN = "k3v9q2m7x4t8w1z6p5r0"  # в боте токен случайный; здесь постоянный, чтобы пример не менялся
ADDRESS = "Москва, Арбат 47к1, кв 32"


class Dialog:
    """Пишет в stdout реплики пользователя и всё, что бот отправил в ответ."""

    def __init__(self, router: Router, api: fakes.FakeMaxApi):
        self.router, self.api, self.pos = router, api, 0
        self.speaker, self._photo = ANNA, 0

    async def _feed(self, uid: int, update: dict, said: str) -> None:
        self.speaker = uid
        first, last, _ = PEOPLE[uid]
        who = (update["callback"]["user"] if "callback" in update else
               update["user"] if "user" in update else update["message"]["sender"])
        who.update(first_name=first, last_name=last)  # имя профиля MAX (для кнопки «Это я: …»)
        print(f"**{first}:** {said}\n")
        await self.router.handle(parse_update(update))
        self._show()

    def _show(self) -> None:
        for name, kw in self.api.calls[self.pos:]:
            if name == "answer" and kw.get("notification"):
                print(f"_Всплывающее уведомление: {kw['notification']}_\n")
                continue
            if name == "send":
                text, kb = kw["text"], kw["keyboard"]
                to = "" if kw["user_id"] == self.speaker else f" → {PEOPLE[kw['user_id']][2]}"
            elif name == "answer" and kw.get("message") is not None:
                atts = kw["message"].get("attachments") or []
                text, kb, to = kw["message"].get("text"), (atts[0] if atts else None), ""
            else:
                continue
            quoted = "\n".join(">" + (" " + line if line else "") for line in (text or "").split("\n"))
            print(f"**Бот{to}:**\n\n{quoted}")
            rows = (kb or {}).get("payload", {}).get("buttons", [])
            if rows:
                print(">\n> " + "<br>".join(" ".join(f"`[{b['text']}]`" for b in row) for row in rows))
            print()
        self.pos = len(self.api.calls)

    async def text(self, uid: int, text: str) -> None:
        await self._feed(uid, fakes.message_created(uid, text), text)

    async def press(self, uid: int, button: str) -> None:
        payload = self.api.button(button)["payload"]
        await self._feed(uid, fakes.message_callback(uid, payload), f"нажимает `[{button}]`")

    async def open_link(self, uid: int, payload: str) -> None:
        await self._feed(uid, fakes.bot_started(uid, payload), f"открывает ссылку-приглашение (`start={payload}`)")

    async def contact(self, uid: int) -> None:
        phone = "79123456789" if uid == ANNA else "79161234567"
        await self._feed(uid, fakes.message_created(uid, None, [fakes.contact(uid, phone)]),
                         "нажимает `[Отправить мой номер]` и подтверждает в MAX")

    async def photo(self, uid: int, caption: str | None = None) -> None:
        self._photo += 1
        url = f"https://i.oneme.ru/i?r=meter{self._photo}"
        self.api.files[url] = f"meter-photo-{self._photo}".encode()
        said = "присылает фото счётчика" + (f" с подписью «{caption}»" if caption else "")
        await self._feed(uid, fakes.message_created(uid, caption, [fakes.image(url)]), said)


def section(title: str, note: str = "") -> None:
    print(f"## {title}\n")
    if note:
        print(f"{note}\n")


async def main() -> None:
    clock.set_now(NOW)
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(bot_token="demo", bot_username=BOT, data_dir=Path(tmp), arshin_mode="fixtures")
        repo = await Repo.open(settings.db_path)
        api = fakes.FakeMaxApi()
        arshin = ArshinClient("fixtures", repo=repo)
        router = Router(Deps(api=api, repo=repo, settings=settings, recognizer=StubRecognizer(), bot_username=BOT,
                             arshin=arshin))
        d = Dialog(router, api)
        invite.new_token = lambda: INVITE_TOKEN
        try:
            await run(d)
        finally:
            await repo.close()
            clock.set_now(None)


async def run(d: Dialog) -> None:
    print("# Пример диалога с ботом\n")
    print("Сгенерировано командой `python -m tools.transcript > docs/DIALOG_EXAMPLE.md`: сообщения прошли "
          "через настоящий роутер бота, вместо MAX — тестовый двойник. Дата — 19 октября 2026, окно подачи "
          "открыто. Без ключа DaData адрес разбирается локально, без `RECOGNIZER_URL` цифры подставляет "
          "демо-распознавание, а срок поверки берётся из демо-данных ФГИС «Аршин» (ARSHIN_MODE=fixtures; "
          "номер на «0» — записи нет). Кнопки показаны как `[Текст]`, ряды разделены переносом.\n")

    section("1. Регистрация и подача показаний по фото")
    await d.text(ANNA, "привет")
    await d.text(ANNA, "иванова анна сергеевна")
    await d.contact(ANNA)
    await d.text(ANNA, ADDRESS)
    await d.press(ANNA, "Да")
    await d.press(ANNA, "Всё верно")
    await d.photo(ANNA)
    await d.press(ANNA, "Хол. вода")
    await d.press(ANNA, "Арбат 47к1, кв 32")
    await d.press(ANNA, "Ввести номер")  # демо-распознавание номер не читает
    await d.text(ANNA, "18-452178")
    await d.press(ANNA, "Отправить")

    section("2. Ручной ввод", "Новый счётчик без фото: тип, адрес, показание. Номер на «0» — в демо-данных "
            "ФГИС записи нет, бот спрашивает дату из паспорта.")
    await d.press(ANNA, "Подать ещё")
    await d.press(ANNA, "Ввести вручную")
    await d.press(ANNA, "Новый счётчик")
    await d.press(ANNA, "Гор. вода")
    await d.press(ANNA, "Арбат 47к1, кв 32")
    await d.text(ANNA, "12,34,5")
    await d.text(ANNA, "45,678")
    await d.text(ANNA, "18-452190")
    await d.press(ANNA, "Отправить")
    await d.press(ANNA, "Позже")

    section("3. Цифры не распознаны: ручной ввод и замена показания",
            "Демо-распознавание возвращает ошибку, если в подписи к фото есть слово «ошибка». "
            "За октябрь по этому счётчику уже подавали, поэтому бот предлагает заменить показание.")
    await d.photo(ANNA, "ошибка")
    await d.press(ANNA, "Хол. вода · Арбат 47к1, кв 32")
    await d.press(ANNA, "Ввести вручную")
    await d.text(ANNA, "105,1")
    await d.press(ANNA, "Отправить")
    await d.press(ANNA, "Заменить")

    section("4. Второй жилец по тому же адресу: нет прав",
            "Модель прав: первый зарегистрированный по адресу — собственник, следующие ждут его разрешения. "
            "При DEMO_MODE=true есть ещё «Открыть доступ (демо)» — одобряем за собственника; здесь показан "
            "обычный путь через запрос.")
    await d.text(OLEG, "здравствуйте")
    await d.text(OLEG, "Петров Олег Иванович")
    await d.contact(OLEG)
    await d.text(OLEG, ADDRESS)
    await d.press(OLEG, "Да")
    await d.press(OLEG, "Всё верно")
    await d.press(OLEG, "Запросить доступ")
    await d.press(ANNA, "Разрешить")

    section("5. Уведомления: /demo")
    await d.text(ANNA, "/demo")
    await d.press(ANNA, "О поверке")
    await d.press(ANNA, "О счёте")
    await d.press(ANNA, "Оплатить")
    await d.text(ANNA, "/demo")
    await d.press(ANNA, "Поверка в ФГИС")

    section("6. Приглашение жильца и отзыв доступа",
            "Собственник создаёт одноразовую ссылку на 7 дней и пересылает её. Новый человек открывает её, "
            "регистрируется, выбирает адрес из приглашения — и сразу получает доступ. Отзыв — с подтверждением.")
    await d.press(ANNA, "В меню")
    await d.press(ANNA, "Профиль")
    await d.press(ANNA, "Пригласить жильца")
    await d.open_link(MARIA, "inv_" + INVITE_TOKEN)
    await d.text(MARIA, "Смирнова Мария Павловна")
    await d.text(MARIA, "+7 903 222-33-44")
    await d.press(MARIA, "Да, этот")
    await d.press(MARIA, "Всё верно")
    await d.press(ANNA, "Управлять доступом")
    await d.press(ANNA, "Отозвать: Мария С.")
    await d.press(ANNA, "Отозвать")


if __name__ == "__main__":
    asyncio.run(main())
