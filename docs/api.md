# API мини-приложения

Бэкенд мини-приложения: `app/web/api.py`, клиент — `app/web/static/app.js`. Отдельного публичного API нет.

## Общее

- **Авторизация.** Каждый запрос `/api/*` (кроме `/api/health`) несёт заголовок `X-Max-Init-Data` — строку
  `WebApp.initData` из моста MAX. Сервер проверяет подпись (HMAC-SHA256, ключ `HMAC("WebAppData", BOT_TOKEN)`)
  и срок `INIT_DATA_TTL`, пользователь — `user.id` из initData. При `DEV_AUTH=true` вместо initData принимается
  `X-Dev-User: <MAX user_id>` (только для локальной разработки).
- **Ошибки** — всегда JSON `{"code": "...", "message": "..."}`, `message` по-русски для показа пользователю.
  Общие коды: `401 unauthorized`, `422 bad_request` (тело не разобралось), `500 internal`.
- **Показания** в ответах — числа в единицах счётчика (`123.456`); на вход — строки, как их ввёл пользователь
  (`"123,456"`). Поля тарифов — `t1`, `t2`, `t3`.
- **Даты** — `YYYY-MM-DD`, время — ISO 8601 в часовом поясе `TZ`.
- Незарегистрированный пользователь (профиля нет или регистрация не закончена) получает `403 no_access`
  на запросы к счётчикам и общему доступу; `GET /api/me` отвечает ему `registered: false`.

| Метод | Путь | Что делает |
|---|---|---|
| GET | `/api/health` | `{"ok": true}`, без авторизации |
| GET | `/api/me` | профиль, дашборд, счётчики, адреса |
| GET | `/api/meters/{id}` | счётчик и история показаний |
| DELETE | `/api/meters/{id}` | удалить счётчик |
| POST | `/api/recognize` | распознать фото счётчика |
| POST | `/api/readings` | подать показание |
| GET | `/api/shares` | общий доступ: свои адреса, ссылки, чужие адреса |
| POST | `/api/shares/invites` | создать ссылку на адреса |
| DELETE | `/api/shares/invites/{id}` | отменить ссылку |
| DELETE | `/api/shares/{address_id}/members/{member_id}` | закрыть человеку доступ к адресу |
| DELETE | `/api/shares/received/{address_id}` | выйти из общего доступа |

## GET /api/me

```json
{
  "registered": true,
  "user": {"full_name": "Иванова Анна Сергеевна", "phone": "+79123456789", "phone_verified": true,
           "photo_url": null},
  "dashboard": {...},
  "meters": [...],
  "addresses": [...],
  "bot_username": "t226_hakaton_max_bot"
}
```

Незарегистрированному — `registered: false`, `user: null`, пустые списки и пустой дашборд.
`photo_url` — фото профиля MAX: из initData или аватар из `GET /chats/{chat_id}` (кэш на час); может быть `null`.

**`dashboard`** — тот же дашборд, что в меню бота (`domain/dashboard.py`):

| Поле | Содержимое |
|---|---|
| `lines` | строки дашборда простым текстом (для бота; мини-приложение их не разбирает) |
| `urgent` | самое срочное: `{kind: verification\|bill\|submit, text, days_left}` или `null` |
| `window` | окно подачи: `{period, month_label, from, to, open, days_left, next_from}`; закрыто — ближайшее следующее |
| `bill`, `bills` | ближайший неоплаченный счёт и все: `{id, address_id, amount_kop, amount_text, due, days_left, demo}` (у `bill` ещё `count`) |
| `verification` | ближайшая поверка в пределах 60 дней: `{meter_id, meter_label, type, due, days_left}` или `null` |
| `pending` | адреса, ждущие одобрения собственника: `[{label, full_text}]` |
| `submitted`, `total` | сколько счётчиков подано за текущий месяц и сколько всего |

**Счётчик** (элемент `meters`):

| Поле | Содержимое |
|---|---|
| `id`, `type`, `type_label`, `unit`, `tariffs` | `type`: `cold_water`, `hot_water`, `electricity`, `gas`, `heat` |
| `address_id`, `address_label`, `address_full` | короткая подпись адреса (для кнопок) и полный адрес |
| `serial` | заводской номер или `null` |
| `verification_due`, `verification_source` | срок поверки и откуда он: `user` (паспорт), `model` (демо), `arshin` (ФГИС) |
| `arshin_url`, `arshin_demo` | карточка поверки на fgis.gost.ru; `true` — демо-данные ФГИС |
| `last` | последнее показание `{period, values, created_at, by}`; `by` — «Пётр С.», если подавал не вы |
| `submitted_this_period` | подано ли за текущий месяц |

**Адрес** (элемент `addresses`): `id`, `label`, `full_text`, `access` (`granted`, `pending`, `denied`),
`role` (`owner`, `tenant`), `verified` (`false` — не сверен с ФИАС). Для чужого адреса с доступом:
`owner` («Анна И.»), `owner_gen` («Анны И.»), `owner_female`, `since`. Для своего: `shared_count` (у скольких
людей доступ), `invites_count` (действующих ссылок), `members: [{name_short, access}]`.

## GET /api/meters/{id}

Поля счётчика и `history` — до 12 последних показаний, новые первыми:
`[{id, period, values, source, status, created_at, by}]`. `source`: `photo`, `photo_edited`, `manual`, `miniapp`;
`status`: `accepted` или `flagged` (большой прирост). Ошибки: `404 not_found` (нет или удалён),
`403 no_access` (адрес не ваш или доступ закрыт).

## DELETE /api/meters/{id}

Мягкое удаление: показания остаются. → `{"status": "deleted", "id": 1}`.
Ошибки: `404 not_found`, `403 no_access`, `403 not_owner` (к адресу есть доступ у других людей, а удаляет не собственник).

## POST /api/recognize

`multipart/form-data`: `meter_id`, `file` (изображение до 10 МБ). Фото сохраняется во временный файл, распознаётся
и сразу удаляется.

```json
{
  "values": {"t1": 595.825, "t2": null, "t3": null},
  "texts": {"t1": "595,825"},
  "serial": "18-452178",
  "confidence": 0.9, "stub": false,
  "readable": true, "issues": [], "note": null, "missing": [],
  "message": null,
  "serial_mismatch": false, "serial_note": null, "serial_required": false
}
```

- `texts` — показание как прочитано (без дописанных нулей), его и нужно ставить в поле ввода.
- `readable: false` — не распознали; `message` объясняет почему и что делать. При удачном чтении `message` —
  конкретные предупреждения о фото или `null`.
- `missing` — тарифы, которых нет на фото (их вводят вручную).
- `stub: true` — демо-распознавание.
- `serial_note` — номер на фото не совпал с сохранённым или не виден; `serial_required` — у счётчика нет номера
  и на фото его не разобрали: показание с фото будет принято только вместе с номером.

Ошибки: `413 too_large`, `415 not_image`, `422 no_file`, `400 bad_request` (битый multipart), `404`, `403`.

## POST /api/readings

```json
{"meter_id": 1, "values": {"t1": "595,825"}, "source": "photo", "serial": "18-452178",
 "confirm": false, "replace": false}
```

- `source`: `photo` (значения с фото) или `manual`. У счётчика без номера показание с `source: "photo"`
  принимается только с `serial`.
- `confirm: true` — подтвердить большой прирост; `replace: true` — заменить показание за этот месяц.

→ `{"status": "accepted" | "flagged", "reading": {...}, "serial": "18-452178"}` (`serial` — если номер сохранили).
После записи бот присылает подтверждение в чат, а если у счётчика есть номер, который ещё не сверяли с ФГИС
«Аршин», в фоне проверяет поверку.

| Ошибка | Когда |
|---|---|
| `422 bad_format` | не число, лишние разряды, пустое поле |
| `422 less_than_previous` | меньше прошлого показания |
| `409 needs_confirm` | прирост выше порога — повторить с `confirm: true` |
| `409 already_submitted` | за месяц уже подано — повторить с `replace: true` |
| `422 serial_required`, `serial_bad`, `serial_taken` | номер не указан, не похож на заводской, уже у другого счётчика адреса |
| `404 not_found`, `403 no_access` | нет счётчика или доступа |

## Общий доступ

**GET /api/shares**

```json
{
  "owned": [{"address_id": 1, "label": "Арбат 47к1, кв 32", "full_text": "...",
             "members": [{"member_id": 7, "name": "Олег П.", "since": "2026-10-01",
                          "last_submitted_at": "2026-10-18", "submitted_this_period": true}]}],
  "invites": [{"id": 3, "addresses": [{"address_id": 2, "label": "..."}], "expires_at": "2026-10-26",
               "url": "https://max.ru/<бот>?start=inv_<token>"}],
  "received": [{"address_id": 5, "label": "...", "full_text": "...", "owner": "Анна И.",
                "owner_gen": "Анны И.", "since": "2026-10-02"}],
  "limits": {"active_invites": 5}
}
```

`owned` — все свои адреса (в `members` только люди с открытым доступом), `invites` — действующие ссылки,
`received` — адреса, к которым доступ открыли вам.

**POST /api/shares/invites** `{"address_ids": [1, 2]}` → `201 {id, url, expires_at, share_text}`.
Ссылка одноразовая, действует 7 дней; `share_text` — готовый текст для пересылки.
Ошибки: `422 empty`, `403 not_owner`, `409 limit` (уже 5 действующих ссылок), `503 no_username` (имя бота неизвестно).

**DELETE /api/shares/invites/{id}** → `{"status": "cancelled", "id": 3}`.
Ошибки: `404 not_found` (чужая или нет такой), `409 invite_used` (ссылку уже приняли).

**DELETE /api/shares/{address_id}/members/{member_id}** — собственник закрывает доступ; человеку приходит
сообщение в чат. → `{"status": "closed", "address_id": 1, "member_id": 7}`. Ошибки: `403 not_owner`, `404 not_found`.

**DELETE /api/shares/received/{address_id}** — получатель убирает общий адрес у себя; собственнику приходит
сообщение, если доступ был открыт. → `{"status": "removed", "address_id": 5}`. Ошибка: `404 not_found`
(свой адрес так не убрать).
