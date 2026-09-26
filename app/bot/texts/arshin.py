"""Тексты проверки поверки по ФГИС «Аршин» (app/arshin_service.py, подача, меню, уведомления).

Текстовые значения вынесены в yaml/arshin.yaml.
"""
from app.bot.texts.loader import load_texts

_D = load_texts("arshin.yaml")

# --- После подачи ({date} — 18.10.2029) ---
FOUND: str = _D["FOUND"]
EXPIRED: str = _D["EXPIRED"]
UNFIT: str = _D["UNFIT"]
NOT_FOUND: str = _D["NOT_FOUND"]
DEMO_NOTE: str = _D["DEMO_NOTE"]

# --- «Это ваш счётчик?» ---
PICK_ONE: str = _D["PICK_ONE"]
PICK_MANY: str = _D["PICK_MANY"]
PICK_DEMO: str = _D["PICK_DEMO"]
PICK_OR_DATE: str = _D["PICK_OR_DATE"]
OPTION: str = _D["OPTION"]
OPTION_UNFIT: str = _D["OPTION_UNFIT"]
BTN_NONE: str = _D["BTN_NONE"]
BTN_NOT_MINE: str = _D["BTN_NOT_MINE"]
PICKED: str = _D["PICKED"]
PICKED_UNFIT: str = _D["PICKED_UNFIT"]
BTN_CARD: str = _D["BTN_CARD"]

# --- Меню, дашборд, уведомления ---
SOURCE: str = _D["SOURCE"]
SOURCE_DEMO: str = _D["SOURCE_DEMO"]
METERS_SOURCE: str = _D["METERS_SOURCE"]
ANTIFRAUD: str = _D["ANTIFRAUD"]
ANTIFRAUD_MANY: str = _D["ANTIFRAUD_MANY"]
NOTICE_SOURCE: str = _D["NOTICE_SOURCE"]
NOTICE_ANTIFRAUD: str = _D["NOTICE_ANTIFRAUD"]

# --- /demo ---
BTN_DEMO: str = _D["BTN_DEMO"]
DEMO_NO_SERIAL: str = _D["DEMO_NO_SERIAL"]
DEMO_OFF: str = _D["DEMO_OFF"]
DEMO_UNAVAILABLE: str = _D["DEMO_UNAVAILABLE"]
