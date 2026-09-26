# Сервер maxsmartcity.ru: как устроен и как обновлять

## Как устроено

```
Интернет ──443──▶ nginx (сертификат Let's Encrypt, конфиг /etc/nginx/sites-enabled/maxsmartcity.ru)
                   ├─ /api/*  ──▶ 127.0.0.1:8080  контейнер app (бот MAX + API мини-приложения)
                   └─ /       ──▶ /var/www/maxsmartcity.ru   статика мини-приложения (index.html, app.js, styles.css)

контейнер app ──▶ http://meter-reader:8000/recognize   контейнер meter-reader (распознавание, Qwen в Yandex Cloud)
               ──▶ platform-api2.max.ru (long polling), fgis.gost.ru (Аршин), DaData
```

- Сервер: Ubuntu 26.04, пользователь `user` (в группе `docker`, sudo по паролю). Docker и compose из пакетов
  Ubuntu (`docker.io`, `docker-compose-v2`), служба `docker` включена и стартует при загрузке.
- Репозиторий: `/home/user/max-smart-city-webapp`, ветка `compose-two-services`.
- Контейнеры описаны в `compose.yaml`: `app` и `meter-reader`, `restart: unless-stopped` и healthcheck.
  Оба порта слушают только `127.0.0.1` (8080 и 8000). Наружу смотрит только nginx.
- `compose.override.yaml` лежит только на сервере (в `.git/info/exclude`): он переводит порт `app` на `127.0.0.1:8080`.
- `.env` (права 600) лежит только на сервере. В нём `BOT_TOKEN`, `BOT_USERNAME`, `YC_API_KEY`, `YC_FOLDER_ID`,
  `MINIAPP_ORIGINS`, `ARSHIN_MODE=live` и прочее. `RECOGNIZER_URL` там НЕ задаётся: адрес meter-reader боту даёт compose.
- Данные: `./data` (том контейнера app, владелец uid 10001) — `bot.db` (SQLite), `photos/`, `backups/`.
- Мини-приложение отдаёт nginx с этого же домена, API тот же origin (`<meta name="api-base" content="">`).
  GitHub Pages для этого сервера не нужен.
- nginx и сертификаты настроены отдельно, их не трогаем.
- До Docker бот работал как systemd-сервисы `max-bot` и `meter-reader` из `.venv` (uv). Они выключены и удалены,
  копии юнитов, `.env` и БД на момент перехода лежат в `/home/user/backups/`. Каталог `.venv` больше не нужен.

## Обновить до новой версии

```bash
ssh user@193.124.129.60
cd ~/max-smart-city-webapp
bash deploy/update.sh                 # git pull, бэкап БД, пересборка контейнеров, статика, проверки
bash deploy/update.sh <ветка>         # выложить другую ветку (например main)
bash deploy/update.sh --static        # поменялся только app/web/static — без пересборки
```

Скрипт спросит пароль sudo один раз: он нужен, чтобы скопировать статику в `/var/www/maxsmartcity.ru`.
В конце должны быть `{"ok":true}` (бот и снаружи), `{"status":"ok",…}` (meter-reader) и в логах
`MAX bot: id=423938205` и `polling started`.

Вручную то же самое:
```bash
git pull && docker compose up -d --build --wait
sudo cp app/web/static/{index.html,app.js,styles.css} /var/www/maxsmartcity.ru/
```

Один токен — один процесс: пока работает сервер, локально бота не запускать (`docker compose stop app`),
иначе апдейты MAX разделятся между двумя экземплярами.

## Повседневное

```bash
docker compose ps                                  # состояние, должно быть (healthy)
docker compose logs -f app                         # логи бота; meter-reader — docker compose logs -f meter-reader
docker compose restart app                         # перезапуск без пересборки (например, после правки .env)
docker compose up -d                               # после правки .env: пересоздать контейнеры с новым окружением
docker compose exec app python -c "import sqlite3;print(sqlite3.connect('/app/data/bot.db').execute('select count(*) from users').fetchone())"
curl -s -F image=@фото.jpg localhost:8000/recognize   # что распознаёт сервис
```

Откат на прошлую версию: `git checkout <коммит> && docker compose up -d --build --wait` (данные в `./data` не трогаются).
Вернуть БД из бэкапа: `docker compose stop app`, затем
`sudo cp data/backups/bot-<время>.db data/bot.db && sudo rm -f data/bot.db-wal data/bot.db-shm`, `docker compose start app`.
