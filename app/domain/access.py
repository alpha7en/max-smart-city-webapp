"""Модель прав на подачу показаний (МОДЕЛЬ для MVP).

В реальности право подтверждают данные УК / ЕГРН / ГИС ЖКХ. Здесь: первый пользователь
по адресу — собственник с доступом, следующие — арендаторы, ждущие разрешения собственника.
"""
from __future__ import annotations

from typing import Literal

Role = Literal["owner", "tenant"]
Access = Literal["granted", "pending", "denied"]


def decide_role(existing_owner: bool) -> tuple[Role, Access]:
    return ("tenant", "pending") if existing_owner else ("owner", "granted")


def can_submit(access: str | None) -> bool:
    return access == "granted"


def meter_delete_denial(role: str | None, access: str | None, others_granted: int) -> str | None:
    """Кто может удалить счётчик: None — можно; 'no_access' — нет доступа к адресу;
    'not_owner' — по адресу есть другие жильцы с доступом, а удаляет не собственник."""
    if access != "granted":
        return "no_access"
    if role != "owner" and others_granted > 0:
        return "not_owner"
    return None
