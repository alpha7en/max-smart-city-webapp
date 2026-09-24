# Архитектура

Бот, планировщик и API мини-приложения работают в одном процессе FastAPI (`app/main.py`). Бот и планировщик
запускаются фоновыми задачами в lifespan. Внешний HTTP ходит только из `app/integrations/`. В `app/domain/`
лежат чистые функции без ввода-вывода. SQL собран в `app/repo.py`.

## Модули

```
app/
  main.py              FastAPI: /api/health, /api/*, статика /app/; в lifespan запускает poller и scheduler
  config.py            Settings из переменных окружения
  clock.py             текущее время в TZ (МСК); тесты подменяют его через set_now()
  db.py, schema.sql    подключение SQLite (WAL, foreign_keys) и миграции по PRAGMA user_version
  repo.py              все SQL-запросы; транзакции через repo.tx()
  readings.py          submit_reading(): общие проверки и запись показания для бота и API
  scheduler.py         раз в минуту удаляет просроченные фото, раз в ~10 минут (9–21 МСК) шлёт уведомления
  domain/
    meters.py          типы счётчиков, разбор и формат значений, окно подачи, демо-счёт, пороги прироста
    addresses.py       кандидат адреса, ключ дедупликации, короткие подписи адресов
    people.py          проверка ФИО, нормализация телефона, телефон из vCard
    access.py          модель прав: первый по адресу собственник, остальные ждут разрешения
    dashboard.py       строки меню-дашборда и срочное действие (общие для бота и /api/me)
  integrations/
    max_api.py         клиент MAX Bot API (httpx, повторы при 429/5xx, TLS с сертификатом Минцифры)
    certs/             russian_trusted_ca.pem
    address_service.py DaData suggest или локальный разбор адреса
    recognizer.py      HttpRecognizer (RECOGNIZER_URL) или StubRecognizer (демо)
  bot/
    events.py          сырой update MAX → Event (текст, фото, контакт, кнопка, bot_started)
    poller.py          long polling GET /updates, маркер в kv, отдельная задача на каждый апдейт
    router.py          общие правила неожиданного ввода и регистрация обработчиков декораторами
    states.py          перечень состояний диалога
    session.py         сессия (состояние, данные, flow_id, TTL) в таблице sessions
    ctx.py             контекст события: reply/toast/ack, кнопки, заметки к следующему сообщению
    keyboards.py       кнопки MAX, кодек payload «flow|action|arg», проверка лимитов и URL
    photos.py          скачивание фото во временный файл, удаление, чистка по сроку
    flows/             registration, profile (профиль и права доступа), submission, menu, notify (+ /demo)
    texts/             все тексты бота; fmt.py: экранирование markdown, числа, деньги, даты
  web/
    auth.py            проверка initData мини-приложения (HMAC + срок)
    api.py             /api/me, /api/meters/{id}, /api/readings, /api/recognize
    static/            мини-приложение: index.html, app.js, styles.css (без сборки)
tools/live_smoke.py    живая проверка MAX API тем же клиентом и теми же кнопками
tools/transcript.py    пример диалога через настоящий роутер → docs/DIALOG_EXAMPLE.md
tests/                 pytest: fakes.py (FakeMaxApi, апдейты в формате MAX), conftest.py (фикстура chat)
```

## Путь одного апдейта

1. `Poller.poll_once()` вызывает `GET /updates?marker&timeout=25`, сохраняет `marker` в `kv` и создаёт по задаче на апдейт.
2. `events.parse_update()` превращает update в `Event`. Кнопку нажал `callback.user`, а в `message.sender` лежит сам бот.
   Сообщения не из личного диалога отбрасываются.
3. `Router.handle()` отбрасывает дубли, берёт блокировку пользователя, загружает пользователя и сессию,
   применяет общие правила (см. [SCENARIO.md](SCENARIO.md)) и вызывает обработчик состояния из `flows/`.
4. Обработчик читает и пишет через `repo`, подачу делает через `readings.submit_reading()`, отвечает через
   `ctx.reply()` → `MaxApi.send()` или `POST /answers`. На нажатие кнопки бот отвечает всегда.
5. Сессия сохраняется только после успешной обработки. При исключении пользователь видит «Что-то пошло не так…»,
   состояние остаётся прежним, в логе появляется `trace=`.

Мини-приложение ходит в `/api/*` и пользуется теми же `repo`, `readings.submit_reading()` и `domain/dashboard.py`.
После подачи из мини-приложения бот пишет подтверждение в чат.

## Хранение

SQLite `DATA_DIR/bot.db`. В Docker это том `./data`, поэтому данные переживают `docker compose down`.
Показания хранятся целыми числами в тысячных долях единицы. Период показания — `YYYY-MM`, одно действующее
показание на счётчик за месяц. Временные фото лежат в `DATA_DIR/photos/` со сроком жизни в таблице `photos`.
Состав таблиц описан в README, раздел «Данные».

## Безопасность

- initData мини-приложения проверяется по HMAC-SHA256: ключ равен `HMAC("WebAppData", BOT_TOKEN)`, срок жизни задаёт `INIT_DATA_TTL`.
  Без валидного initData API отвечает `401`. Обход `X-Dev-User` работает только при `DEV_AUTH=true` (по умолчанию выключен).
- CORS разрешён только доменам из `MINIAPP_ORIGINS`: методы GET/POST, заголовки `X-Max-Init-Data` и `Content-Type`, без cookies.
  Мини-приложение принимает `?api=` только для localhost, иначе initData мог бы уйти на чужой хост.
- TLS проверяется всегда: certifi плюс Russian Trusted Root CA из `integrations/certs/`. `verify=False` в коде нет.
- Секреты задаются только через окружение (`.env` в `.gitignore`). Токен не пишется в логи и не выводится `live_smoke`.
- Контейнер работает от непривилегированного пользователя `app`. Фото ограничены 10 МБ, файлы создаются
  с правами 0600 и удаляются после использования.
- Пользовательский ввод экранируется перед markdown (`texts/fmt.esc`). Кнопки `link` принимают только публичные http(s)-URL.

## Как расширять

**Реальное распознавание.** Поднять сервис распознавания (например, вторым сервисом в `compose.yaml`) и задать
`RECOGNIZER_URL`. Если его ответ отличается от контракта из `integrations/recognizer.py`, поправить
только `HttpRecognizer._parse`. Бот и API уже умеют работать с низкой уверенностью, ошибкой и таймаутом.

**Новый тип счётчика.** Добавить значение в `MeterType` и `SPECS` (подпись, единица, разрядность, порог прироста)
в `domain/meters.py`. Расширить `CHECK` на `meters.type` миграцией (`db.MIGRATIONS`, `SCHEMA_VERSION`), добавить
пример в `texts/submission.EXAMPLES`, кнопку в `flows/submission.TYPE_ROWS` и параметры заглушки в `recognizer.py`.
Тесты: `tests/test_submission.py`.

**Новый регион или УК.** Адреса DaData покрывают всю Россию, локальный разбор знает города федерального значения
и формат «г Город, улица, дом». Окно подачи сейчас одно на всех (`SUBMIT_DAY_FROM/TO`). Для разных УК нужно
хранить график у адреса и передавать его в `submission_window()`. Передачу показаний в УК стоит добавить
отдельным клиентом в `app/integrations/` и вызывать после успешного `submit_reading()`, заменив пометку «смоделирована».
