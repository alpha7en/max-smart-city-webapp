"""Подключение к SQLite (WAL, foreign_keys) и миграции по PRAGMA user_version."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

SCHEMA_VERSION = 3
SCHEMA_FILE = Path(__file__).with_name("schema.sql")
# Миграции: версия → SQL. Версия 1 — schema.sql целиком; новые таблицы — только здесь (и новой БД тоже).
MIGRATIONS: dict[int, str] = {
    # 2: одноразовые приглашения жильцов от собственника (ссылка max.ru/<бот>?start=inv_<token>).
    2: """
CREATE TABLE invites(
  token TEXT PRIMARY KEY, address_id INTEGER NOT NULL REFERENCES addresses(id),
  owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  used_by INTEGER REFERENCES users(id) ON DELETE SET NULL, used_at TEXT);
CREATE INDEX invites_address ON invites(address_id);
""",
    # 3: поверка по данным ФГИС «Аршин»: verification_source='arshin' (CHECK меняется только пересборкой
    # таблицы — порядок SQLite «12 шагов», внешние ключи выключены в migrate()), поля записи и кэш ответов.
    3: """
CREATE TABLE meters_new(
  id INTEGER PRIMARY KEY, address_id INTEGER NOT NULL REFERENCES addresses(id),
  type TEXT NOT NULL CHECK(type IN('cold_water','hot_water','electricity','gas','heat')),
  tariffs INTEGER NOT NULL DEFAULT 1 CHECK(tariffs BETWEEN 1 AND 3),
  serial TEXT, serial_norm TEXT,
  verification_due TEXT, verification_source TEXT CHECK(verification_source IN('user','model','arshin')),
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL, active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  arshin_vri_id TEXT, arshin_checked_at TEXT, arshin_mit_title TEXT);
INSERT INTO meters_new(id, address_id, type, tariffs, serial, serial_norm, verification_due, verification_source,
                       created_by, active, created_at)
  SELECT id, address_id, type, tariffs, serial, serial_norm, verification_due, verification_source,
         created_by, active, created_at FROM meters;
DROP TABLE meters;
ALTER TABLE meters_new RENAME TO meters;
CREATE UNIQUE INDEX meters_serial ON meters(address_id, serial_norm) WHERE serial_norm IS NOT NULL;
CREATE TABLE arshin_cache(key TEXT PRIMARY KEY, payload TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL CHECK(status IN('found','none','error')), fetched_at TEXT NOT NULL);
""",
}


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
    if version < SCHEMA_VERSION:
        await db.execute("PRAGMA foreign_keys=OFF")  # пересборка таблиц (v3); вне транзакции, иначе не действует
        while version < SCHEMA_VERSION:
            version += 1
            await db.executescript("BEGIN;\n" + MIGRATIONS[version] + "\nCOMMIT;")
            await db.execute(f"PRAGMA user_version={version}")
        async with db.execute("PRAGMA foreign_key_check") as cur:
            if bad := await cur.fetchall():
                raise RuntimeError(f"foreign key check failed after migration: {[tuple(r) for r in bad][:5]}")
        await db.execute("PRAGMA foreign_keys=ON")
    return version


async def init_db(path: str | Path) -> aiosqlite.Connection:
    db = await connect(path)
    await migrate(db)
    return db
