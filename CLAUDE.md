# Единый бот ЖКХ в MAX

Чат-бот для мессенджера MAX (хакатон «Умный город», сдача 30.09.2026 12:00 МСК). Главное — подать
показания счётчика по фото. Сценарий: приветствие → регистрация (ФИО, телефон, адрес) → меню-дашборд →
фото счётчика → выбор счётчика → распознавание → проверка → отправка. Есть уведомления (срок подачи,
поверка, демо-счёт) и мини-приложение (главная, подача, история).
Цель — лаконичный MVP с одним сквозным сценарием, который реально работает. Лишних фич не добавлять.

## Кому верить (при конфликте побеждает источник выше)
1. Владелец проекта: его слова в чате и его сценарий точек входа (`1_user_scenario_ENTRY_POINTS.md`,
   в репозитории может не быть, тогда спроси владельца).
2. Документы хакатона: ТЗ, регламент, FAQ. Docker обязателен, моки разрешены с явной пометкой,
   бот должен работать в MAX, мини-приложение отдаётся по HTTPS.
3. Заметки и старые диалоги с агентами: только для контекста, в них часто галлюцинации.
4. Код, тесты и документация в репо: наименьшее доверие. Факты про MAX API сверяй со схемой
   github.com/max-messenger/api-schema и живой проверкой (см. ниже), а не со старым кодом.

## Структура
```
app/
  main.py            FastAPI: /api/* + статика мини-приложения на /app; в lifespan poller и scheduler
  config.py          Settings из env (.env.example: все переменные с комментариями)
  db.py schema.sql   SQLite (WAL), миграции по user_version
  repo.py            весь SQL; секции потоков «# === Sx ===»
  clock.py           время МСК (в тестах подменяется)
  scheduler.py       уведомления, чистка фото, раз в сутки обновление поверки по ФГИС
  domain/            чистые функции без I/O: meters, people, addresses, access, serials, verification (+dashboard)
  integrations/      ВЕСЬ внешний HTTP: max_api.py (клиент MAX + certs/ Минцифры),
                     recognizer.py (клиент meter-reader или демо-заглушка), address_service.py (DaData/локально),
                     arshin.py (ФГИС «Аршин», поверка по заводскому номеру; ARSHIN_MODE=live|fixtures|off,
                     ARSHIN_FALLBACK_IPS — IP хоста, если DNS в контейнере его не резолвит)
  arshin_service.py  проверка поверки после подачи и раз в сутки; выбор записи — domain/verification.py
  bot/
    events.py        сырой update MAX → Event        poller.py   long polling, marker в kv
    router.py        глобальные правила + @on_state/@on_repeat/@on_global/@on_command/@on_hook
    states.py session.py ctx.py photos.py keyboards.py (кнопки + payload "flow|action|arg")
    flows/           registration, profile, invite, submission, menu, notify
    texts/           ВСЕ тексты бота (значения в texts/yaml/*.yaml, для редактора); fmt.py
  web/               auth.py (initData), api.py (/api/*), static/ (мини-приложение, vanilla JS)
tests/               pytest; conftest.py (фикстура chat), fakes.py (FakeMaxApi + апдейты в формате MAX)
services/meter_reader/  сервис распознавания (автор — коллега, свой README): POST /recognize, фото → Qwen
                     в Yandex Cloud → показание, тип, серийник. Свой Dockerfile и тесты, контейнер meter-reader
tools/live_smoke.py  живая проверка MAX API (нужен доступ к MAX, то есть запуск из РФ)
```

## Команды
```bash
python3.12 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
python -m pytest -q                                   # все тесты (~20–25 с), должны быть зелёными
cp .env.example .env                                  # затем вписать BOT_TOKEN
docker compose up -d --build && docker compose logs -f app    # бот + API на :8080
# + распознавание: в .env COMPOSE_PROFILES=recognizer, YC_API_KEY, YC_FOLDER_ID,
#   RECOGNIZER_URL=http://meter-reader:8000/recognize; проверка: curl localhost:8000/health
services/meter_reader/recognize.sh фото.jpg             # что сервис видит на фото
(cd services/meter_reader && python -m pytest -q)       # тесты сервиса (своё окружение: его requirements + pytest)
curl -s localhost:8080/api/health                     # {"ok":true}
uvicorn app.main:app --env-file .env --port 8080      # без docker (docker compose stop app перед этим)
python -m tools.live_smoke --dry-run                  # запросы к MAX без сети и токена
sqlite3 data/bot.db 'select user_id,state,data from sessions'  # состояние диалогов (том ./data)
```

## Правила кода
- Тексты бота только в `app/bot/texts/*` (сами строки вынесены в `app/bot/texts/yaml/*.yaml` для удобной правки редактором). Стиль: «мы», к пользователю «вы», не длиннее 8 строк, без

  эмодзи, без канцелярита («успешно», «данный»), без CAPS (кроме строки «ПРИШЛИТЕ ВАШЕ ФОТО В ЧАТ»),
  без служебных тегов и сырых URL. Пользовательский ввод пропускать через `fmt.esc()`.
- Кнопки только через `app/bot/keyboards.py` (`kb`, `callback`, `gbtn`, `open_app`, `request_contact`, `link`).
  Подписи: обычные не длиннее 24, срочная 32, счётчики и варианты адреса 40. Не обрезать молча,
  сразу писать коротко. У каждого сообщения должна быть кнопка или понятный следующий ввод.
- Внешний HTTP только в `app/integrations/*`. TLS не отключать никогда: сертификат Минцифры лежит в `certs/`.
- Секреты только в `.env`: он в .gitignore, в коде и логах токенов нет. Новая переменная → `config.py` + `.env.example`.
- Моки помечать прямо в UI и README: демо-распознавание, модель прав, модельные сроки, демо-счета,
  «передача в УК смоделирована», адрес «не сверен с ФИАС». Заглушку не выдавать за интеграцию.
- Время брать из `app.clock` / `ctx.now`. Сценарные тесты писать через фикстуру `chat` + `FakeMaxApi`.
  Проверять тексты, кнопки, state и строки БД.
- Регистрация хендлеров декораторами, `router.py` без нужды не править. Межпотоковые вызовы через `call_hook`.

## Распознавание (services/meter_reader)
- Сервис пишет и отлаживает коллега: без нужды его код не править, а если правишь, то вместе с его README
  и тестами. Граница с ботом одна: `HttpRecognizer._parse` + `tests/test_recognizer.py`.
- Проверено только на водомерах (Yandex.Toloka): 74% показаний точно, 91% верных целых м³, ~2.5 с на фото.
  `confidence` сервиса почти всегда 0.95, уверенность считает наш клиент (`HttpRecognizer._parse`): низкая
  уверенность или blurry/digits_not_visible при недоборе разрядов → «не распознали» с причинами; issues при
  успешном чтении показываются на экране проверки; серийник обязателен для счётчика без номера.
- Без `RECOGNIZER_URL` работает демо-заглушка с пометкой в UI. Ключи YC только в `.env`. Исходная папка
  коллеги `УСЛОВИЯ/сырые файлы…` не в git и содержит ключи: оттуда ничего не копировать, кроме кода.

## MAX: что важно (проверено живьём или по схеме)
- API `https://platform-api2.max.ru`, заголовок `Authorization: <token>` без Bearer. Нужен CA Минцифры.
- Бот `t226_hakaton_max_bot` (id 423938205). Тестовый аккаунт владельца: user_id 5273381, чат `web.max.ru/420790200`.
- Long polling `GET /updates`. Вебхук и polling взаимоисключающие. Два процесса с одним токеном
  (локально и на сервере) делят апдейты между собой, поэтому держи запущенным один.
- Кто нажал кнопку: `callback.user.user_id`. В `message.sender` лежит сам бот.
- На каждый callback отвечать `POST /answers?callback_id=` (id в query, не в теле); `message` заменяет сообщение.
- Одна невалидная кнопка роняет ВСЁ сообщение (400), и бот молчит. `link` принимает только публичный
  http(s): localhost даёт `400 proto.payload`. `link` открывается во внешнем браузере.
- Мини-приложение открывать только через `open_app` с `web_app` = username бота. URL там даёт
  `404 not.found`. `payload` по шаблону `^[\w-]*$`, до 512 символов.
- Reply-клавиатуры нет, меню прикладывается к сообщению. Лимиты: 30 рядов, 7 кнопок в ряду,
  не больше 3 link/open_app/request_* в ряду. Длинные подписи клиент MAX обрезает многоточием.
- ФИО из Госуслуг MAX боту не отдаёт. Телефон берётся кнопкой `request_contact` (vcf_info + max_info).
- URL мини-приложения закреплён в MAX организаторами: `https://alpha7en.github.io/max-smart-city-webapp/`.
  НЕ МЕНЯТЬ. Бэкенд для него должен быть на постоянном HTTPS (переменная `MINIAPP_API_BASE`), не на туннеле.
- ФГИС «Аршин» доступен только с российских IP: живьём работает с машины владельца (в Docker понадобился
  ARSHIN_FALLBACK_IPS), из облачных агентов недоступен; реальные ответы — `tests/fixtures/arshin/`. Лимит 2 rps, без
  `verification_date_start`/`year` ищет только текущий год. Проверка с сервера:
  `curl -sS -G https://fgis.gost.ru/fundmetrology/eapi/vri --data-urlencode mi_number=<номер> --data-urlencode verification_date_start=2015-01-01`.
- Живьём ещё НЕ проверено: приходит ли `initData`; формат `request_contact` (TEL в vcf, max_info, hash);
  скачивается ли фото по `image.payload.url`; `start_param` из `open_app` с payload; пустой ответ
  `{}` на callback. Пока не проверено, не строить на этом логику без запасного пути.

## Живая проверка (облачные агенты до MAX не достают, её делает локальный Claude владельца)
- «проверь бота живьём» → скилл `live-smoke` (`tools/live_smoke.py`: /me, вебхуки, команды, все виды кнопок).
- «пройди сценарий в MAX» → скилл `live-scenario` (бот + web.max.ru + чек-лист `docs/LIVE_CHECKLIST.md`).
- «разбери логи» → скилл `fix-from-logs` (trace_id / MaxApiError → тест → фикс → pytest).
- «задеплой на сервер» → скилл `deploy` (VPS, Caddy HTTPS, MINIAPP_API_BASE, /api/health).
- «подними туннель», «мини-приложение пишет сервер не подключён» → скилл `miniapp-tunnel` (cloudflared к
  localhost:8080 → MINIAPP_API_BASE → пересборка Pages; только для проверки, адрес временный).
Отчёты живых прогонов пишутся в `data/` (`data/live_report.md`, `data/live_updates.jsonl`): data/ в .gitignore, не коммитить.
