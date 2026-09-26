"""Тексты приглашения жильца собственником и управления доступом по адресу.

Текстовые значения вынесены в yaml/invite.yaml.
"""
from app.bot.texts.loader import load_texts

_D = load_texts("invite.yaml")

# --- Собственник создаёт приглашение ---
INVITE_LINK: str = _D["INVITE_LINK"]
INVITE_CREATED: str = _D["INVITE_CREATED"]
INVITE_LIMIT: str = _D["INVITE_LIMIT"]
PICK_ADDRESS: str = _D["PICK_ADDRESS"]
OWNER_ONLY: str = _D["OWNER_ONLY"]
NO_USERNAME: str = _D["NO_USERNAME"]

# --- Приглашённый открыл ссылку ---
INVALID: dict[str, str] = _D["INVALID"]
SELF: str = _D["SELF"]
ALREADY: str = _D["ALREADY"]
OFFER: str = _D["OFFER"]
ACCEPTED: str = _D["ACCEPTED"]
DECLINED: str = _D["DECLINED"]
OWNER_ACCEPTED: str = _D["OWNER_ACCEPTED"]
BTN_ACCEPT: str = _D["BTN_ACCEPT"]
BTN_DECLINE: str = _D["BTN_DECLINE"]

# --- Регистрация по приглашению ---
REG_INVITED: str = _D["REG_INVITED"]
REG_KEPT: str = _D["REG_KEPT"]
ASK_INVITE_ADDRESS: str = _D["ASK_INVITE_ADDRESS"]
BTN_THIS_ADDRESS: str = _D["BTN_THIS_ADDRESS"]
BTN_OTHER_ADDRESS: str = _D["BTN_OTHER_ADDRESS"]
REG_ACCEPTED: str = _D["REG_ACCEPTED"]
REG_NOT_APPLIED: str = _D["REG_NOT_APPLIED"]

# --- Профиль собственника: кто имеет доступ ---
ACCESS_LINE: str = _D["ACCESS_LINE"]
ONLY_YOU: str = _D["ONLY_YOU"]
WAITS: str = _D["WAITS"]
BTN_INVITE: str = _D["BTN_INVITE"]
BTN_MANAGE: str = _D["BTN_MANAGE"]
MEMBERS: str = _D["MEMBERS"]
MEMBERS_EMPTY: str = _D["MEMBERS_EMPTY"]
STATUS: dict[str, str] = _D["STATUS"]
BTN_REVOKE: str = _D["BTN_REVOKE"]
BTN_REVOKE_N: str = _D["BTN_REVOKE_N"]
BTN_ALLOW: str = _D["BTN_ALLOW"]
BTN_ALLOW_N: str = _D["BTN_ALLOW_N"]

# --- Отзыв ---
REVOKE_ASK: str = _D["REVOKE_ASK"]
BTN_REVOKE_YES: str = _D["BTN_REVOKE_YES"]
BTN_REVOKE_NO: str = _D["BTN_REVOKE_NO"]
REVOKED: str = _D["REVOKED"]
REVOKE_SELF: str = _D["REVOKE_SELF"]
REVOKE_GONE: str = _D["REVOKE_GONE"]
REVOKE_ALREADY: str = _D["REVOKE_ALREADY"]
TENANT_REVOKED: str = _D["TENANT_REVOKED"]
