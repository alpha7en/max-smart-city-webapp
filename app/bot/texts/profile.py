"""Тексты профиля, «нет прав» (SPEC §5.8) и запроса доступа у собственника.

Текстовые значения вынесены в yaml/profile.yaml.
"""
from app.bot.texts.loader import load_texts

_D = load_texts("profile.yaml")

# --- Профиль ---
PROFILE: str = _D["PROFILE"]
NO_ADDRESSES: str = _D["NO_ADDRESSES"]
ROLE: dict[tuple[str, str], str] = {
    ("owner", "granted"): _D["ROLE"]["owner_granted"],
    ("tenant", "granted"): _D["ROLE"]["tenant_granted"],
    ("tenant", "pending"): _D["ROLE"]["tenant_pending"],
    ("tenant", "denied"): _D["ROLE"]["tenant_denied"],
}
BTN_EDIT_PHONE: str = _D["BTN_EDIT_PHONE"]
BTN_ADD_ADDRESS: str = _D["BTN_ADD_ADDRESS"]
BTN_DELETE: str = _D["BTN_DELETE"]
BTN_PROFILE: str = _D["BTN_PROFILE"]

ASK_PHONE: str = _D["ASK_PHONE"]
PHONE_SAVED: str = _D["PHONE_SAVED"]
ADDRESS_EXAMPLE: str = _D["ADDRESS_EXAMPLE"]

ASK_ADDRESS: str = _D["ASK_ADDRESS"]
ADDRESS_SAVED: str = _D["ADDRESS_SAVED"]
ADDRESS_DUP: str = _D["ADDRESS_DUP"]

DELETE_ASK: str = _D["DELETE_ASK"]
DELETED: str = _D["DELETED"]
DELETE_KEPT: str = _D["DELETE_KEPT"]
BTN_DELETE_YES: str = _D["BTN_DELETE_YES"]
BTN_DELETE_NO: str = _D["BTN_DELETE_NO"]
BTN_START_OVER: str = _D["BTN_START_OVER"]

# --- Нет прав (§5.8) ---
NO_ACCESS: str = _D["NO_ACCESS"]
RIGHTS_MODEL: str = _D["RIGHTS_MODEL"]
NO_ACCESS_DENIED: str = _D["NO_ACCESS_DENIED"]
BTN_REQUEST: str = _D["BTN_REQUEST"]
BTN_DEMO_GRANT: str = _D["BTN_DEMO_GRANT"]
DEMO_GRANTED: str = _D["DEMO_GRANTED"]
DEMO_GRANTED_NOTE: str = _D["DEMO_GRANTED_NOTE"]
BTN_REQUEST_FOR: str = _D["BTN_REQUEST_FOR"]

# --- Запрос доступа ---
REQUEST_SENT: str = _D["REQUEST_SENT"]
REQUEST_DUP: str = _D["REQUEST_DUP"]
REQUEST_FAILED: str = _D["REQUEST_FAILED"]
ACCESS_ALREADY: str = _D["ACCESS_ALREADY"]
ACCESS_CLAIMED: str = _D["ACCESS_CLAIMED"]
ACCESS_UNKNOWN: str = _D["ACCESS_UNKNOWN"]

OWNER_REQUEST: str = _D["OWNER_REQUEST"]
OWNER_GRANTED: str = _D["OWNER_GRANTED"]
OWNER_DENIED: str = _D["OWNER_DENIED"]
OWNER_ONLY: str = _D["OWNER_ONLY"]
DECIDED: dict[str, str] = _D["DECIDED"]
REQUEST_GONE: str = _D["REQUEST_GONE"]
BTN_ALLOW: str = _D["BTN_ALLOW"]
BTN_DENY: str = _D["BTN_DENY"]

TENANT_GRANTED: str = _D["TENANT_GRANTED"]
TENANT_DENIED: str = _D["TENANT_DENIED"]
BTN_SUBMIT: str = _D["BTN_SUBMIT"]
