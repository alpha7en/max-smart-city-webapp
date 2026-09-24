---
name: fix-from-logs
description: Разбирает логи бота (docker compose logs): находит trace_id, MaxApiError/«MAX API 4xx», ошибки polling и распознавания, воспроизводит проблему тестом, чинит и прогоняет pytest. Вызывать на фразы «разбери логи», «бот написал что-то пошло не так», «что в логах», «почему упало», «trace …».
---

# fix-from-logs: разбери логи

## 1. Собрать
```bash
docker compose logs --since 30m --no-color app > data/app.log   # или --tail 2000; на сервере то же через ssh
grep -nE 'trace=|Traceback|MAX API|MaxApiError|polling error|retry in|failed|not registered|broken session' data/app.log
```
Если владелец назвал trace (8 hex-символов из «Что-то пошло не так…»), ищи `grep -n 'trace=<id>' -A40 data/app.log`.
Токен в логи не попадает. Если попал, это отдельный баг: сообщи и не копируй его никуда.

## 2. Классифицировать
| В логе | Значит | Где смотреть |
|---|---|---|
| `handler failed trace=… user=… state=… kind=…` + трассировка | исключение в сценарии; состояние не сохранено | последний кадр трассировки в `app/bot/flows/*`; сессия: `sqlite3 data/bot.db "select state,data from sessions"` |
| `MAX API 400 proto.payload` | невалидная кнопка, всё сообщение не ушло | кто строил клавиатуру (трассировка) → `app/bot/keyboards.py` |
| `MAX API 404 not.found … LinkPK` | open_app с URL вместо username | `keyboards.open_app`, BOT_USERNAME |
| `MAX API 400` на `/answers` | неверное тело ответа на callback | `ctx.reply/toast/ack`, `max_api.answer` |
| `polling error … 401/403` | токен | `.env` |
| `polling error` сетевой, повторяется | сеть, TLS, VPN | `max_api.ssl_context`, окружение |
| `attachment.not.ready`, `retry in` | нормальные повторы | баг, только если повторы кончились |
| `pending photo download failed`, `PhotoError`, `415 not_image`, `413 too_large` | фото не скачалось | `app/bot/photos.py`, `max_api.download` (редиректы CDN?) |
| `recognizer failed … ConnectError` | `meter-reader` не запущен или не тот URL | `COMPOSE_PROFILES=recognizer`, `RECOGNIZER_URL=http://meter-reader:8000/recognize`, `docker compose ps` |
| `recognizer failed … 502` | Yandex Cloud: ключ, квота, битый ответ модели | `docker compose logs meter-reader`; `services/meter_reader` (llm.py, recognizer.py) |
| `recognizer failed … 400` | сервис не смог открыть картинку | формат фото из MAX; `services/meter_reader/meter_reader/image_utils.py` |
| `recognizer failed: TimeoutError`/`ReadTimeout` | модель дольше 20 с | `YC_TIMEOUT`, нагрузка; клиент `HttpRecognizer` |
| цифры распознаны неверно (жалоба, не ошибка) | качество модели | прогнать фото через `services/meter_reader/recognize.sh`, передать автору сервиса; бот не чинить |
| `hook … is not registered` | поток не зарегистрировал точку входа | `@on_hook` в `app/bot/flows/*` |
| `broken session … reset to IDLE` | в sessions.data несовместимый JSON | кто пишет `session.data` |
| `scheduler tick failed` | ошибка уведомлений | `app/scheduler.py`, `flows/notify.py` |

## 3. Воспроизвести тестом (до фикса)
- Сценарий: фикстура `chat` из `tests/conftest.py`. Методы: `await chat.text("…")`, `chat.photo()`, `chat.press("Подпись")`,
  `chat.payload("flow|action|arg")`. Проверки через `api.texts()`, `api.last_text()`, `api.button("…")`, `api.named("send")`,
  плюс строки БД через фикстуру `repo`. Состояние из лога воспроизведи теми же шагами, что прошёл пользователь.
- Формат MAX: если апдейт выглядел иначе, чем в `tests/fakes.py`, возьми сырой из `data/live_updates.jsonl`
  (`tools.live_smoke --listen`). Замаскируй телефон и имя, добавь фабрику или параметр в fakes.
- Ошибка ответа MAX: для клиента используй `httpx.MockTransport` (как в `tests/test_foundation.py`), для сценария — подкласс `FakeMaxApi`, который бросает `MaxApiError(400, "proto.payload", "…")`.
- Тест сначала должен упасть по той же причине, что в логе.

## 4. Починить и проверить
1. Минимальная правка там, где причина, а не там, где симптом. Тексты только в `app/bot/texts/*`, кнопки только через keyboards.
2. `.venv/bin/python -m pytest -q`: всё зелёное, быстрее 15 с.
3. `docker compose up -d --build` и повторить шаг вживую (скилл `live-scenario`, нужный пункт чек-листа).
4. Владельцу: что было (строка лога), причина, файл и правка, какой тест добавлен. Коммит — по его просьбе.
