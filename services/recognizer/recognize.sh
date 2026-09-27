#!/usr/bin/env bash
# Отправляет фото счётчика в сервис и печатает результат.
# Использование: ./recognize.sh photo.jpg [photo2.jpg ...]
# Адрес сервиса можно переопределить: METER_API=http://host:8000 ./recognize.sh photo.jpg
set -euo pipefail

API="${METER_API:-http://localhost:8000}"

if [ "$#" -eq 0 ]; then
    echo "Использование: $0 фото.jpg [фото2.jpg ...]" >&2
    exit 1
fi

for photo in "$@"; do
    if [ ! -f "$photo" ]; then
        echo "Файл не найден: $photo" >&2
        continue
    fi
    echo "== $photo"
    response=$(curl -sS --max-time 180 -w '\n%{http_code}' -F "image=@${photo}" "${API}/recognize") || {
        echo "Сервис недоступен по адресу ${API}" >&2
        exit 1
    }
    code="${response##*$'\n'}"
    body="${response%$'\n'*}"
    if [ "$code" != "200" ]; then
        echo "Ошибка $code: $body" >&2
        continue
    fi
    python3 - "$body" <<'EOF'
import json
import sys

r = json.loads(sys.argv[1])
types = {
    "hot_water": "горячая вода",
    "cold_water": "холодная вода",
    "electricity": "электричество",
    "gas": "газ",
    "unknown": "не определён",
}
units = {"m3": "м³", "kWh": "кВт·ч"}
rows = [
    ("Тип", types.get(r["meter_type"], r["meter_type"])),
    ("Показание", f'{r["reading"]} {units.get(r["unit"], r["unit"] or "")}'.strip()
        if r["reading"] is not None else "не распознано"),
    ("На счётчике", r["reading_text"]),
    ("Тариф", r["tariff"]),
    ("Производитель", r["brand"]),
    ("Модель", r["model"]),
    ("Серийный №", r["serial_number"]),
]
for name, value in rows:
    if value:
        print(f"  {name:<14} {value}")
EOF
done
