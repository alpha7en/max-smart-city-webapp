"""Подключение к SQLite (WAL, foreign_keys) и миграции по PRAGMA user_version."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

SCHEMA_VERSION = 1
SCHEMA_FILE = Path(__file__).with_name("schema.sql")
# Миграции: версия → SQL. Версия 1 — schema.sql целиком.
MIGRATIONS: dict[int, str] = {}


def ts(dt: datetime) -> str:
    """Время в формате SQLite datetime('now'): 'YYYY-MM-DD HH:MM:SS' (UTC)."""
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


async def connect(path: str | Path) -> aiosqlite.Connection:
    """Открывает БД в режиме autocommit; транзакции — явно через BEGIN в repo."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(path), isolation_level=None)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.execute("PRAGMA busy_timeout=5000")
    await db.execute("PRAGMA synchronous=NORMAL")
    return db


async def migrate(db: aiosqlite.Connection) -> int:
    async with db.execute("PRAGMA user_version") as cur:
        version = (await cur.fetchone())[0]
    if version == 0:
        await db.executescript("BEGIN;\n" + SCHEMA_FILE.read_text("utf-8") + "\nCOMMIT;")
        version = 1
        await db.execute(f"PRAGMA user_version={version}")
    while version < SCHEMA_VERSION:
        version += 1
        await db.executescript("BEGIN;\n" + MIGRATIONS[version] + "\nCOMMIT;")
        await db.execute(f"PRAGMA user_version={version}")
    return version


async def init_db(path: str | Path) -> aiosqlite.Connection:
    db = await connect(path)
    await migrate(db)
    return db
