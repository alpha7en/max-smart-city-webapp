"""Событие бота из сырого update MAX (схема: max-messenger/api-schema, Update)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.domain.people import parse_vcf_phone

Kind = Literal["start", "text", "photo", "contact", "location", "callback", "other"]
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic")
START_TEXTS = {"/start"}


@dataclass
class Event:
    kind: Kind
    user_id: int                          # MAX user_id того, кто действует
    chat_id: int | None
    text: str | None = None               # текст или подпись к фото
    start_payload: str | None = None
    photo_url: str | None = None
    photo_count: int = 0
    contact_phone: str | None = None      # +7XXXXXXXXXX из vcf_info
    contact_owner_id: int | None = None   # max_info.user_id — чей это контакт
    contact_vcf: str | None = None        # сырой vcf_info (для проверки hash)
    contact_hash: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    callback_id: str | None = None
    payload: str | None = None
    message_mid: str | None = None        # для callback — mid сообщения с кнопкой
    first_name: str | None = None
    last_name: str | None = None
    update_id: str = ""                   # ключ дедупа: mid или callback_id


def _is_image_file(att: dict) -> bool:
    name = str(att.get("filename") or "").lower()
    return name.endswith(IMAGE_EXT)


def _from_message(upd: dict) -> Event | None:
    msg = upd.get("message") or {}
    sender = msg.get("sender") or {}
    recipient = msg.get("recipient") or {}
    if not sender.get("user_id") or sender.get("is_bot"):
        return None
    if recipient.get("chat_type") not in (None, "dialog"):  # только личные диалоги
        return None
    body = msg.get("body") or {}
    text = body.get("text")
    atts: list[dict[str, Any]] = body.get("attachments") or []
    ev = Event(
        kind="other",
        user_id=int(sender["user_id"]),
        chat_id=recipient.get("chat_id"),
        text=text,
        first_name=sender.get("first_name"),
        last_name=sender.get("last_name"),
        update_id=str(body.get("mid") or ""),
    )
    photos = [
        a for a in atts
        if a.get("type") == "image" or (a.get("type") == "file" and _is_image_file(a))
    ]
    contact = next((a for a in atts if a.get("type") == "contact"), None)
    location = next((a for a in atts if a.get("type") == "location"), None)
    if contact:
        p = contact.get("payload") or {}
        ev.kind = "contact"
        ev.contact_vcf = p.get("vcf_info")
        ev.contact_hash = p.get("hash")
        ev.contact_phone = parse_vcf_phone(ev.contact_vcf)
        owner = (p.get("max_info") or {}).get("user_id")
        ev.contact_owner_id = int(owner) if owner is not None else None
    elif location:
        ev.kind = "location"
        ev.latitude, ev.longitude = location.get("latitude"), location.get("longitude")
    elif photos:
        ev.kind = "photo"
        ev.photo_count = len(photos)
        ev.photo_url = (photos[0].get("payload") or {}).get("url")
    elif atts:
        ev.kind = "other"
    elif text is not None and text.strip():
        cmd, _, rest = text.strip().partition(" ")
        if cmd.lower() in START_TEXTS:
            ev.kind = "start"
            ev.start_payload = rest.strip() or None
        else:
            ev.kind = "text"
    return ev


def parse_update(upd: dict) -> Event | None:
    """Update MAX → Event. Неинтересные типы и сообщения не из диалога → None."""
    t = upd.get("update_type")
    if t == "message_created":
        return _from_message(upd)
    if t == "message_callback":
        cb = upd.get("callback") or {}
        user = cb.get("user") or {}  # кто нажал; message.sender — это бот
        if not user.get("user_id"):
            return None
        msg = upd.get("message") or {}
        return Event(
            kind="callback",
            user_id=int(user["user_id"]),
            chat_id=(msg.get("recipient") or {}).get("chat_id"),
            callback_id=cb.get("callback_id"),
            payload=cb.get("payload"),
            message_mid=(msg.get("body") or {}).get("mid"),
            first_name=user.get("first_name"),
            last_name=user.get("last_name"),
            update_id=str(cb.get("callback_id") or ""),
        )
    if t == "bot_started":
        user = upd.get("user") or {}
        if not user.get("user_id"):
            return None
        return Event(
            kind="start",
            user_id=int(user["user_id"]),
            chat_id=upd.get("chat_id"),
            start_payload=upd.get("payload"),
            first_name=user.get("first_name"),
            last_name=user.get("last_name"),
            update_id=f"start:{user['user_id']}:{upd.get('timestamp', '')}",
        )
    return None
