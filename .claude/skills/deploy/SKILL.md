---
name: deploy
description: Чек-лист деплоя бота на VPS. Docker compose как постоянный процесс, .env, HTTPS для API мини-приложения через Caddy с доменом, переменная MINIAPP_API_BASE для GitHub Pages, MINIAPP_ORIGINS, проверка /api/health и CORS. URL мини-приложения в MAX не меняется. Вызывать на фразы «задеплой на сервер», «выложи бота», «подними на VPS», «обнови сервер».
---

# deploy: задеплой на сервер

Неизменно: мини-приложение живёт по адресу `https://alpha7en.github.io/max-smart-city-webapp/`. Этот URL закреплён
в MAX организаторами, его НЕ МЕНЯТЬ ни в MAX, ни в business.max.ru, ни в репо. Меняется только адрес
бэкенда, в который ходит мини-приложение (`MINIAPP_API_BASE`).
Запущен один экземпляр бота: перед стартом на сервере останови локальный (`docker compose stop app`).
Иначе апдейты разделятся между двумя процессами.

## 0. Что спросить у владельца
- SSH-доступ к VPS (`user@host`). Хостинг лучше в РФ: MAX API за рубежом может быть недоступен.
- Домен или поддомен для API (например `api.<домен>`) с A-записью на IP сервера. Без домена нет
  сертификата, а без HTTPS мини-приложение не достучится до бэкенда. Временные туннели не годятся.
- Какую ветку выкладывать и можно ли переключить GitHub Pages на GitHub Actions (шаг 4).

## 1. Сервер
```bash
ssh user@host 'docker --version && docker compose version' || echo "поставить Docker Engine + compose plugin"
ssh user@host 'sudo systemctl enable --now docker'         # переживает перезагрузку
ssh user@host 'git clone https://github.com/alpha7en/max-smart-city-webapp.git && cd max-smart-city-webapp && git checkout <ветка>'
scp .env user@host:max-smart-city-webapp/.env && ssh user@host 'chmod 600 max-smart-city-webapp/.env'
```
В `.env` на сервере должно быть: `BOT_TOKEN` (не печатать), `BOT_USERNAME=t226_hakaton_max_bot`,
`MINIAPP_ORIGINS=https://alpha7en.github.io`, `DEV_AUTH=false`, `DEMO_MODE=true` (для жюри), `DADATA_API_KEY` (если есть).
Для реального распознавания ещё `YC_API_KEY`, `YC_FOLDER_ID` (контейнер `meter-reader` поднимается всегда,
адрес боту даёт compose; `RECOGNIZER_URL` и `COMPOSE_PROFILES` из старого `.env` убрать); порт 8000 проброшен только на 127.0.0.1, наружу его не открывать.
Проверка: `curl -s localhost:8000/health` → `{"status":"ok",…}`. `MAX_TEST_USER_ID` серверу не нужен. Сверь без вывода значений: `grep -cE '^(BOT_TOKEN|BOT_USERNAME|MINIAPP_ORIGINS)=..*' .env` → 3.

## 2. Приложение как постоянный процесс
В `compose.yaml` уже есть `restart: unless-stopped` и healthcheck. Порт 8080 наружу не открываем
(Docker обходит ufw), поэтому на сервере рядом кладём `compose.override.yaml`, его не коммитить:
```yaml
services:
  app:
    ports: !override
      - "127.0.0.1:8080:8080"
```
```bash
docker compose up -d --build && docker compose ps && curl -s localhost:8080/api/health   # {"ok":true}
docker compose logs --since 2m app | grep -E 'MAX bot: id=|polling started|ERROR'
curl -s localhost:8000/health          # если заданы ключи YC: {"status":"ok",…}
```

## 3. HTTPS через Caddy (сертификат Let's Encrypt автоматически)
```bash
sudo apt install -y caddy
echo 'api.<домен> {
    reverse_proxy 127.0.0.1:8080
}' | sudo tee /etc/caddy/Caddyfile && sudo systemctl reload caddy
sudo ufw allow 22,80,443/tcp    # если ufw включён
```
Проверка с любой машины:
```bash
curl -s https://api.<домен>/api/health
curl -si -X OPTIONS https://api.<домен>/api/me -H 'Origin: https://alpha7en.github.io' \
  -H 'Access-Control-Request-Method: GET' -H 'Access-Control-Request-Headers: X-Max-Init-Data' | grep -i access-control-allow-origin
```
Ожидается `{"ok":true}` и `access-control-allow-origin: https://alpha7en.github.io`.
`curl https://api.<домен>/api/me` без initData → 401 `{"code":"unauthorized",…}`: так и должно быть.

## 4. Мини-приложение на GitHub Pages → этот бэкенд
1. Переменная репозитория: `gh variable set MINIAPP_API_BASE --body https://api.<домен>`
   (или Settings → Secrets and variables → Actions → Variables). Без `/` в конце.
2. Pages: Settings → Pages → Source = GitHub Actions. Адрес `alpha7en.github.io/max-smart-city-webapp/` при этом не меняется.
   Ветку `gh-pages` не удалять, пока новая сборка не проверена.
3. Сборка: `gh workflow run pages.yml --ref main` (workflow должен быть в main: окружение github-pages обычно
   пускает только ветку по умолчанию). Сама сборка запускается и при push в main, если изменён `app/web/static/**`.
   Статус: `gh run list --workflow pages.yml -L 3`.
4. Проверка: `curl -s https://alpha7en.github.io/max-smart-city-webapp/ | grep api-base` показывает `https://api.<домен>`.

## 5. Живая проверка после деплоя
- `.venv/bin/python -m tools.live_smoke` локально (без `--listen`: слушание отнимет апдейты у серверного бота).
- `docs/LIVE_CHECKLIST.md`: разделы 1 и 3 бегло, раздел 4 (мини-приложение, initData, start_param) полностью (скилл `live-scenario`).
- В логах сервера после открытия мини-приложения запросы `/api/me` отвечают 200, а не 401.

## 6. Обслуживание
```bash
ssh user@host 'cd max-smart-city-webapp && git pull && docker compose up -d --build'            # обновить
ssh user@host 'cd max-smart-city-webapp && docker compose logs --since 1h app'                    # логи → скилл fix-from-logs
ssh user@host 'cd max-smart-city-webapp && sqlite3 data/bot.db ".backup data/backup-$(date +%F).db"'  # бэкап БД
```
Откат: `git checkout <прошлый коммит> && docker compose up -d --build`. Данные в `./data` сохраняются.
