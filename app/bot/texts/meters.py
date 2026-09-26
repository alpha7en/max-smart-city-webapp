"""Тексты управления счётчиками: карточка счётчика и удаление (бот и API мини-приложения).

Текстовые значения вынесены в yaml/meters.yaml.
"""
from app.bot.texts.loader import load_texts

_D = load_texts("meters.yaml")

# --- «Мои счётчики» ---
LIST_HINT: str = _D["LIST_HINT"]

# --- Карточка счётчика ---
CARD_ADDRESS: str = _D["CARD_ADDRESS"]
CARD_SERIAL: str = _D["CARD_SERIAL"]
CARD_NO_SERIAL: str = _D["CARD_NO_SERIAL"]
CARD_LAST: str = _D["CARD_LAST"]
CARD_NO_READINGS: str = _D["CARD_NO_READINGS"]
CARD_VERIF: str = _D["CARD_VERIF"]
CARD_NO_VERIF: str = _D["CARD_NO_VERIF"]
VERIF_SOURCE: dict[str, str] = _D["VERIF_SOURCE"]
VERIF_SOURCE_NONE: str = _D["VERIF_SOURCE_NONE"]
NOTE_ADDRESS: str = _D["NOTE_ADDRESS"]
NOTE_MODEL: str = _D["NOTE_MODEL"]
NOTE_ARSHIN_DEMO: str = _D["NOTE_ARSHIN_DEMO"]
NOTE: str = _D["NOTE"]

BTN_SUBMIT: str = _D["BTN_SUBMIT"]
BTN_DELETE: str = _D["BTN_DELETE"]
SUBMIT_FOR: str = _D["SUBMIT_FOR"]

# --- Удаление ---
ASK_DELETE: str = _D["ASK_DELETE"]
BTN_DELETE_YES: str = _D["BTN_DELETE_YES"]
DELETED: str = _D["DELETED"]
GONE: str = _D["GONE"]
NOT_OWNER: str = _D["NOT_OWNER"]
SUBMIT_GONE: str = _D["SUBMIT_GONE"]

# --- API мини-приложения ---
API_NOT_OWNER: str = _D["API_NOT_OWNER"]
