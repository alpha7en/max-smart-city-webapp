---
name: live-smoke
description: Живая проверка MAX Bot API этого бота через tools/live_smoke.py. Проверяет токен, /me, вебхуки, команды и все виды кнопок и разметки, которые шлёт проект; по ошибкам находит место в коде, чинит, прогоняет pytest. Вызывать на фразы «проверь бота живьём», «smoke», «проверь токен/кнопки в MAX», «почему бот молчит», «работает ли API MAX».
---

# live-smoke: проверь бота живьём

Живой MAX доступен только отсюда, с машины владельца в РФ: облачные агенты до platform-api2.max.ru не достают.
Правила: токен не печатать и не показывать (`cat .env` запрещён), TLS не отключать, URL мини-приложения
не трогать, вебхуки без явного согласия владельца не создавать и не удалять.

## 1. Окружение
```bash
test -f .env && echo ".env есть" || cp .env.example .env    # потом попросить владельца вписать BOT_TOKEN
grep -c '^BOT_TOKEN=..*' .env        # 1 = токен задан (значение не выводим)
grep -E '^(BOT_USERNAME|MAX_TEST_USER_ID|MAX_API_BASE)=' .env
```
- Если нет `MAX_TEST_USER_ID`, добавь в конец .env `MAX_TEST_USER_ID=5273381` (это тестовый аккаунт владельца, не секрет).
- Python: если нет `.venv`, выполни `python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt`.
  Без Python можно в docker: `docker compose run --rm --no-deps -v "$PWD/tools:/app/tools:ro" app python -m tools.live_smoke --yes`.
- Если MAX не открывается: для доменов MAX VPN нужно выключить (или добавить их в исключения).

## 2. Прогон
```bash
.venv/bin/python -m tools.live_smoke --yes          # /me, /subscriptions, /me/commands, 7 тестовых сообщений, PUT, DELETE
```
Флаг `--yes` снимает вопрос перед отправкой. Сообщения уходят только владельцу (MAX_TEST_USER_ID) и потом
удаляются; `--keep` их оставит (нужно, чтобы владелец посмотрел глазами или открыл «Smoke 5»).
Проверка того, что приходит ОТ клиента MAX (пустой answer `{}`, контакт, фото):
```bash
docker compose stop app      # иначе апдейты заберёт бот
.venv/bin/python -m tools.live_smoke --yes --keep --listen 120
```
Пока идут 120 секунд, попроси владельца в чате с ботом нажать любую кнопку «Smoke», нажать «Отправить мой номер»
и прислать фото счётчика. Сырые апдейты сохраняются в `data/live_updates.jsonl`: это эталон формата для
тестов (телефоны и имена перед переносом в tests/ замаскировать). Потом снова запусти бота: `docker compose start app`.
initData мини-приложения: `.venv/bin/python -m tools.live_smoke --check-init-data '<строка>'`.

## 3. Разбор таблицы (FAIL → причина → где править)
| Симптом | Причина | Где |
|---|---|---|
| `0 network … CERTIFICATE` | не подхватился CA Минцифры | `app/integrations/max_api.py: ssl_context()`, `certs/` |
| `0 network` без TLS | нет доступа к MAX (VPN, DNS, провайдер) | окружение, не код |
| `401` | токен неверный или отозван | `.env` (новый токен выдают на business.max.ru) |
| BOT_USERNAME ≠ /me | open_app получит 404 | `.env` BOT_USERNAME |
| есть подписки | вебхук глушит long polling | спросить владельца → `DELETE /subscriptions?url=…` |
| `400 proto.payload` на сообщении | невалидная кнопка роняет всё сообщение | `app/bot/keyboards.py` (какая кнопка — видно по имени шага) |
| `404 not.found` на open_app | в web_app не username бота | `keyboards.open_app`, BOT_USERNAME |
| PUT «только убрать кнопки» FAIL | MAX не принимает PUT без text | `max_api.edit`, `ctx.clear_keyboard` (передавать текст сообщения) |
| answer `{}` FAIL | пустой ответ не принят | `ctx.ack`, `router._quiet_ack` → слать `notification` |
| фото: скачивание FAIL / 415 / 0 байт | CDN отдаёт редирект (httpx по умолчанию не идёт за ним) или другой content-type | `max_api.download` (`follow_redirects=True`), `app/bot/photos.py` |
| contact: нет TEL | другой формат vcf | `app/domain/people.py: parse_vcf_phone`, `app/bot/events.py` |
| contact: hash WARN | другая формула подписи | не блокирует: проверка мягкая, записать факт |
Глазами (владелец смотрит чат или ты через браузер, см. скилл live-scenario): жирный и курсив без `**`,
экранированные `* _ [ ] \`` видны как есть, подписи 24/32/40 не обрезаны многоточием (обрезанные длины
записать; при необходимости ужесточить лимиты в `keyboards.py` и CLAUDE.md), open_app открывается внутри MAX.

## 4. Фикс
1. Минимальная правка в нужном модуле, стиль как у соседнего кода.
2. Тест с реальным форматом из `data/live_updates.jsonl` или из ответа MAX: `tests/fakes.py` (фабрики апдейтов)
   или `httpx.MockTransport` для клиента (см. `tests/test_foundation.py`).
3. `.venv/bin/python -m pytest -q`: всё зелёное. Затем повторить `live_smoke`.
4. Коммитить только по просьбе владельца.

## 5. Итог владельцу
Коротко: таблица (OK/FAIL/WARN), что из списка «НЕ проверено» теперь подтверждено или опровергнуто,
что исправлено и что предлагается. Подтверждённые факты перенеси в CLAUDE.md (раздел «MAX: что важно»)
и сними пометку [НЕ ПРОВЕРЕНО] в `docs/LIVE_CHECKLIST.md`.
