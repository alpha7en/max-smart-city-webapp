"""Загрузчик текстовых ресурсов бота из YAML-файлов.

Тексты хранятся в папке yaml/ рядом с этим файлом:
app/bot/texts/yaml/<section>.yaml

Кэширует загруженные файлы при первом обращении, чтобы не читать диск повторно.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

YAML_DIR = Path(__file__).parent / "yaml"


@lru_cache(maxsize=None)
def load_texts(filename: str) -> dict[str, Any]:
    """Загружает словарь текстов из YAML-файла в app/bot/texts/yaml/."""
    file_path = YAML_DIR / filename
    if not file_path.exists():
        raise FileNotFoundError(f"Файл с текстами не найден: {file_path}")
    with file_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def clear_cache() -> None:
    """Сбрасывает кэш текстов (используется в тестах и при перезагрузке)."""
    load_texts.cache_clear()
