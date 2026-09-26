---
name: deploy
description: Деплой бота на прод-сервер maxsmartcity.ru. Docker compose (app + meter-reader) за готовым nginx, статика мини-приложения в /var/www/maxsmartcity.ru, скрипт deploy/update.sh, проверка /api/health. nginx не трогать, GitHub Pages для прода не нужен. Вызывать на фразы «задеплой на сервер», «выложи бота», «обнови сервер», «выложи ветку».
---

# deploy: задеплой на сервер

Прод (слова владельца): бот, API и мини-приложение на одном сервере `https://maxsmartcity.ru`.
Полное устройство — `docs/DEPLOY_SERVER.md`. Коротко:
- nginx (настроен отдельно, НЕ ТРОГАТЬ): `/api/` → `127.0.0.1:8080`, `/` → `/var/www/maxsmartcity.ru` (статика).
- `~/max-smart-city-webapp`: `compose.yaml` (app + meter-reader), `.env` и `compose.override.yaml`
  (порт app на 127.0.0.1) лежат только на сервере. Данные в `./data`.
- Доступ по SSH (`user@<IP сервера>`) и пароль sudo даёт владелец; секреты в репо и память не записывать.
- Один токен — один процесс: перед запуском на сервере останови локальный бот (`docker compose stop app`).

## Обновить
```bash
ssh user@<host> 'cd max-smart-city-webapp && bash deploy/update.sh [ветка]'   # sudo спросит пароль для /var/www
```
Скрипт: `git pull --ff-only` → бэкап БД в `data/backups/` → `docker compose up -d --build --wait` →
копирует `app/web/static/*` в `/var/www/maxsmartcity.ru` (с `?v=<коммит>`) → проверки.
Если меняли только статику: `bash deploy/update.sh --static`.

## Проверить
```bash
curl -s https://maxsmartcity.ru/api/health                                   # {"ok":true}
curl -s -o /dev/null -w '%{http_code}\n' https://maxsmartcity.ru/api/me      # 401 без initData — так и надо
ssh user@<host> 'cd max-smart-city-webapp && docker compose ps && curl -s localhost:8000/health && \
  docker compose logs --since 5m app | grep -E "MAX bot: id=|polling started|ERROR"'
```
Ожидается: оба контейнера `(healthy)`, `MAX bot: id=423938205`, `polling started`, meter-reader `{"status":"ok",…}`.
В `.env` не должно быть `RECOGNIZER_URL=http://127.0.0.1…`: в контейнере это сам app, адрес meter-reader даёт compose.

## Живая проверка после деплоя
- `.venv/bin/python -m tools.live_smoke` локально (без `--listen`: слушание отнимет апдейты у серверного бота).
- `docs/LIVE_CHECKLIST.md`: разделы 1 и 3 бегло, раздел 4 (мини-приложение) полностью (скилл `live-scenario`).

## Откат и бэкапы
`git checkout <коммит> && docker compose up -d --build --wait`. Бэкапы БД: `data/backups/` (10 последних),
восстановление описано в `docs/DEPLOY_SERVER.md`.
