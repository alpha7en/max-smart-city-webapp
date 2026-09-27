"""«Поделиться доступом» — общая логика бота (flows/sharing.py) и API мини-приложения (web/api.py).

Модель (слова владельца): делятся АДРЕСОМ — одним или несколькими сразу, не счётчиком и не профилем.
- Собственник адреса (role='owner'; модель прав — первый зарегистрировавший) создаёт одноразовую ссылку
  max.ru/<бот>?start=inv_<token> на 7 дней, не больше INVITE_LIMIT действующих.
- Принявший получает access='granted', role='tenant' (в интерфейсе — «доступ от Анны И.», не «арендатор»),
  granted_by — собственник. Свой адрес пропускаем, pending/denied → granted.
- Получатель может только выйти из общего доступа («Выйти»: строка user_addresses удаляется), менять адрес — нет.
  Собственник может «закрыть доступ» (access='denied'). Показания всегда остаются за адресом.
- Удаление аккаунта: получатель — у собственника всё остаётся, ему уведомление; собственник — адрес
  переходит к первому получателю с доступом (repo.delete_user_data), ему уведомление.
"""
from __future__ import annotations

import logging
import secrets
import string
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from app import clock
from app.bot import keyboards as K
from app.bot.texts import common as C
from app.bot.texts import profile as PT
from app.bot.texts import sharing as T
from app.bot.texts.fmt import day_month, esc, month_name, short_date, with_notes
from app.domain.meters import current_period
from app.domain.people import past, short_name, short_name_gen
from app.repo import Repo, Row, is_demo_person

log = logging.getLogger(__name__)
INVITE_TTL = timedelta(days=7)
INVITE_LIMIT = 5            # действующих ссылок на собственника
TOKEN_LEN = 20              # [a-z0-9]: без «_», чтобы inv_new_<aid> и inv_<token> не путались
_ALPHABET = string.ascii_lowercase + string.digits
SHARED = "sh_list"          # g|sh_list — «Общий доступ» (flows/sharing.py)
DECISION_KIND = "access_decision"  # notifications.kind: решение по запросу доступа доставлено, ключ «uid:aid»


def new_token() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(TOKEN_LEN))


def invite_url(bot: str, token: str) -> str:
    return f"https://max.ru/{bot}?start=inv_{token}"


def local_date(stamp: str | None) -> date | None:
    """'2026-10-26 09:00:00' (UTC в БД) → дата по МСК."""
    if not stamp:
        return None
    return datetime.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).astimezone(clock.TZ).date()


# === Имена и подписи ===

def sharer(ua: Row) -> str | None:
    """ФИО того, кто открыл доступ к адресу (granted_by), иначе нынешнего собственника."""
    return ua.get("granted_by_name") or ua.get("owner_name")


def role_text(ua: Row) -> str | None:
    """«доступ от Анны И.» для общего адреса с доступом; None — свой адрес или доступа нет."""
    if ua["role"] == "owner" or ua["access"] != "granted" or not sharer(ua):
        return None
    return T.ROLE_SHARED.format(by=esc(short_name_gen(sharer(ua))))


def addresses_text(labels: Sequence[str]) -> str:
    """«адресу Арбат 47к1, кв 32» / «адресам: Арбат 47к1, кв 32; Тверская 1, кв 5» (без разметки)."""
    if len(labels) == 1:
        return T.ADDR_ONE.format(address=labels[0])
    return T.ADDR_MANY.format(addresses="; ".join(labels))


def member_status(m: Row, today: date) -> str:
    """«показания 12.10» (за текущий месяц подавал) / «за октябрь показаний нет» / «ждёт одобрения»."""
    if m["access"] == "pending":
        return T.STATUS_PENDING
    period = current_period(today)
    if m.get("last_period") == period and (when := local_date(m.get("last_at"))):
        return T.STATUS_SUBMITTED.format(date=short_date(when))
    return T.STATUS_NOT_SUBMITTED.format(month=month_name(period))


# === Приглашения ===

async def owned(repo: Repo, user_id: int) -> list[Row]:
    return [a for a in await repo.user_addresses(user_id) if a["role"] == "owner"]


async def create_invite(repo: Repo, owner_id: int, address_ids: Sequence[int],
                        now: datetime) -> tuple[Row | None, str | None]:
    """→ (приглашение с address_ids, None) или (None, 'empty' | 'not_owner' | 'limit')."""
    ids = list(dict.fromkeys(address_ids))
    if not ids:
        return None, "empty"
    mine = {a["id"] for a in await owned(repo, owner_id)}
    if not set(ids) <= mine:
        return None, "not_owner"
    invite_id = await repo.create_invite(new_token(), owner_id, ids, now, now + INVITE_TTL, INVITE_LIMIT)
    if invite_id is None:
        return None, "limit"
    return await repo.get_invite_by_id(invite_id), None


def invite_state(inv: Row | None, now: datetime) -> str | None:
    """None — действует; 'unknown' | 'used' | 'cancelled' | 'expired'."""
    if inv is None:
        return "unknown"
    if inv["used_at"]:
        return "used"
    if inv["cancelled_at"]:
        return "cancelled"
    if datetime.strptime(inv["expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC) <= now:
        return "expired"
    return None


async def check_invite(repo: Repo, token: str, now: datetime) -> tuple[Row | None, str | None]:
    """(приглашение, ошибка): ошибка None — действует, иначе как invite_state."""
    token = (token or "").strip().lower()
    ok = token.isascii() and token.isalnum() and len(token) <= 64
    inv = await repo.get_invite(token) if ok else None
    return inv, invite_state(inv, now)


async def invite_addresses(repo: Repo, inv: Row) -> list[Row]:
    """Адреса приглашения глазами собственника (его подписи); адреса, которые уже не его, пропускаем."""
    rows = [await repo.user_address(inv["owner_user_id"], aid) for aid in inv["address_ids"]]
    return [r for r in rows if r and r["role"] == "owner"]


async def invite_texts(repo: Repo, inv: Row, bot: str) -> dict:
    """Тексты ссылки: owner, labels, addresses («адресу …»), until, url, link (для пересылки), share_text."""
    owner = await repo.get_user_by_id(inv["owner_user_id"])
    labels = [a["label"] for a in await invite_addresses(repo, inv)]
    until = local_date(inv["expires_at"])
    kw = {"owner": short_name(owner["full_name"] if owner else None), "addresses": addresses_text(labels),
          "until": day_month(until)}
    url = invite_url(bot, inv["token"])
    return {**kw, "labels": labels, "url": url, "link": T.LINK.format(url=url, **kw),
            "share_text": T.SHARE_TEXT.format(**kw)}


# === Уведомления в чат ===

def menu_kb(*rows) -> dict:
    return K.kb(*rows, [K.gbtn(C.BTN_MENU, "menu")])


async def notify(api, user: Row | None, text: str, keyboard: dict | None = None) -> bool:
    """Сообщение другому пользователю (собственнику/получателю). Сбой MAX только логируем."""
    if api is None or not user or is_demo_person(user):
        return False
    try:
        await api.send(text, user_id=user["max_user_id"], keyboard=keyboard)
        return True
    except Exception as e:  # noqa: BLE001 — уведомление не должно ронять основное действие
        log.warning("share notice to user %s failed: %s", user["id"], e)
        return False


async def notify_closed(repo: Repo, api, member_id: int, address_id: int) -> bool:
    """Получателю: «Собственник закрыл вам доступ к адресу …»."""
    member = await repo.user_address(member_id, address_id)
    if member is None:
        return False
    return await notify(api, await repo.get_user_by_id(member_id),
                        T.TENANT_REVOKED.format(label=esc(member["full_text"])),
                        menu_kb([K.gbtn(T.BTN_SHARED, SHARED)]))


async def notify_removed(repo: Repo, api, user: Row, removed: Row) -> bool:
    """Собственнику: «Пётр С. вышел из общего доступа к адресу …» (removed — удалённая связь, как user_address)."""
    owner = await repo.get_user_by_id(removed["owner_id"]) if removed.get("owner_id") else None
    owner_ua = await repo.user_address(owner["id"], removed["id"]) if owner else None
    if owner_ua is None:
        return False
    return await notify(api, owner, T.OWNER_REMOVED.format(name=esc(short_name(user["full_name"])),
                                                           left=past(user["full_name"], "вышел"),
                                                           label=esc(owner_ua["full_text"])),
                        menu_kb([K.gbtn(T.BTN_SHARED, SHARED)]))


async def close_access(repo: Repo, api, owner_id: int, member_id: int, address_id: int) -> str:
    """Собственник закрывает доступ (access=denied), получателю — сообщение.
    → 'ok' | 'not_owner' | 'self' | 'not_found' | 'already'."""
    ua = await repo.user_address(owner_id, address_id)
    if ua is None or ua["role"] != "owner":
        return "not_owner"
    if member_id == owner_id:
        return "self"
    member = await repo.user_address(member_id, address_id)
    if member is None or member["role"] == "owner":
        return "not_found"
    if member["access"] == "denied":
        return "already"
    await repo.set_access(member_id, address_id, "denied")
    await notify_closed(repo, api, member_id, address_id)
    return "ok"


async def remove_received(repo: Repo, api, user: Row, address_id: int) -> tuple[str, Row | None]:
    """Получатель убирает адрес у себя; собственнику — сообщение, если доступ был открыт.
    → ('ok' | 'notified' | 'own' | 'not_found', удалённая связь)."""
    ua = await repo.user_address(user["id"], address_id)
    if ua is not None and ua["role"] == "owner":
        return "own", ua
    removed = await repo.remove_address(user["id"], address_id) if ua else None
    if removed is None:
        return "not_found", None
    sent = removed["access"] == "granted" and await notify_removed(repo, api, user, removed)
    return ("notified" if sent else "ok"), removed


async def after_account_deleted(repo: Repo, api, full_name: str | None, info: Row) -> None:
    """После repo.delete_user_data: собственникам — «человек удалил профиль», новым собственникам — «адрес ваш»."""
    by_owner: dict[int, list[int]] = {}
    for r in info.get("received", []):
        by_owner.setdefault(r["owner_id"], []).append(r["address_id"])
    for owner_id, aids in by_owner.items():
        owner = await repo.get_user_by_id(owner_id)
        rows = [await repo.user_address(owner_id, aid) for aid in aids] if owner else []
        labels = [r["label"] for r in rows if r]
        if labels:
            await notify(api, owner, T.MEMBER_DELETED.format(name=esc(short_name(full_name)),
                                                             addresses=esc(addresses_text(labels))),
                         menu_kb([K.gbtn(T.BTN_SHARED, SHARED)]))
    for p in info.get("promoted", []):
        ua = await repo.user_address(p["user_id"], p["address_id"])
        if ua:
            await notify(api, await repo.get_user_by_id(p["user_id"]),
                         with_notes(T.PROMOTED.format(label=esc(ua["full_text"])), PT.RIGHTS_MODEL),
                         menu_kb([K.gbtn(T.BTN_SHARE, "share")]))
