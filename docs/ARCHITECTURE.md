# Архитектура

Бот, планировщик и API мини-приложения работают в одном процессе FastAPI (`app/main.py`). Бот и планировщик
запускаются фоновыми задачами в lifespan. Внешний HTTP ходит только из `app/integrations/`. В `app/domain/`
лежат чистые функции без ввода-вывода. SQL собран в `app/repo.py`. Распознавание показаний — отдельный
сервис `services/meter_reader/` (свой контейнер `meter-reader`, профиль compose `recognizer`).

## Модули

```
app/
  main.py              FastAPI: /api/health, /api/*, статика /app/; в lifespan запускает poller и scheduler
  config.py            Settings из переменных окружения
  clock.py             текущее время в TZ (МСК); тесты подменяют его через set_now()
  db.py, schema.sql    подключение SQLite (WAL, foreign_keys) и миграции по PRAGMA user_version
  repo.py              все SQL-запросы; транзакции через repo.tx()
  readings.py          submit_reading(): общие проверки и запись показания для бота и API
  arshin_service.py    поверка по ФГИС «Аршин»: проверка после подачи (≤6 с, дальше в фоне), выбор записи, суточное обновление
  scheduler.py         раз в минуту удаляет просроченные фото, раз в ~10 минут (9–21 МСК) шлёт уведомления
  domain/
    meters.py          типы счётчиков, разбор и формат значений, окно подачи, демо-счёт, пороги прироста
    addresses.py       кандидат адреса, ключ дедупликации, короткие подписи адресов
    people.py          проверка ФИО, нормализация телефона, телефон из vCard
    access.py          модель прав: первый по адресу собственник, остальные ждут разрешения
    dashboard.py       строки меню-дашборда и срочное действие (общие для бота и /api/me)
    verification.py    записи ФГИС: даты ISO и dd.mm.yyyy, варианты номера, выбор записи (high/low/none)
    serials.py         заводской номер: очистка, ключ сравнения, показ и проверка формата по типу счётчика
  integrations/
    max_api.py         клиент MAX Bot API (httpx, повторы при 429/5xx, TLS с сертификатом Минцифры)
    address_service.py DaData suggest или локальный разбор адреса
    recognizer.py      HttpRecognizer (RECOGNIZER_URL → services/meter_reader) или StubRecognizer (демо)
    arshin.py          клиент ФГИС «Аршин» /eapi/vri: троттлинг 0,6 с, ≤4 запроса, повтор, breaker, кэш; fixtures — демо;
                       ARSHIN_FALLBACK_IPS — резервные IP, если DNS в контейнере не резолвит хост (install_dns_fallback)
  bot/
    events.py          сырой update MAX → Event (текст, фото, контакт, кнопка, bot_started)
    poller.py          long polling GET /updates, маркер в kv, отдельная задача на каждый апдейт
    router.py          общие правила неожиданного ввода и регистрация обработчиков декораторами
    states.py          перечень состояний диалога
    session.py         сессия (состояние, данные, flow_id, TTL) в таблице sessions
    ctx.py             контекст события: reply/toast/ack, кнопки, заметки к следующему сообщению
    keyboards.py       кнопки MAX, кодек payload «flow|action|arg», проверка лимитов и URL
    photos.py          скачивание фото во временный файл, удаление, чистка по сроку
    flows/             registration, profile (профиль и права доступа), invite (приглашения, отзыв доступа),
                       submission, menu, notify (+ /demo)
    texts/             все тексты бота; fmt.py: экранирование markdown, числа, деньги, даты
  web/
    auth.py            проверка initData мини-приложения (HMAC + срок)
    api.py             /api/me, /api/meters/{id}, /api/readings, /api/recognize
    static/            мини-приложение: index.html, app.js, styles.css (без сборки)
certs/                 russian_trusted_ca.pem: публичный CA Минцифры для TLS к MAX (не секрет; certs/README.md)
tools/live_smoke.py    живая проверка MAX API тем же клиентом и теми же кнопками
tools/transcript.py    пример диалога через настоящий роутер → docs/DIALOG_EXAMPLE.md
tests/                 pytest: fakes.py (FakeMaxApi, апдейты в формате MAX), conftest.py (фикстура chat)

services/meter_reader/ отдельный FastAPI-сервис: POST /recognize (фото → Qwen в Yandex Cloud AI Studio → JSON)
  meter_reader/        api.py, recognizer.py (барабаны → показание, 5 + 3), prompts.py, llm.py, image_utils.py
  scripts/eval_water.py  замер точности на датасете Yandex.Toloka Water Meters
  tests/               постобработка ответа модели без сети
```

## Распознавание

Бот скачивает фото из MAX во временный файл и, когда счётчик выбран, вызывает `recognizer.recognize(path, type,
tariffs)` с таймаутом 25 с. `HttpRecognizer` отправляет файл в `meter-reader` (`POST /recognize`, поле `image`)
и переводит ответ в `Recognition`: значение в тысячных из `reading_text`, текст как прочитан (без дописанных нулей),
серийный номер, свою уверенность, коды проблем `issues` с пояснением модели, производителя и модель. Серийный номер
сверяется с сохранённым и обязателен (нет ни у счётчика, ни на фото — «Переснять» / «Ввести номер»); производитель
и модель служебные: пишутся только в `readings.recognized_json` и в тексты не попадают.
«Не распознали» с конкретными причинами: `readable=false`, нет значения, ошибка сервиса, его `confidence` < 0.6 или
размытость при недоборе целых цифр. Прочитали с оговорками — предупреждения на экране проверки; «Проверьте цифры
внимательно», если тип не совпал, цифр больше, чем на табло такого типа, или это не водомер (свет и газ не проверены
на данных). У многотарифного счётчика сервис видит один тариф: его значение идёт подсказкой, тарифы вводятся вручную.
Мини-приложение ходит в тот же распознаватель через `POST /api/recognize`. Фото на время распознавания уходит
в Yandex Cloud, у нас удаляется после подачи.

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
После подачи из мини-приложения бот пишет подтверждение в чат и в фоне проверяет поверку по ФГИС.
Изменения профиля, адресов и доступа мини-приложение не делает само: открывает чат диплинком
`max.ru/<бот>?start=<payload>`. Роутер переводит payload в действие (`START_PAYLOADS`) или, для `inv_…`,
в точку входа приглашений (`START_PREFIXES` → hook `invite.start`), в том числе до регистрации.

## Хранение

SQLite `DATA_DIR/bot.db`. В Docker это том `./data`, поэтому данные переживают `docker compose down`.
Схема: `schema.sql` (версия 1) плюс `db.MIGRATIONS` по `PRAGMA user_version` (2 — `invites`, 3 — поля ФГИС
у `meters` и `arshin_cache`); новая таблица или поле — только новой миграцией. Показания хранятся целыми числами в тысячных долях единицы. Период показания — `YYYY-MM`, одно действующее
показание на счётчик за месяц. Временные фото лежат в `DATA_DIR/photos/` со сроком жизни в таблице `photos`.
Состав таблиц описан в README, раздел «Данные».

## Безопасность

- initData мини-приложения проверяется по HMAC-SHA256: ключ равен `HMAC("WebAppData", BOT_TOKEN)`, срок жизни задаёт `INIT_DATA_TTL`.
  Без валидного initData API отвечает `401`. Обход `X-Dev-User` работает только при `DEV_AUTH=true` (по умолчанию выключен).
- CORS разрешён только доменам из `MINIAPP_ORIGINS`: методы GET/POST, заголовки `X-Max-Init-Data` и `Content-Type`, без cookies.
  Мини-приложение принимает `?api=` только для localhost, иначе initData мог бы уйти на чужой хост.
- TLS проверяется всегда: certifi плюс Russian Trusted Root/Sub CA из `certs/` в корне репозитория (публичные, отпечатки закреплены тестом). `verify=False` в коде нет.
- Секреты задаются только через окружение (`.env` в `.gitignore`). Токен не пишется в логи и не выводится `live_smoke`.
- Контейнер работает от непривилегированного пользователя `app`. Фото ограничены 10 МБ, файлы создаются
  с правами 0600 и удаляются после использования.
- Пользовательский ввод экранируется перед markdown (`texts/fmt.esc`). Кнопки `link` принимают только публичные http(s)-URL.

## Как расширять

**Распознавание.** Сервис `services/meter_reader` меняется независимо от бота: промпт, модель, постобработка.
Если меняется формат ответа, поправить только `HttpRecognizer._parse` и `tests/test_recognizer.py`.
Бот отправляет подсказки `meter_type` и `tariffs`: сервис берёт по ним промпт типа и отмечает `wrong_type`.
Для нового типа счётчика добавить его в промпт сервиса и замерить точность, затем убрать из «непроверенных»
(`_VALIDATED` в `recognizer.py`).

**Новый тип счётчика.** Добавить значение в `MeterType` и `SPECS` (подпись, единица, разрядность, порог прироста)
в `domain/meters.py`. Расширить `CHECK` на `meters.type` миграцией (`db.MIGRATIONS`, `SCHEMA_VERSION`), добавить
пример в `texts/submission.EXAMPLES`, кнопку в `flows/submission.TYPE_ROWS` и параметры заглушки в `recognizer.py`.
Тесты: `tests/test_submission.py`.

**Новый регион или УК.** Адреса DaData покрывают всю Россию, локальный разбор знает города федерального значения
и формат «г Город, улица, дом». Окно подачи сейчас одно на всех (`SUBMIT_DAY_FROM/TO`). Для разных УК нужно
хранить график у адреса и передавать его в `submission_window()`. Передачу показаний в УК стоит добавить
отдельным клиентом в `app/integrations/` и вызывать после успешного `submit_reading()`, заменив пометку «смоделирована».
