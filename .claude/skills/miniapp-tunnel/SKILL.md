---
name: miniapp-tunnel
description: Временный HTTPS-туннель (cloudflared) к локальному боту, чтобы мини-приложение на GitHub Pages заработало без хостинга. Проверяет, что бот запущен и .env готов, поднимает туннель, проверяет /api/health и CORS, прописывает адрес в MINIAPP_API_BASE и пересобирает Pages. Вызывать на фразы «подними туннель», «мини-приложение пишет сервис недоступен», «мини-приложение пишет сервер не подключён», «протестировать мини-приложение без хостинга».
---

# miniapp-tunnel: мини-приложение без хостинга

Мини-приложение живёт по адресу `https://alpha7en.github.io/max-smart-city-webapp/` (закреплён в MAX, НЕ МЕНЯТЬ).
Бэкенд для него задаёт переменная репозитория `MINIAPP_API_BASE`: пустая — экран «Сервер мини-приложения
не подключён». Туннель даёт временный HTTPS-адрес к `localhost:8080`. Это режим проверки, не продакшен:
адрес меняется при каждом перезапуске туннеля, постоянный вариант — скилл `deploy`.

## 1. Бот запущен
```bash
curl -s localhost:8080/api/health          # {"ok":true}
```
Нет ответа — запусти (`docker compose up -d --build` или `uvicorn app.main:app --env-file .env --port 8080`)
и проверь ещё раз. Держи запущенным один экземпляр бота на токен.

## 2. .env готов (значения токена не печатать)
```bash
grep -q '^BOT_TOKEN=.\+' .env && echo "BOT_TOKEN задан" || echo "BOT_TOKEN пуст"
grep -E '^(MINIAPP_ORIGINS|DEV_AUTH)=' .env
```
Нужно: `MINIAPP_ORIGINS=https://alpha7en.github.io` (без `/` в конце, можно через запятую с другими)
и `DEV_AUTH=false` (в MAX приходит настоящий initData). Поправил `.env` — перезапусти бота
(`docker compose up -d` пересоздаст контейнер) и повтори шаг 1.

## 3. Туннель
Нет `cloudflared`: macOS — `brew install cloudflared`; Linux — пакет с
https://github.com/cloudflare/cloudflared/releases (`.deb`: `sudo dpkg -i cloudflared-linux-amd64.deb`,
или бинарник `cloudflared-linux-amd64` → `chmod +x` и в `PATH`).

Запусти в фоне и вылови адрес:
```bash
mkdir -p data && cloudflared tunnel --url http://localhost:8080 > data/tunnel.log 2>&1 &
for i in $(seq 30); do URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' data/tunnel.log | head -1); [ -n "$URL" ] && break; sleep 1; done
echo "$URL"
```
Адрес не появился за 30 с — покажи владельцу хвост `data/tunnel.log`.

## 4. Проверка туннеля
```bash
curl -s "$URL/api/health"                                                   # {"ok":true}
curl -si -X OPTIONS "$URL/api/me" -H 'Origin: https://alpha7en.github.io' \
  -H 'Access-Control-Request-Method: GET' -H 'Access-Control-Request-Headers: X-Max-Init-Data' \
  | grep -i access-control-allow-origin                                     # https://alpha7en.github.io
```
Нет `access-control-allow-origin` — в `.env` бота нет `MINIAPP_ORIGINS` (шаг 2). Первые секунды после
старта туннель может отвечать 502/530: подожди и повтори.

## 5. Адрес → GitHub Pages
Есть `gh` (и `gh auth status` зелёный):
```bash
gh variable set MINIAPP_API_BASE --body "$URL"
gh workflow run pages.yml --ref main
sleep 5; RUN=$(gh run list --workflow pages.yml -L 1 --json databaseId -q '.[0].databaseId')
gh run watch "$RUN" --exit-status
curl -s "https://alpha7en.github.io/max-smart-city-webapp/?nocache=$(date +%s)" | grep 'name="api-base"'   # содержит $URL
```
CDN Pages может отдавать старую версию ещё минуту-две — повтори `curl`.

Нет `gh` — дай владельцу точные клики:
1. GitHub → репозиторий → Settings → Secrets and variables → Actions → вкладка Variables →
   `MINIAPP_API_BASE` (New repository variable или Edit) → значение `<URL>` без `/` в конце → Save.
2. Actions → «Mini app → GitHub Pages» → Run workflow → ветка `main` → Run workflow. Дождаться зелёной галочки.
3. Проверить: открыть исходный код страницы `https://alpha7en.github.io/max-smart-city-webapp/` —
   в `<meta name="api-base">` стоит этот адрес.

## 6. Проверка в MAX
Открой бота в MAX → «Мини-приложение»: должна открыться главная со счётчиками. В логах бота запросы
`/api/me` отвечают 200 (401 — initData не принят: `DEV_AUTH`, токен, раздел 4 `docs/LIVE_CHECKLIST.md`).

## Предупредить владельца
- Адрес `*.trycloudflare.com` меняется при каждом перезапуске туннеля — после перезапуска повтори шаги 3–5.
  Пока туннель выключен, мини-приложение покажет «Нет связи с сервером».
- Остановить: `pkill -f 'cloudflared tunnel'`. Для жюри нужен постоянный бэкенд (скилл `deploy`).
- Посмотреть мини-приложение в браузере без MAX: `DEV_AUTH=true` в `.env`, перезапуск бота, затем
  `http://localhost:8080/app/?dev_user=<свой MAX user_id>` (id человека, например 5273381, а не id бота).
  После проверки верни `DEV_AUTH=false`.
