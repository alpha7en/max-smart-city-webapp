#!/usr/bin/env bash
# Обновление на сервере maxsmartcity.ru. Запуск из каталога репозитория:
#   bash deploy/update.sh            # текущая ветка
#   bash deploy/update.sh <ветка>    # переключиться на другую ветку
#   bash deploy/update.sh --static   # только статика мини-приложения, без пересборки контейнеров
# Что делает: git pull → бэкап БД → docker compose up --build → статика мини-приложения в /var/www → проверки.
# Порядок и устройство сервера: docs/DEPLOY_SERVER.md.
set -euo pipefail
cd "$(dirname "$0")/.."

WWW=/var/www/maxsmartcity.ru   # корень сайта в nginx (конфиг nginx не трогаем)
STATIC_ONLY=false
BRANCH=""
for a in "$@"; do
  case "$a" in
    --static) STATIC_ONLY=true ;;
    *) BRANCH="$a" ;;
  esac
done

step() { printf '\n== %s\n' "$*"; }

step "Код"
git fetch -q origin
if [ -n "$BRANCH" ]; then
  git checkout -q "$BRANCH" 2>/dev/null || git checkout -q -b "$BRANCH" --track "origin/$BRANCH"
fi
git pull -q --ff-only
echo "$(git rev-parse --abbrev-ref HEAD) @ $(git log --oneline -1)"

if ! $STATIC_ONLY; then
  step "Бэкап БД → data/backups/"
  if docker compose ps --status running -q app | grep -q .; then
    docker compose exec -T app python -c "
import sqlite3, time, pathlib
d = pathlib.Path('/app/data/backups'); d.mkdir(exist_ok=True)
dst = d / time.strftime('bot-%Y%m%d-%H%M%S.db')
sqlite3.connect('/app/data/bot.db').backup(sqlite3.connect(dst))
old = sorted(d.glob('bot-*.db'))[:-10]   # храним 10 последних
for p in old: p.unlink()
print(dst.name)"
  else
    echo "контейнер app не запущен, бэкап пропущен"
  fi

  step "Контейнеры"
  docker compose up -d --build --wait --wait-timeout 120
  docker image prune -f >/dev/null
fi

step "Мини-приложение → $WWW"
tmp=$(mktemp -d)
find app/web/static -maxdepth 1 -type f ! -name '.gitkeep' -exec cp {} "$tmp"/ \;
# сбрасываем кэш браузера/MAX: ?v=dev → ?v=<коммит>
sed -i "s/?v=dev/?v=$(git rev-parse --short HEAD)/g" "$tmp/index.html"
sudo install -o www-data -g www-data -m 644 "$tmp"/* "$WWW"/
rm -rf "$tmp"
ls -l "$WWW"

step "Проверки"
docker compose ps
curl -fsS localhost:8080/api/health && echo "  ← бот/API"
curl -fsS localhost:8000/health && echo "  ← meter-reader"
curl -fsS https://maxsmartcity.ru/api/health && echo "  ← снаружи через nginx"
docker compose logs --since 3m app | grep -E 'MAX bot: id=|polling started|ERROR' || true
