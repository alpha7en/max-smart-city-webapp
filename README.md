# Единый бот ЖКХ для MAX

Временный README: полный текст по пунктам ТЗ будет позже.

## Запуск

```bash
cp .env.example .env   # укажите BOT_TOKEN
docker compose up --build
```

Проверка: `curl http://localhost:8080/api/health` → `{"ok":true}`.
Без `BOT_TOKEN` работает только веб-часть: API и статика мини-приложения на `/app`.

## Разработка

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pytest
uvicorn app.main:app --reload --port 8080
```
