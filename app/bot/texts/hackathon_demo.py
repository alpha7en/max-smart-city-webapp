"""ТОЛЬКО ДЛЯ ХАКАТОНА: тексты тестового профиля для проверяющих (app/bot/flows/hackathon_demo.py).

Текстовые значения вынесены в yaml/hackathon_demo.yaml.
"""
from app.bot.texts.loader import load_texts

_D = load_texts("hackathon_demo.yaml")

COMMAND_DESCRIPTION: str = _D["COMMAND_DESCRIPTION"]  # в списке команд бота в MAX
CREATED: str = _D["CREATED"]                          # над меню после создания
HAS_PROFILE: str = _D["HAS_PROFILE"]                  # профиль уже есть — сначала удалить
