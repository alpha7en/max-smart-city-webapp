"""Тексты бота: YAML-файлы в app/bot/texts/yaml/ загружаются, и все ключи Python-модулей в них есть."""
from __future__ import annotations

import importlib

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
