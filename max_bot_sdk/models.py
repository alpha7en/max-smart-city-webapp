"""
Data models and typed structures for MAX Bot API.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

@dataclass
class User:
    user_id: int
    first_name: str = ""
    last_name: Optional[str] = None
    username: Optional[str] = None
    is_bot: bool = False
    name: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "User":
        uid = data.get("user_id") or data.get("id") or 0
        return cls(
            user_id=int(uid),
            first_name=data.get("first_name") or data.get("name") or "",
            last_name=data.get("last_name"),
            username=data.get("username"),
            is_bot=data.get("is_bot", False),
            name=data.get("name")
        )

@dataclass
class Recipient:
    chat_type: str = "dialog"
    chat_id: Optional[int] = None
    user_id: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Recipient":
        return cls(
            chat_type=data.get("chat_type", "dialog"),
            chat_id=data.get("chat_id"),
            user_id=data.get("user_id")
        )

@dataclass
class MessageBody:
    mid: str = ""
    seq: int = 0
    text: str = ""
    attachments: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MessageBody":
        return cls(
            mid=data.get("mid", ""),
            seq=data.get("seq", 0),
            text=data.get("text", "") or "",
            attachments=data.get("attachments", []) or []
        )

@dataclass
class Message:
    sender: User
    recipient: Recipient
    body: MessageBody
    timestamp: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        return cls(
            sender=User.from_dict(data.get("sender", {})),
            recipient=Recipient.from_dict(data.get("recipient", {})),
            body=MessageBody.from_dict(data.get("body", {})),
            timestamp=data.get("timestamp", 0)
        )

@dataclass
class Callback:
    callback_id: str
    payload: str
    user: Optional[User] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Callback":
        user = User.from_dict(data.get("user", {})) if "user" in data else None
        return cls(
            callback_id=str(data.get("callback_id") or data.get("id") or ""),
            payload=str(data.get("payload", "")),
            user=user
        )

@dataclass
class Update:
    update_type: str
    timestamp: int = 0
    message: Optional[Message] = None
    callback: Optional[Callback] = None
    user: Optional[User] = None
    chat_id: Optional[int] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Update":
        upd_type = data.get("update_type", "")
        if not upd_type:
            if "message" in data:
                upd_type = "message_created"
            elif "callback" in data:
                upd_type = "message_callback"

        msg = Message.from_dict(data.get("message", {})) if "message" in data else None
        cb = Callback.from_dict(data.get("callback", {})) if "callback" in data else None
        u = User.from_dict(data.get("user", {})) if "user" in data else None

        return cls(
            update_type=upd_type,
            timestamp=data.get("timestamp", 0),
            message=msg,
            callback=cb,
            user=u,
            chat_id=data.get("chat_id"),
            raw=data
        )

    @property
    def sender_user_id(self) -> Optional[int]:
        """Универсальное извлечение ID пользователя независимо от типа события"""
        if self.message and self.message.sender.user_id:
            return self.message.sender.user_id
        if self.callback and self.callback.user and self.callback.user.user_id:
            return self.callback.user.user_id
        if self.user and self.user.user_id:
            return self.user.user_id
        return None

    @property
    def effective_chat_id(self) -> Optional[int]:
        """Универсальное извлечение ID чата"""
        if self.chat_id:
            return self.chat_id
        if self.message and self.message.recipient.chat_id:
            return self.message.recipient.chat_id
        return None

    @property
    def text(self) -> str:
        if self.message:
            return self.message.body.text
        return ""

    @property
    def has_image(self) -> bool:
        if not self.message or not self.message.body:
            return False
        return any(att.get("type") in ["image", "photo", "file"] for att in (self.message.body.attachments or []))

    @property
    def has_contact(self) -> bool:
        if not self.message or not self.message.body:
            return False
        return any(att.get("type") == "contact" for att in (self.message.body.attachments or []))

    @property
    def contact(self) -> Optional[Dict[str, Any]]:
        if not self.message or not self.message.body:
            return None
        for att in (self.message.body.attachments or []):
            if att.get("type") == "contact":
                return att
        return None

