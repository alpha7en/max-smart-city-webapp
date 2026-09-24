"""Часы приложения: местное время (TZ, по умолчанию МСК). В тестах подменяются через set_now()."""
from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _zone() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("TZ", "").strip() or "Europe/Moscow")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/Moscow")


TZ = _zone()
_fixed: datetime | None = None


def now() -> datetime:
    """Текущее время, aware, в зоне TZ."""
    return _fixed if _fixed is not None else datetime.now(TZ)


def today() -> date:
    return now().date()


def set_now(value: datetime | None) -> None:
    """Зафиксировать время (naive трактуется как TZ); None — вернуть реальные часы."""
    global _fixed
    if value is not None and value.tzinfo is None:
        value = value.replace(tzinfo=TZ)
    _fixed = value.astimezone(TZ) if value is not None else None
