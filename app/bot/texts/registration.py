"""Тексты регистрации (SPEC §5.5, §8 эталоны 1–4) и общих шагов адреса.

Текстовые значения вынесены в yaml/registration.yaml.
"""
from app.bot.texts.loader import load_texts

_D = load_texts("registration.yaml")

# --- Имя ---
ASK_NAME: str = _D["ASK_NAME"]
ASK_NAME_AGAIN: str = _D["ASK_NAME_AGAIN"]
NAME_EXAMPLE: str = _D["NAME_EXAMPLE"]
NAME_ERRORS: dict[str, str] = _D["NAME_ERRORS"]
NAME_ERROR_DEFAULT: str = _D["NAME_ERROR_DEFAULT"]
BTN_ITS_ME: str = _D["BTN_ITS_ME"]
BTN_KEEP: str = _D["BTN_KEEP"]

# --- Телефон ---
ASK_PHONE: str = _D["ASK_PHONE"]
PHONE_AGAIN: str = _D["PHONE_AGAIN"]
PHONE_ERROR: str = _D["PHONE_ERROR"]
PHONE_FOREIGN: str = _D["PHONE_FOREIGN"]
NOT_YOUR_CONTACT: str = _D["NOT_YOUR_CONTACT"]
CONTACT_NO_PHONE: str = _D["CONTACT_NO_PHONE"]
FROM_MAX: str = _D["FROM_MAX"]
BTN_SHARE_PHONE: str = _D["BTN_SHARE_PHONE"]

# --- Адрес (регистрация и профиль) ---
ADDRESS_EXAMPLE: str = _D["ADDRESS_EXAMPLE"]
ASK_ADDRESS: str = _D["ASK_ADDRESS"]
ADDRESS_RETRY: str = _D["ADDRESS_RETRY"]
ADDRESS_NOT_FOUND: str = _D["ADDRESS_NOT_FOUND"]
ADDRESS_NOT_FOUND_ASIS: str = _D["ADDRESS_NOT_FOUND_ASIS"]
ADDRESS_ONE: str = _D["ADDRESS_ONE"]
ADDRESS_MANY: str = _D["ADDRESS_MANY"]
LOCAL_NOTE: str = _D["LOCAL_NOTE"]
FLAT_WARNING: str = _D["FLAT_WARNING"]
ASK_FLAT: str = _D["ASK_FLAT"]
FLAT_ERROR: str = _D["FLAT_ERROR"]
BTN_YES: str = _D["BTN_YES"]
BTN_NO_OTHER: str = _D["BTN_NO_OTHER"]
BTN_NOT_MINE: str = _D["BTN_NOT_MINE"]
BTN_PRIVATE_HOUSE: str = _D["BTN_PRIVATE_HOUSE"]
BTN_ASIS: str = _D["BTN_ASIS"]
BTN_FIX: str = _D["BTN_FIX"]

# --- Подтверждение ---
CONFIRM: str = _D["CONFIRM"]
SAVED: str = _D["SAVED"]
DONE: str = _D["DONE"]
DONE_SHORT: str = _D["DONE_SHORT"]
DONE_PHOTO: str = _D["DONE_PHOTO"]
DONE_PHOTO_EXPIRED: str = _D["DONE_PHOTO_EXPIRED"]
BTN_ALL_OK: str = _D["BTN_ALL_OK"]
BTN_EDIT_NAME: str = _D["BTN_EDIT_NAME"]
BTN_EDIT_PHONE: str = _D["BTN_EDIT_PHONE"]
BTN_EDIT_ADDRESS: str = _D["BTN_EDIT_ADDRESS"]
