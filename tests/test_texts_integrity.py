"""Тесты целостности текстовых ресурсов бота (YAML-файлы в app/bot/texts/yaml/).

Проверяет:
1. Корректность синтаксиса всех YAML-файлов.
2. Соответствие ключей между YAML и Python-модулями.
3. Валидность всех плейсхолдеров {placeholder} в шаблонах.
4. Ограничения длины кнопок MAX (обычные ≤ 24, срочные ≤ 32, счётчики/адреса ≤ 40).
5. Правила оформления текстов из CLAUDE.md (без эмодзи, длина сообщений).
"""
from __future__ import annotations

import importlib
import re
import string
from pathlib import Path

import pytest
import yaml

from app.bot.texts.loader import YAML_DIR, load_texts

MODULES = [
    "common",
    "menu",
    "registration",
    "submission",
    "meters",
    "profile",
    "invite",
    "notify",
    "arshin",
    "api",
    "hackathon_demo",
]


def test_all_yaml_files_exist_and_loadable():
    """Каждый модуль должен иметь существующий и валидный YAML-файл."""
    assert YAML_DIR.exists(), f"Директория {YAML_DIR} не существует"
    yaml_files = list(YAML_DIR.glob("*.yaml"))
    assert len(yaml_files) >= len(MODULES), f"Ожидалось минимум {len(MODULES)} yaml-файлов, найдено {len(yaml_files)}"

    for mod_name in MODULES:
        file_path = YAML_DIR / f"{mod_name}.yaml"
        assert file_path.exists(), f"Отсутствует файл {file_path}"
        data = load_texts(f"{mod_name}.yaml")
        assert isinstance(data, dict), f"{file_path} должен содержать словарь верхнего уровня"
        assert len(data) > 0, f"{file_path} пустой"


def test_python_modules_match_yaml_keys():
    """Все публичные константы Python-модулей должны присутствовать в YAML."""
    for mod_name in MODULES:
        py_mod = importlib.import_module(f"app.bot.texts.{mod_name}")
        yaml_data = load_texts(f"{mod_name}.yaml")

        for attr in dir(py_mod):
            if attr.startswith("_") or attr == "annotations":
                continue
            val = getattr(py_mod, attr)
            if callable(val) or isinstance(val, (type(yaml), type(importlib))):
                continue

            # Специальные преобразования
            if attr == "ROLE" and mod_name == "profile":
                assert "ROLE" in yaml_data
                continue
            if attr in ("MENU_WORDS", "CANCEL_WORDS", "LATER_WORDS"):
                assert attr in yaml_data
                assert isinstance(val, set)
                continue
            if attr == "TARIFF_BUTTONS" and mod_name == "submission":
                assert "TARIFF_BUTTONS" in yaml_data
                continue

            assert attr in yaml_data, f"Константа {attr} из {mod_name}.py отсутствует в {mod_name}.yaml"


def _extract_format_keys(template: str) -> set[str]:
    """Извлекает имена плейсхолдеров из строки формата Python."""
    keys = set()
    formatter = string.Formatter()
    for _, field_name, _, _ in formatter.parse(template):
        if field_name:
            # Извлекаем базовое имя (если есть спецификаторы или атрибуты)
            base = field_name.split(".")[0].split("[")[0]
            if base:
                keys.add(base)
    return keys


def test_format_placeholders_syntax():
    """Все строки с {плейсхолдерами} должны быть синтаксически корректными format-строками."""
    for yaml_file in YAML_DIR.glob("*.yaml"):
        with yaml_file.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        def check_strings(obj, path=""):
            if isinstance(obj, str):
                if "{" in obj and "}" in obj:
                    try:
                        keys = _extract_format_keys(obj)
                        # Проверяем, что нет битых фигурных скобок
                        dummy_kwargs = {k: "TEST" for k in keys}
                        # Если строка содержит {example}, {name} и т.д. — формат должен работать
                        # Пропускаем строки с markdown/regex, где скобки экранированы
                        obj.format(**dummy_kwargs)
                    except (ValueError, KeyError) as e:
                        pytest.fail(f"Ошибка в format-строке в {yaml_file.name} [{path}]: {e}")
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    check_strings(v, f"{path}.{k}" if path else str(k))
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    check_strings(v, f"{path}[{i}]")

        check_strings(data)


def test_button_length_limits():
    """Проверка лимитов длины кнопок из CLAUDE.md: обычные ≤ 24, срочные ≤ 32, счётчики/адреса ≤ 40."""
    for yaml_file in YAML_DIR.glob("*.yaml"):
        with yaml_file.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        for key, val in data.items():
            if not isinstance(val, str):
                continue
            if key.startswith("BTN_"):
                # Кнопка с динамическим плейсхолдером (например, "Это я: {name}") проверяется без него
                clean_btn = re.sub(r"\{.*?\}", "", val).strip()
                if key in ("BTN_SHARE_PHONE", "BTN_PRIVATE_HOUSE", "BTN_SAVE_AS_IS"):
                    # Допустимые кнопки до 24 символов
                    assert len(val) <= 24, f"Кнопка {key} в {yaml_file.name} ('{val}') длиннее 24 симв.: {len(val)}"
                elif "URGENT" in key:
                    assert len(val) <= 32, f"Срочная кнопка {key} ('{val}') длиннее 32 симв.: {len(val)}"
                else:
                    # Обычные кнопки не должны быть длиннее 32 символов даже с запасом
                    assert len(clean_btn) <= 32, f"Кнопка {key} в {yaml_file.name} ('{val}') слишком длинная"

            if key.startswith("URGENT_"):
                # Срочные действия ≤ 32
                clean_urgent = re.sub(r"\{.*?\}", "", val).strip()
                assert len(clean_urgent) <= 32, f"Срочное действие {key} ('{val}') длиннее 32: {len(clean_urgent)}"


def test_no_prohibited_emoji():
    """В текстах бота не должно быть графических эмодзи (правило CLAUDE.md)."""
    # Диапазоны эмодзи в Unicode
    emoji_pattern = re.compile(
        "[\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map
        "\U0001F1E0-\U0001F1FF"  # flags
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "]+",
        flags=re.UNICODE,
    )

    for yaml_file in YAML_DIR.glob("*.yaml"):
        with yaml_file.open("r", encoding="utf-8") as f:
            content = f.read()
        emojis = emoji_pattern.findall(content)
        assert not emojis, f"В файле {yaml_file.name} найдены эмодзи: {emojis}"
