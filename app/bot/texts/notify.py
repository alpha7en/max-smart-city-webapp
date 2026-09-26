"""Тексты уведомлений и /demo (поток S4).

Текстовые значения вынесены в yaml/notify.yaml.
"""
from app.bot.texts.loader import load_texts

_D = load_texts("notify.yaml")

# --- Подача показаний ---
SUBMIT_OPEN: str = _D["SUBMIT_OPEN"]
SUBMIT_NEXT: str = _D["SUBMIT_NEXT"]
SUBMIT_LAST: str = _D["SUBMIT_LAST"]
SUBMIT_NOT_DONE: str = _D["SUBMIT_NOT_DONE"]
SUBMIT_HINT: str = _D["SUBMIT_HINT"]
ALREADY_SUBMITTED: str = _D["ALREADY_SUBMITTED"]

# --- Поверка ---
VERIFICATION_SOON: str = _D["VERIFICATION_SOON"]
VERIFICATION_TODAY: str = _D["VERIFICATION_TODAY"]
VERIFICATION_OVERDUE: str = _D["VERIFICATION_OVERDUE"]
VERIFICATION_WHY: str = _D["VERIFICATION_WHY"]
VERIFICATION_MODEL: str = _D["VERIFICATION_MODEL"]
VERIFICATION_UPDATED: str = _D["VERIFICATION_UPDATED"]
METER_GONE: str = _D["METER_GONE"]

# --- Счёт (демо) ---
BILL_DUE: str = _D["BILL_DUE"]
BILL_OVERDUE: str = _D["BILL_OVERDUE"]
BILL_DEMO: str = _D["BILL_DEMO"]
ALREADY_PAID: str = _D["ALREADY_PAID"]

# --- Кнопки уведомлений ---
BTN_SUBMIT: str = _D["BTN_SUBMIT"]
BTN_VERIFY: str = _D["BTN_VERIFY"]
BTN_PAY: str = _D["BTN_PAY"]

# --- /demo ---
DEMO: str = _D["DEMO"]
DEMO_ADD_METER_FIRST: str = _D["DEMO_ADD_METER_FIRST"]
DEMO_NO_BILLS: str = _D["DEMO_NO_BILLS"]
BTN_DEMO_SUBMIT: str = _D["BTN_DEMO_SUBMIT"]
BTN_DEMO_VERIFY: str = _D["BTN_DEMO_VERIFY"]
BTN_DEMO_BILL: str = _D["BTN_DEMO_BILL"]
