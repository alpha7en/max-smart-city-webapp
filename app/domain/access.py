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
