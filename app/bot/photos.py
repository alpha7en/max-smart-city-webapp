"""Временное хранение фото счётчиков: скачать, удалить, подчистить просроченные."""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from app.integrations.max_api import MaxApi, MaxApiError
from app.repo import Repo

log = logging.getLogger(__name__)
PHOTO_TTL = timedelta(minutes=30)         # фото подачи
PENDING_PHOTO_TTL = timedelta(hours=24)   # фото, присланное до/во время регистрации
MAX_BYTES = 10 * 1024 * 1024


class PhotoError(Exception):
    """Фото не удалось скачать или сохранить."""


async def download_to_tmp(api: MaxApi, repo: Repo, photos_dir: Path, user_id: int, url: str,
                          now: datetime, ttl: timedelta = PHOTO_TTL) -> str:
    """Скачивает фото (≤10 МБ, image/*) в photos_dir/{uuid}.jpg с правами 0600. → photo_id."""
    if not url:
        raise PhotoError("no url")
    try:
        data, _ = await api.download(url, max_bytes=MAX_BYTES)
    except MaxApiError as e:
        raise PhotoError(str(e)) from e
    if not data:
        raise PhotoError("empty file")
    photo_id = uuid.uuid4().hex
    photos_dir.mkdir(parents=True, exist_ok=True)
    path = photos_dir / f"{photo_id}.jpg"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    await repo.add_photo(photo_id, user_id, str(path), now, now + ttl)
    return photo_id


async def photo_path(repo: Repo, photo_id: str | None) -> str | None:
    """Путь к файлу, если фото ещё на месте."""
    if not photo_id:
        return None
    row = await repo.get_photo(photo_id)
    return row["path"] if row and Path(row["path"]).exists() else None


async def delete_photo(repo: Repo, photo_id: str | None) -> None:
    if not photo_id:
        return
    row = await repo.delete_photo_row(photo_id)
    if row:
        Path(row["path"]).unlink(missing_ok=True)


async def sweep(repo: Repo, now: datetime) -> int:
    """Удаляет просроченные фото (файлы и записи)."""
    rows = await repo.expired_photos(now)
    for row in rows:
        await delete_photo(repo, row["id"])
    if rows:
        log.info("swept %d expired photos", len(rows))
    return len(rows)
