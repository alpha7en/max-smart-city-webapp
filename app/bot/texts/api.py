"""Тексты API мини-приложения (/api/*): сообщения ошибок и подтверждение подачи в чат.

Текстовые значения вынесены в yaml/api.yaml.
"""
from app.bot.texts.loader import load_texts

_D = load_texts("api.yaml")

MSG: dict[str, str] = _D["MSG"]
CHAT_SAVED: str = _D["CHAT_SAVED"]
CHAT_FLAGGED: str = _D["CHAT_FLAGGED"]
CHAT_MOCK: str = _D["CHAT_MOCK"]
BTN_MORE: str = _D["BTN_MORE"]
UK_MOCK: str = _D["UK_MOCK"]
