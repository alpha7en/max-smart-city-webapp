"""Все SQL-запросы приложения. Строки возвращаются как dict (ключи = колонки).

Одно соединение aiosqlite на процесс; доступ сериализуется реентерабельным локом (владелец —
текущая asyncio-задача), поэтому `async with repo.tx():` можно вкладывать и вызывать внутри
него любые методы репозитория.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import fields
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from app import clock
from app.db import init_db, ts
from app.domain.access import decide_role
from app.domain.meters import FIELDS, demo_bill, normalize_serial

Row = dict[str, Any]
# Подписи адресов: список адресов пользователя (dict строк addresses) → список label той же длины.
Labeler = Callable[[list[Row]], list[str]]

ADDRESS_COLUMNS = (
    "status", "source", "full_text", "postal_code", "region", "locality", "street", "house", "block",
    "flat", "house_fias_id", "flat_fias_id", "fias_id", "oktmo", "house_cadnum", "geo_lat", "geo_lon",
)


class ReadingExists(Exception):
    """За период уже есть показание, а replace=False."""

    def __init__(self, reading: Row):
        super().__init__(f"reading exists: {reading['id']}")
        self.reading = reading


def values_of(row: Row | None) -> dict[str, int | None]:
    """{'t1','t2','t3'} из строки readings (или last_t1… из user_meters)."""
    if not row:
        return {}
    prefix = "last_" if "last_t1" in row else ""
    return {f: row.get(prefix + f) for f in FIELDS}


def default_labeler(rows: list[Row]) -> list[str]:
    """Подписи через domain.addresses.short_labels (поток S3)."""
    from app.domain.addresses import AddressCandidate, short_labels

    names = {f.name for f in fields(AddressCandidate)}
    cands = [
        AddressCandidate.from_dict({k: v for k, v in r.items() if k in names}) for r in rows
    ]
    return short_labels(cands)


def _now() -> str:
    """Время записи из app.clock (подменяется в тестах) в формате БД — не datetime('now') SQLite."""
    return ts(clock.now())


class Repo:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task | None = None

    @classmethod
    async def open(cls, path: str | Path) -> Repo:
        return cls(await init_db(path))

    async def close(self) -> None:
        await self.db.close()

    # === Базовые помощники ===

    @asynccontextmanager
    async def _use(self, write: bool) -> AsyncIterator[aiosqlite.Connection]:
        task = asyncio.current_task()
        if self._owner is task:  # уже внутри нашей транзакции/чтения
            yield self.db
            return
        async with self._lock:
            self._owner = task
            try:
                if write:
                    await self.db.execute("BEGIN IMMEDIATE")
                try:
                    yield self.db
                except BaseException:
                    if write:
                        await self.db.execute("ROLLBACK")
                    raise
                if write:
                    await self.db.execute("COMMIT")
            finally:
                self._owner = None

    def tx(self):
        """Транзакция: `async with repo.tx(): ...` — всё внутри атомарно."""
        return self._use(write=True)

    async def _one(self, sql: str, params: Sequence = ()) -> Row | None:
        async with self._use(False) as db, db.execute(sql, params) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def _all(self, sql: str, params: Sequence = ()) -> list[Row]:
        async with self._use(False) as db, db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def _exec(self, sql: str, params: Sequence = ()) -> aiosqlite.Cursor:
        async with self._use(True) as db:
            return await db.execute(sql, params)

    # === Пользователи ===

    async def get_user(self, max_user_id: int) -> Row | None:
        return await self._one("SELECT * FROM users WHERE max_user_id=?", (max_user_id,))

    async def get_user_by_id(self, user_id: int) -> Row | None:
        return await self._one("SELECT * FROM users WHERE id=?", (user_id,))

    async def ensure_user(self, max_user_id: int, chat_id: int | None = None) -> Row:
        """Находит или создаёт пользователя; обновляет chat_id, если пришёл новый."""
        async with self.tx():
            user = await self.get_user(max_user_id)
            if user is None:
                await self._exec(
                    "INSERT INTO users(max_user_id, chat_id, created_at) VALUES(?, ?, ?)",
                    (max_user_id, chat_id, _now()),
                )
            elif chat_id and user["chat_id"] != chat_id:
                await self._exec("UPDATE users SET chat_id=? WHERE id=?", (chat_id, user["id"]))
            else:
                return user
            return await self.get_user(max_user_id)

    async def update_user(self, user_id: int, **values: Any) -> None:
        """Обновляет поля full_name, phone, phone_verified, registered_at, chat_id."""
        allowed = {"full_name", "phone", "phone_verified", "registered_at", "chat_id"}
        bad = set(values) - allowed
        if bad:
            raise ValueError(f"unknown user fields: {bad}")
        if values:
            cols = ", ".join(f"{k}=?" for k in values)
            await self._exec(f"UPDATE users SET {cols} WHERE id=?", (*values.values(), user_id))

    async def registered_users(self) -> list[Row]:
        return await self._all("SELECT * FROM users WHERE registered_at IS NOT NULL ORDER BY id")

    async def delete_user_data(self, user_id: int) -> int:
        """Удаляет пользователя: сессию, фото (и файлы), уведомления, связи с адресами.
        Показания и счётчики остаются за адресом (user_id/created_by → NULL). Возвращает число удалённых фото."""
        async with self.tx():
            photos = await self._all("SELECT path FROM photos WHERE user_id=?", (user_id,))
            for sql in (
                "DELETE FROM sessions WHERE user_id=?",
                "DELETE FROM photos WHERE user_id=?",
                "DELETE FROM notifications WHERE user_id=?",
                "DELETE FROM user_addresses WHERE user_id=?",
                "DELETE FROM users WHERE id=?",
            ):
                await self._exec(sql, (user_id,))
        for p in photos:
            Path(p["path"]).unlink(missing_ok=True)
        return len(photos)

    # === Адреса ===

    async def get_address(self, address_id: int) -> Row | None:
        return await self._one("SELECT * FROM addresses WHERE id=?", (address_id,))

    async def find_address(self, norm_key: str) -> Row | None:
        return await self._one("SELECT * FROM addresses WHERE norm_key=?", (norm_key,))

    async def upsert_address(self, address: dict, norm_key: str) -> int:
        """Адрес по norm_key: существующий → его id (данные обновляются, если новые проверены лучше), иначе вставка.
        `address` — AddressCandidate.to_dict()."""
        vals = {c: address.get(c) for c in ADDRESS_COLUMNS}
        vals["status"] = vals["status"] or "unverified"
        vals["source"] = vals["source"] or "local"
        raw = address.get("raw")
        vals["raw_json"] = json.dumps(raw, ensure_ascii=False) if raw is not None else None
        async with self.tx():
            existing = await self.find_address(norm_key)
            if existing:
                if existing["status"] == "unverified" and vals["status"] != "unverified":
                    cols = ", ".join(f"{k}=?" for k in vals)
                    await self._exec(
                        f"UPDATE addresses SET {cols} WHERE id=?", (*vals.values(), existing["id"])
                    )
                return existing["id"]
            cols = ", ".join(["norm_key", "created_at", *vals])
            marks = ", ".join("?" * (len(vals) + 2))
            cur = await self._exec(
                f"INSERT INTO addresses({cols}) VALUES({marks})", (norm_key, _now(), *vals.values())
            )
            return cur.lastrowid

    async def address_owner(self, address_id: int) -> Row | None:
        """Пользователь-собственник адреса (role='owner') или None."""
        return await self._one(
            "SELECT u.* FROM users u JOIN user_addresses ua ON ua.user_id=u.id "
            "WHERE ua.address_id=? AND ua.role='owner' ORDER BY ua.created_at LIMIT 1",
            (address_id,),
        )

    async def user_addresses(self, user_id: int) -> list[Row]:
        """Адреса пользователя: поля addresses + role, access, label, raw_input, linked_at."""
        return await self._all(
            "SELECT a.*, ua.role, ua.access, ua.label, ua.raw_input, ua.created_at AS linked_at "
            "FROM user_addresses ua JOIN addresses a ON a.id=ua.address_id "
            "WHERE ua.user_id=? ORDER BY ua.created_at, a.id",
            (user_id,),
        )

    async def user_address(self, user_id: int, address_id: int) -> Row | None:
        return await self._one(
            "SELECT a.*, ua.role, ua.access, ua.label, ua.raw_input FROM user_addresses ua "
            "JOIN addresses a ON a.id=ua.address_id WHERE ua.user_id=? AND ua.address_id=?",
            (user_id, address_id),
        )

    async def link_address(
        self, user_id: int, address_id: int, raw_input: str | None = None, label: str = ""
    ) -> tuple[str, str]:
        """Привязывает адрес к пользователю по модели прав. Уже привязан → текущие (role, access)."""
        async with self.tx():
            cur = await self.user_address(user_id, address_id)
            if cur:
                return cur["role"], cur["access"]
            role, access = decide_role(await self.address_owner(address_id) is not None)
            await self._exec(
                "INSERT INTO user_addresses(user_id, address_id, role, access, label, raw_input, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (user_id, address_id, role, access, label, raw_input, _now()),
            )
            return role, access

    async def set_access(self, user_id: int, address_id: int, access: str) -> None:
        await self._exec(
            "UPDATE user_addresses SET access=? WHERE user_id=? AND address_id=?",
            (access, user_id, address_id),
        )

    async def relabel(self, user_id: int, labeler: Labeler | None = None) -> dict[int, str]:
        """Пересчитывает короткие подписи всех адресов пользователя. → {address_id: label}."""
        async with self.tx():
            rows = await self.user_addresses(user_id)
            if not rows:
                return {}
            labels = (labeler or default_labeler)(rows)
            for r, label in zip(rows, labels, strict=True):
                if r["label"] != label:
                    await self._exec(
                        "UPDATE user_addresses SET label=? WHERE user_id=? AND address_id=?",
                        (label, user_id, r["id"]),
                    )
            return {r["id"]: label for r, label in zip(rows, labels, strict=True)}

    async def add_user_address(
        self,
        user_id: int,
        address: dict,
        norm_key: str,
        raw_input: str | None,
        today: date,
        labeler: Labeler | None = None,
    ) -> Row:
        """Одна транзакция: upsert адреса, привязка по модели прав, демо-счёт, пересчёт подписей.
        → {address_id, role, access, label}."""
        async with self.tx():
            address_id = await self.upsert_address(address, norm_key)
            role, access = await self.link_address(user_id, address_id, raw_input)
            await self.ensure_demo_bill(address_id, today)
            labels = await self.relabel(user_id, labeler)
        return {"address_id": address_id, "role": role, "access": access, "label": labels[address_id]}

    async def complete_registration(
        self,
        user_id: int,
        *,
        full_name: str,
        phone: str,
        phone_verified: bool,
        address: dict,
        norm_key: str,
        raw_input: str | None,
        now: datetime,
        labeler: Labeler | None = None,
    ) -> Row:
        """«Всё верно» в регистрации — одной транзакцией. → как add_user_address."""
        async with self.tx():
            await self.update_user(
                user_id, full_name=full_name, phone=phone,
                phone_verified=int(phone_verified), registered_at=ts(now),
            )
            return await self.add_user_address(
                user_id, address, norm_key, raw_input, now.date(), labeler
            )

    # === Счётчики ===

    async def get_meter(self, meter_id: int) -> Row | None:
        return await self._one("SELECT * FROM meters WHERE id=?", (meter_id,))

    async def create_meter(
        self,
        address_id: int,
        type: str,
        tariffs: int = 1,
        serial: str | None = None,
        created_by: int | None = None,
        verification_due: str | None = None,
        verification_source: str | None = None,
    ) -> int:
        cur = await self._exec(
            "INSERT INTO meters(address_id, type, tariffs, serial, serial_norm, created_by, "
            "verification_due, verification_source, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (address_id, type, tariffs, serial or None, normalize_serial(serial), created_by,
             verification_due, verification_source, _now()),
        )
        return cur.lastrowid

    async def address_meters(self, address_id: int) -> list[Row]:
        return await self._all(
            "SELECT * FROM meters WHERE address_id=? AND active=1 ORDER BY id", (address_id,)
        )

    async def find_meter_by_serial(self, address_id: int, serial: str) -> Row | None:
        norm = normalize_serial(serial)
        if not norm:
            return None
        return await self._one(
            "SELECT * FROM meters WHERE address_id=? AND serial_norm=? AND active=1",
            (address_id, norm),
        )

    async def set_meter_serial(self, meter_id: int, serial: str | None) -> None:
        await self._exec(
            "UPDATE meters SET serial=?, serial_norm=? WHERE id=?",
            (serial or None, normalize_serial(serial), meter_id),
        )

    async def set_verification(self, meter_id: int, due: str | None, source: str | None) -> None:
        """due — 'YYYY-MM-DD'; source — 'user' | 'model'."""
        await self._exec(
            "UPDATE meters SET verification_due=?, verification_source=? WHERE id=?",
            (due, source, meter_id),
        )

    async def user_meters(self, user_id: int, only_granted: bool = True) -> list[Row]:
        """Счётчики на адресах пользователя: поля meters + address_label, role, access
        + последнее показание: last_id, last_period, last_t1..t3, last_source, last_status, last_created_at."""
        where = "AND ua.access='granted'" if only_granted else ""
        return await self._all(
            "SELECT m.*, ua.label AS address_label, ua.role, ua.access, "
            "r.id AS last_id, r.period AS last_period, r.t1 AS last_t1, r.t2 AS last_t2, r.t3 AS last_t3, "
            "r.source AS last_source, r.status AS last_status, r.created_at AS last_created_at "
            "FROM meters m JOIN user_addresses ua ON ua.address_id=m.address_id AND ua.user_id=? "
            "LEFT JOIN readings r ON r.id=(SELECT id FROM readings WHERE meter_id=m.id "
            "  AND status!='replaced' ORDER BY period DESC, id DESC LIMIT 1) "
            f"WHERE m.active=1 {where} ORDER BY ua.created_at, m.id",
            (user_id,),
        )

    async def user_meter(self, user_id: int, meter_id: int) -> Row | None:
        """Счётчик, если он на адресе пользователя с доступом granted (для проверки прав в API)."""
        for m in await self.user_meters(user_id):
            if m["id"] == meter_id:
                return m
        return None

    # === Показания ===

    async def last_reading(self, meter_id: int, before_period: str | None = None) -> Row | None:
        """Последнее действующее показание (опционально — строго до периода)."""
        cond, params = ("AND period<?", (meter_id, before_period)) if before_period else ("", (meter_id,))
        return await self._one(
            f"SELECT * FROM readings WHERE meter_id=? AND status!='replaced' {cond} "
            "ORDER BY period DESC, id DESC LIMIT 1",
            params,
        )

    async def reading_for_period(self, meter_id: int, period: str) -> Row | None:
        return await self._one(
            "SELECT * FROM readings WHERE meter_id=? AND period=? AND status!='replaced'",
            (meter_id, period),
        )

    async def history(self, meter_id: int, limit: int = 12) -> list[Row]:
        return await self._all(
            "SELECT * FROM readings WHERE meter_id=? AND status!='replaced' "
            "ORDER BY period DESC, id DESC LIMIT ?",
            (meter_id, limit),
        )

    async def add_reading(
        self,
        meter_id: int,
        user_id: int | None,
        period: str,
        values: dict[str, int | None],
        source: str,
        status: str = "accepted",
        recognized: dict | None = None,
        replace: bool = False,
    ) -> int:
        """Вставляет показание. Есть за период: replace=True → старое помечается 'replaced'
        в той же транзакции, иначе ReadingExists."""
        async with self.tx():
            old = await self.reading_for_period(meter_id, period)
            if old:
                if not replace:
                    raise ReadingExists(old)
                await self._exec("UPDATE readings SET status='replaced' WHERE id=?", (old["id"],))
            cur = await self._exec(
                "INSERT INTO readings(meter_id, user_id, period, t1, t2, t3, source, recognized_json, status, "
                "created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (meter_id, user_id, period, values["t1"], values.get("t2"), values.get("t3"), source,
                 json.dumps(recognized, ensure_ascii=False) if recognized else None, status, _now()),
            )
            return cur.lastrowid

    async def submit_reading(
        self,
        *,
        user_id: int,
        period: str,
        values: dict[str, int | None],
        source: str,
        status: str = "accepted",
        recognized: dict | None = None,
        replace: bool = False,
        meter_id: int | None = None,
        draft: dict | None = None,
    ) -> tuple[int, int]:
        """Подача одной транзакцией: при draft={address_id,type,tariffs,serial?,verification_due?}
        сначала создаётся счётчик. → (meter_id, reading_id)."""
        async with self.tx():
            if meter_id is None:
                if not draft:
                    raise ValueError("meter_id or draft required")
                meter_id = await self.create_meter(
                    draft["address_id"], draft["type"], draft.get("tariffs", 1), draft.get("serial"),
                    user_id, draft.get("verification_due"),
                    "user" if draft.get("verification_due") else None,
                )
            reading_id = await self.add_reading(
                meter_id, user_id, period, values, source, status, recognized, replace
            )
            return meter_id, reading_id

    # === Сессии диалога ===

    async def get_session(self, user_id: int) -> Row | None:
        return await self._one("SELECT * FROM sessions WHERE user_id=?", (user_id,))

    async def save_session(
        self, user_id: int, state: str, data: dict, flow_id: str, now: datetime, expires_at: datetime | None
    ) -> None:
        await self._exec(
            "INSERT INTO sessions(user_id, state, data, flow_id, updated_at, expires_at) "
            "VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET state=excluded.state, "
            "data=excluded.data, flow_id=excluded.flow_id, updated_at=excluded.updated_at, "
            "expires_at=excluded.expires_at",
            (user_id, state, json.dumps(data, ensure_ascii=False), flow_id, ts(now),
             ts(expires_at) if expires_at else None),
        )

    async def delete_session(self, user_id: int) -> None:
        await self._exec("DELETE FROM sessions WHERE user_id=?", (user_id,))

    async def expired_sessions(self, now: datetime) -> list[Row]:
        return await self._all(
            "SELECT * FROM sessions WHERE expires_at IS NOT NULL AND expires_at<?", (ts(now),)
        )

    # === Временные фото ===

    async def add_photo(self, photo_id: str, user_id: int, path: str, now: datetime, expires_at: datetime) -> None:
        await self._exec(
            "INSERT INTO photos(id, user_id, path, created_at, expires_at) VALUES(?, ?, ?, ?, ?)",
            (photo_id, user_id, path, ts(now), ts(expires_at)),
        )

    async def get_photo(self, photo_id: str) -> Row | None:
        return await self._one("SELECT * FROM photos WHERE id=?", (photo_id,))

    async def delete_photo_row(self, photo_id: str) -> Row | None:
        """Удаляет запись о фото и возвращает её (файл удаляет вызывающий — см. bot/photos.py)."""
        async with self.tx():
            row = await self.get_photo(photo_id)
            if row:
                await self._exec("DELETE FROM photos WHERE id=?", (photo_id,))
            return row

    async def expired_photos(self, now: datetime) -> list[Row]:
        return await self._all("SELECT * FROM photos WHERE expires_at<?", (ts(now),))

    # === Счета (демо) ===

    async def ensure_demo_bill(self, address_id: int, today: date) -> None:
        """Один неоплаченный демо-счёт за прошлый месяц, если его ещё нет."""
        period, amount, due = demo_bill(address_id, today)
        await self._exec(
            "INSERT OR IGNORE INTO bills(address_id, period, amount_kop, due_date, status, is_demo) "
            "VALUES(?, ?, ?, ?, 'unpaid', 1)",
            (address_id, period, amount, due.isoformat()),
        )

    async def get_bill(self, bill_id: int) -> Row | None:
        return await self._one("SELECT * FROM bills WHERE id=?", (bill_id,))

    async def unpaid_bills(self, user_id: int) -> list[Row]:
        """Неоплаченные счета по адресам пользователя (granted) + address_label."""
        return await self._all(
            "SELECT b.*, ua.label AS address_label FROM bills b "
            "JOIN user_addresses ua ON ua.address_id=b.address_id AND ua.user_id=? AND ua.access='granted' "
            "WHERE b.status='unpaid' ORDER BY b.due_date",
            (user_id,),
        )

    async def set_bill_status(self, bill_id: int, status: str) -> None:
        await self._exec("UPDATE bills SET status=? WHERE id=?", (status, bill_id))

    # === Уведомления ===

    async def try_mark_sent(self, user_id: int, kind: str, dedup_key: str, now: datetime) -> bool:
        """True — отметили впервые (можно слать); False — уже отправляли."""
        cur = await self._exec(
            "INSERT OR IGNORE INTO notifications(user_id, kind, dedup_key, sent_at) VALUES(?, ?, ?, ?)",
            (user_id, kind, dedup_key, ts(now)),
        )
        return cur.rowcount == 1

    async def unmark_sent(self, user_id: int, kind: str, dedup_key: str) -> None:
        """Откат отметки (например, если отправка не удалась)."""
        await self._exec(
            "DELETE FROM notifications WHERE user_id=? AND kind=? AND dedup_key=?",
            (user_id, kind, dedup_key),
        )

    # === KV ===

    async def kv_get(self, key: str) -> str | None:
        row = await self._one("SELECT value FROM kv WHERE key=?", (key,))
        return row["value"] if row else None

    async def kv_set(self, key: str, value: str | None) -> None:
        await self._exec(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    # === S1 (registration/profile) ===

    async def claim_address(self, user_id: int, address_id: int) -> bool:
        """Модель прав: у адреса не осталось собственника → пользователь становится им (granted)."""
        async with self.tx():
            if await self.address_owner(address_id):
                return False
            await self._exec(
                "UPDATE user_addresses SET role='owner', access='granted' WHERE user_id=? AND address_id=?",
                (user_id, address_id),
            )
            return True

    # --- Приглашения жильцов и список доступа ---

    async def address_members(self, address_id: int) -> list[Row]:
        """Все привязанные к адресу, кроме собственника: user_id, full_name, access — по дате привязки."""
        return await self._all(
            "SELECT u.id AS user_id, u.full_name, ua.access FROM user_addresses ua JOIN users u ON u.id=ua.user_id "
            "WHERE ua.address_id=? AND ua.role!='owner' ORDER BY ua.created_at, u.id",
            (address_id,),
        )

    async def active_invites(self, address_id: int, now: datetime) -> list[Row]:
        """Действующие (не использованы и не истекли) приглашения адреса, старые первыми."""
        return await self._all(
            "SELECT * FROM invites WHERE address_id=? AND used_at IS NULL AND expires_at>? ORDER BY created_at",
            (address_id, ts(now)),
        )

    async def create_invite(self, token: str, address_id: int, owner_user_id: int, now: datetime,
                            expires_at: datetime, limit: int) -> bool:
        """Новое приглашение; False — у адреса уже `limit` действующих."""
        async with self.tx():
            if len(await self.active_invites(address_id, now)) >= limit:
                return False
            await self._exec(
                "INSERT INTO invites(token, address_id, owner_user_id, created_at, expires_at) VALUES(?, ?, ?, ?, ?)",
                (token, address_id, owner_user_id, ts(now), ts(expires_at)),
            )
            return True

    async def get_invite(self, token: str) -> Row | None:
        return await self._one("SELECT * FROM invites WHERE token=?", (token,))

    async def use_invite(self, token: str, user_id: int, now: datetime) -> int | None:
        """Принять приглашение одной транзакцией: отметить использованным и открыть доступ
        (нет связи с адресом — tenant/granted, есть — access=granted). → address_id или None,
        если приглашение не действует или пользователь — собственник этого адреса."""
        async with self.tx():
            inv = await self.get_invite(token)
            if inv is None or inv["used_at"] or inv["expires_at"] <= ts(now):
                return None
            aid = inv["address_id"]
            ua = await self.user_address(user_id, aid)
            if ua and ua["role"] == "owner":
                return None
            await self._exec("UPDATE invites SET used_by=?, used_at=? WHERE token=?", (user_id, ts(now), token))
            if ua:
                await self.set_access(user_id, aid, "granted")
            else:
                await self._exec(
                    "INSERT INTO user_addresses(user_id, address_id, role, access, label, created_at) "
                    "VALUES(?, ?, 'tenant', 'granted', '', ?)",
                    (user_id, aid, _now()),
                )
                await self.ensure_demo_bill(aid, now.date())
                await self.relabel(user_id)
            return aid

    # === S2 (submission) ===

    async def meter_access(self, user_id: int, meter_id: int) -> Row | None:
        """Счётчик (поля meters) + access/role пользователя по его адресу (None, если адрес не привязан).
        Нет такого счётчика → None."""
        return await self._one(
            "SELECT m.*, ua.access, ua.role, ua.label AS address_label FROM meters m "
            "LEFT JOIN user_addresses ua ON ua.address_id=m.address_id AND ua.user_id=? WHERE m.id=?",
            (user_id, meter_id),
        )

    # === S4 (dashboard/notify) ===

    # === S5b (api) ===

    async def get_reading(self, reading_id: int) -> Row | None:
        return await self._one("SELECT * FROM readings WHERE id=?", (reading_id,))

    # === ФГИС «Аршин» ===

    async def arshin_cache_get(self, key: str) -> Row | None:
        return await self._one("SELECT * FROM arshin_cache WHERE key=?", (key,))

    async def arshin_cache_put(self, key: str, payload: str, status: str, fetched_at: str) -> None:
        await self._exec(
            "INSERT INTO arshin_cache(key, payload, status, fetched_at) VALUES(?, ?, ?, ?) ON CONFLICT(key) "
            "DO UPDATE SET payload=excluded.payload, status=excluded.status, fetched_at=excluded.fetched_at",
            (key, payload, status, fetched_at),
        )

    async def set_arshin(self, meter_id: int, *, due: str | None, vri_id: str | None, mit_title: str | None,
                         checked_at: datetime) -> None:
        """Запись ФГИС: срок (None — последняя поверка «непригоден») и source='arshin'."""
        await self._exec(
            "UPDATE meters SET verification_due=?, verification_source=?, arshin_vri_id=?, arshin_mit_title=?, "
            "arshin_checked_at=? WHERE id=?",
            (due, "arshin" if due else None, vri_id, mit_title, ts(checked_at), meter_id),
        )

    async def mark_arshin_checked(self, meter_id: int, checked_at: datetime) -> None:
        await self._exec("UPDATE meters SET arshin_checked_at=? WHERE id=?", (ts(checked_at), meter_id))

    async def arshin_refresh_candidates(self, now: datetime, limit: int) -> list[Row]:
        """Счётчики с номером без даты от пользователя, которые пора (пере)проверить в ФГИС:
        не проверяли; нет arshin-даты — 3 дня; есть — 30 дней. Давно не проверенные — первыми."""
        return await self._all(
            "SELECT * FROM meters WHERE active=1 AND serial_norm IS NOT NULL "
            "AND (verification_source IS NULL OR verification_source IN ('model','arshin')) "
            "AND (arshin_checked_at IS NULL OR arshin_checked_at < CASE WHEN verification_source='arshin' "
            "THEN ? ELSE ? END) ORDER BY arshin_checked_at IS NOT NULL, arshin_checked_at, id LIMIT ?",
            (ts(now - timedelta(days=30)), ts(now - timedelta(days=3)), limit),
        )

    async def meter_photo_hint(self, meter_id: int) -> tuple[str | None, str | None]:
        """Марка и модель с последнего фото счётчика (readings.recognized_json) — для выбора записи ФГИС."""
        rows = await self._all(
            "SELECT recognized_json FROM readings WHERE meter_id=? AND recognized_json IS NOT NULL "
            "ORDER BY id DESC LIMIT 5", (meter_id,))
        for r in rows:
            rec = json.loads(r["recognized_json"])
            if rec.get("brand") or rec.get("model"):
                return rec.get("brand"), rec.get("model")
        return None, None
