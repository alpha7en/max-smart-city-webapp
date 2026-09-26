"""ТОЛЬКО ДЛЯ ХАКАТОНА: тексты демо-профиля для проверяющих (app/bot/flows/hackathon_demo.py).

Текстовые значения вынесены в yaml/hackathon_demo.yaml.
"""
from app.bot.texts.loader import load_texts

_D = load_texts("hackathon_demo.yaml")

OFFER: str = _D["OFFER"]
BTN_YES: str = _D["BTN_YES"]
BTN_NO: str = _D["BTN_NO"]
CREATED: str = _D["CREATED"]
ALREADY: str = _D["ALREADY"]
