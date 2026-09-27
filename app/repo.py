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
from app.domain.access import decide_role, meter_delete_denial
from app.domain.meters import FIELDS, demo_bill, normalize_serial

HACKATHON_DEMO_KEY = "hackathon-demo:"  # префикс norm_key адресов демо-профиля (flows/hackathon_demo.py)
# Демо-люди демо-профиля (жильцы и собственник общего адреса): max_user_id < 0 — аккаунта MAX у них нет,
# сообщений им не шлём (sharing.notify, планировщик берёт только registered_users с max_user_id > 0).
DEMO_PERSON_MAX_ID = 0


def is_demo_person(user: dict | None) -> bool:
    return bool(user) and user["max_user_id"] < DEMO_PERSON_MAX_ID


Row = dict[str, Any]
# Подписи адресов: список адресов пользователя (dict строк addresses) → список label той же длины.
Labeler = Callable[[list[Row]], list[str]]

# Адрес пользователя: поля addresses + связь (role, access, label, …) + кто открыл доступ (granted_by_name),
# собственник адреса (owner_id, owner_name) и сколько людей с доступом кроме собственника (shared_count).
_UA_SELECT = (
    "SELECT a.*, ua.role, ua.access, ua.label, ua.raw_input, ua.created_at AS linked_at, ua.granted_by, "
    "ua.granted_at, (SELECT full_name FROM users WHERE id=ua.granted_by) AS granted_by_name, "
    "(SELECT o.user_id FROM user_addresses o WHERE o.address_id=a.id AND o.role='owner' "
    " ORDER BY o.created_at LIMIT 1) AS owner_id, "
    "(SELECT u.full_name FROM user_addresses o JOIN users u ON u.id=o.user_id WHERE o.address_id=a.id "
    " AND o.role='owner' ORDER BY o.created_at LIMIT 1) AS owner_name, "
    "(SELECT COUNT(*) FROM user_addresses x WHERE x.address_id=a.id AND x.role!='owner' AND x.access='granted') "
    " AS shared_count FROM user_addresses ua JOIN addresses a ON a.id=ua.address_id "
)

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
    """Подписи адресов пользователя: domain.addresses.short_labels."""
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
        return await self._all("SELECT * FROM users WHERE registered_at IS NOT NULL AND max_user_id>? ORDER BY id",
                               (DEMO_PERSON_MAX_ID,))

    async def delete_user_data(self, user_id: int) -> Row:
        """Удаляет пользователя: сессию, фото (и файлы), уведомления, связи с адресами, его приглашения.
        Показания и счётчики остаются за адресом (user_id/created_by → NULL).
        Общий доступ: у адресов, где он собственник, собственником становится первый получивший доступ
        (модель прав, как claim_address). → {photos: число фото, received: [{address_id, owner_id}] — адреса,
        которыми с ним делились, promoted: [{address_id, user_id}] — кто стал собственником}."""
        async with self.tx():
            photos = await self._all("SELECT path FROM photos WHERE user_id=?", (user_id,))
            links = await self.user_addresses(user_id)
            received = [{"address_id": a["id"], "owner_id": a["owner_id"]} for a in links
                        if a["role"] != "owner" and a["access"] == "granted" and a["owner_id"]]
            promoted = []
            for a in links:
                if a["role"] != "owner":
                    continue
                heir = await self._one(
                    "SELECT ua.user_id FROM user_addresses ua JOIN users u ON u.id=ua.user_id WHERE ua.address_id=? "
                    "AND ua.user_id!=? AND ua.role!='owner' AND ua.access='granted' AND u.max_user_id>? "
                    "ORDER BY COALESCE(ua.granted_at, ua.created_at), ua.user_id LIMIT 1",
                    (a["id"], user_id, DEMO_PERSON_MAX_ID))
                if heir:
                    await self._exec("UPDATE user_addresses SET role='owner', granted_by=NULL WHERE user_id=? "
                                     "AND address_id=?", (heir["user_id"], a["id"]))
                    promoted.append({"address_id": a["id"], "user_id": heir["user_id"]})
            for sql in (
                "DELETE FROM sessions WHERE user_id=?",
                "DELETE FROM photos WHERE user_id=?",
                "DELETE FROM notifications WHERE user_id=?",
                "DELETE FROM user_addresses WHERE user_id=?",
                "DELETE FROM users WHERE id=?",
            ):
                await self._exec(sql, (user_id,))
            await self.drop_orphan_demo_people()
        for p in photos:
            Path(p["path"]).unlink(missing_ok=True)
        return {"photos": len(photos), "received": received, "promoted": promoted}

    # === Адреса ===

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
        """Адреса пользователя: поля addresses + role, access, label, raw_input, linked_at, granted_by(_name),
        granted_at, owner_id, owner_name, shared_count (см. _UA_SELECT)."""
        return await self._all(_UA_SELECT + "WHERE ua.user_id=? ORDER BY ua.created_at, a.id", (user_id,))

    async def user_address(self, user_id: int, address_id: int) -> Row | None:
        return await self._one(_UA_SELECT + "WHERE ua.user_id=? AND ua.address_id=?", (user_id, address_id))

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

    async def set_access(self, user_id: int, address_id: int, access: str, by: int | None = None) -> None:
        """access; при 'granted' запоминаем, кто (by — обычно собственник) и когда открыл доступ."""
        if access == "granted":
            await self._exec(
                "UPDATE user_addresses SET access=?, granted_by=COALESCE(?, granted_by), granted_at=? "
                "WHERE user_id=? AND address_id=?", (access, by, _now(), user_id, address_id))
            return
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
        """Счётчики на адресах пользователя: поля meters + address_label (короткий), address_full, role, access
        + последнее показание: last_id, last_period, last_t1..t3, last_source, last_status, last_created_at,
        last_user_id и last_by_name (кто подал; NULL — удалил свои данные)."""
        where = "AND ua.access='granted'" if only_granted else ""
        return await self._all(
            "SELECT m.*, ua.label AS address_label, a.full_text AS address_full, ua.role, ua.access, "
            "r.id AS last_id, r.period AS last_period, r.t1 AS last_t1, r.t2 AS last_t2, r.t3 AS last_t3, "
            "r.source AS last_source, r.status AS last_status, r.created_at AS last_created_at, "
            "r.user_id AS last_user_id, (SELECT full_name FROM users WHERE id=r.user_id) AS last_by_name "
            "FROM meters m JOIN user_addresses ua ON ua.address_id=m.address_id AND ua.user_id=? "
            "JOIN addresses a ON a.id=m.address_id "
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
        """Действующие показания, новые первыми; by_name — ФИО подавшего (NULL — удалил свои данные)."""
        return await self._all(
            "SELECT r.*, u.full_name AS by_name FROM readings r LEFT JOIN users u ON u.id=r.user_id "
            "WHERE r.meter_id=? AND r.status!='replaced' ORDER BY r.period DESC, r.id DESC LIMIT ?",
            (meter_id, limit),
        )

    async def get_reading(self, reading_id: int) -> Row | None:
        return await self._one("SELECT * FROM readings WHERE id=?", (reading_id,))

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
        """Неоплаченные счета по адресам пользователя (granted) + address_label, address_full."""
        return await self._all(
            "SELECT b.*, ua.label AS address_label, a.full_text AS address_full FROM bills b "
            "JOIN addresses a ON a.id=b.address_id "
            "JOIN user_addresses ua ON ua.address_id=b.address_id AND ua.user_id=? AND ua.access='granted' "
            "WHERE b.status='unpaid' ORDER BY b.due_date",
            (user_id,),
        )

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

    # === Права на адрес и «Поделиться доступом» ===

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

    # --- Поделиться доступом: люди с доступом к адресу, приглашения ---

    async def address_members(self, address_id: int) -> list[Row]:
        """Все привязанные к адресу, кроме собственника, по дате привязки: user_id, full_name, max_user_id, access,
        since (когда открыт доступ или привязан адрес), last_at / last_period — последнее их показание по адресу."""
        last = ("(SELECT r.{col} FROM readings r JOIN meters m ON m.id=r.meter_id WHERE m.address_id=ua.address_id "
                "AND r.user_id=ua.user_id AND r.status!='replaced' ORDER BY {order} LIMIT 1)")
        return await self._all(
            "SELECT u.id AS user_id, u.full_name, u.max_user_id, ua.access, "
            "COALESCE(ua.granted_at, ua.created_at) AS since, "
            f"{last.format(col='created_at', order='r.created_at DESC, r.id DESC')} AS last_at, "
            f"{last.format(col='period', order='r.period DESC')} AS last_period "
            "FROM user_addresses ua JOIN users u ON u.id=ua.user_id "
            "WHERE ua.address_id=? AND ua.role!='owner' ORDER BY ua.created_at, u.id",
            (address_id,),
        )

    async def _with_addresses(self, inv: Row | None) -> Row | None:
        """Приглашение + address_ids."""
        if inv is None:
            return None
        rows = await self._all("SELECT address_id FROM invite_addresses WHERE invite_id=? ORDER BY address_id",
                               (inv["id"],))
        return {**inv, "address_ids": [r["address_id"] for r in rows]}

    async def get_invite(self, token: str) -> Row | None:
        return await self._with_addresses(await self._one("SELECT * FROM invites WHERE token=?", (token,)))

    async def get_invite_by_id(self, invite_id: int) -> Row | None:
        return await self._with_addresses(await self._one("SELECT * FROM invites WHERE id=?", (invite_id,)))

    async def owner_invites(self, owner_id: int, now: datetime) -> list[Row]:
        """Действующие приглашения собственника (не приняты, не отменены, не истекли), старые первыми."""
        rows = await self._all(
            "SELECT * FROM invites WHERE owner_user_id=? AND used_at IS NULL AND cancelled_at IS NULL "
            "AND expires_at>? ORDER BY created_at, id", (owner_id, ts(now)))
        return [await self._with_addresses(r) for r in rows]

    async def create_invite(self, token: str, owner_id: int, address_ids: Sequence[int], now: datetime,
                            expires_at: datetime, limit: int) -> int | None:
        """Приглашение на адреса собственника → id; None — у собственника уже `limit` действующих.
        Не свой адрес — ValueError (права проверяет вызывающий, здесь — страховка)."""
        async with self.tx():
            if len(await self.owner_invites(owner_id, now)) >= limit:
                return None
            for aid in address_ids:
                ua = await self.user_address(owner_id, aid)
                if ua is None or ua["role"] != "owner":
                    raise ValueError(f"address {aid} is not owned by user {owner_id}")
            cur = await self._exec(
                "INSERT INTO invites(token, owner_user_id, created_at, expires_at) VALUES(?, ?, ?, ?)",
                (token, owner_id, ts(now), ts(expires_at)))
            for aid in dict.fromkeys(address_ids):
                await self._exec("INSERT INTO invite_addresses(invite_id, address_id) VALUES(?, ?)",
                                 (cur.lastrowid, aid))
            return cur.lastrowid

    async def cancel_invite(self, invite_id: int, now: datetime) -> None:
        await self._exec("UPDATE invites SET cancelled_at=? WHERE id=? AND cancelled_at IS NULL AND used_at IS NULL",
                         (ts(now), invite_id))

    async def use_invite(self, token: str, user_id: int, now: datetime) -> Row | None:
        """Принять приглашение одной транзакцией: доступ granted ко всем его адресам (нет связи — tenant,
        pending/denied — granted; granted_by — собственник). Свой адрес ('own'), уже открытый ('has') и адрес,
        который больше не принадлежит пригласившему ('gone'), пропускаем. Приглашение тратится, только если
        что-то открыли. → {granted, own, has, gone: [address_id]} или None — приглашение не действует."""
        async with self.tx():
            inv = await self.get_invite(token)
            if inv is None or inv["used_at"] or inv["cancelled_at"] or inv["expires_at"] <= ts(now):
                return None
            out: Row = {"granted": [], "own": [], "has": [], "gone": []}
            for aid in inv["address_ids"]:
                owner = await self.address_owner(aid)
                ua = await self.user_address(user_id, aid)
                if ua and ua["role"] == "owner":
                    out["own"].append(aid)
                elif owner is None or owner["id"] != inv["owner_user_id"]:
                    out["gone"].append(aid)
                elif ua and ua["access"] == "granted":
                    out["has"].append(aid)
                else:
                    if ua:
                        await self.set_access(user_id, aid, "granted", by=inv["owner_user_id"])
                    else:
                        await self._exec(
                            "INSERT INTO user_addresses(user_id, address_id, role, access, label, created_at, "
                            "granted_by, granted_at) VALUES(?, ?, 'tenant', 'granted', '', ?, ?, ?)",
                            (user_id, aid, _now(), inv["owner_user_id"], _now()))
                        await self.ensure_demo_bill(aid, now.date())
                    out["granted"].append(aid)
            if out["granted"]:
                await self._exec("UPDATE invites SET used_by=?, used_at=? WHERE id=?", (user_id, ts(now), inv["id"]))
                await self.relabel(user_id)
            return out

    async def remove_address(self, user_id: int, address_id: int) -> Row | None:
        """«Выйти» из общего доступа: удалить связь не-собственника с адресом (показания остаются за адресом).
        → удалённая строка (как user_address) или None — связи нет или это собственник."""
        async with self.tx():
            ua = await self.user_address(user_id, address_id)
            if ua is None or ua["role"] == "owner":
                return None
            await self._exec("DELETE FROM user_addresses WHERE user_id=? AND address_id=?", (user_id, address_id))
            await self.relabel(user_id)
            await self.drop_orphan_demo_people()
            return ua

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
            (due, "arshin" if (due or vri_id) else None, vri_id, mit_title, ts(checked_at), meter_id),
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

    # === Права на счётчик и удаление ===

    async def meter_access(self, user_id: int, meter_id: int) -> Row | None:
        """Счётчик (поля meters) + access/role пользователя по его адресу (None, если адрес не привязан).
        Нет такого счётчика → None."""
        return await self._one(
            "SELECT m.*, ua.access, ua.role, ua.label AS address_label, a.full_text AS address_full FROM meters m "
            "JOIN addresses a ON a.id=m.address_id "
            "LEFT JOIN user_addresses ua ON ua.address_id=m.address_id AND ua.user_id=? WHERE m.id=?",
            (user_id, meter_id),
        )

    async def address_granted_others(self, address_id: int, user_id: int) -> int:
        """Сколько других пользователей с доступом granted привязано к адресу."""
        row = await self._one(
            "SELECT COUNT(*) AS n FROM user_addresses WHERE address_id=? AND user_id!=? AND access='granted'",
            (address_id, user_id),
        )
        return row["n"] if row else 0

    async def delete_meter(self, user_id: int, meter_id: int) -> str:
        """Мягкое удаление счётчика одной транзакцией → 'ok' | 'not_found' | 'no_access' | 'not_owner'.
        active=0: счётчик пропадает из всех выборок (user_meters, address_meters, поиск по номеру, ФГИС),
        показания остаются в БД. serial_norm освобождаем: тот же номер по адресу можно добавить заново —
        это будет новый счётчик (с верным типом и тарифами), serial остаётся для истории."""
        async with self.tx():
            m = await self.meter_access(user_id, meter_id)
            if not m or not m["active"]:
                return "not_found"
            others = await self.address_granted_others(m["address_id"], user_id)
            if denial := meter_delete_denial(m["role"], m["access"], others):
                return denial
            await self._exec("UPDATE meters SET active=0, serial_norm=NULL WHERE id=? AND active=1", (meter_id,))
            return "ok"

    # === Хакатон: демо-профиль для проверяющих (не основной функционал, flows/hackathon_demo.py) ===

    async def is_hackathon_demo(self, user_id: int) -> bool:
        """Профиль создан /demo_profile: у пользователя есть адрес с ключом HACKATHON_DEMO_KEY."""
        row = await self._one(
            "SELECT 1 FROM user_addresses ua JOIN addresses a ON a.id=ua.address_id "
            "WHERE ua.user_id=? AND a.norm_key LIKE ? LIMIT 1", (user_id, HACKATHON_DEMO_KEY + "%"))
        return row is not None

    async def create_demo_person(self, full_name: str, phone: str, max_user_id: int) -> Row:
        """Демо-человек демо-профиля (жилец или собственник общего адреса): max_user_id < 0 — аккаунта MAX нет,
        registered_at NULL — планировщик его не видит. Занятый max_user_id → берём следующий."""
        assert max_user_id < DEMO_PERSON_MAX_ID
        async with self.tx():
            while await self.get_user(max_user_id):
                max_user_id -= 1
            await self._exec("INSERT INTO users(max_user_id, full_name, phone, created_at) VALUES(?, ?, ?, ?)",
                             (max_user_id, full_name, phone, _now()))
            return await self.get_user(max_user_id)

    async def drop_orphan_demo_people(self) -> int:
        """Удаляет демо-людей, с которыми не осталось ни одного реального пользователя по общим адресам
        (демо-профиль удалён или общий демо-адрес убран у себя). Показания остаются за адресом. → сколько удалили."""
        rows = await self._all(
            "SELECT u.id FROM users u WHERE u.max_user_id<? AND NOT EXISTS (SELECT 1 FROM user_addresses d "
            "JOIN user_addresses r ON r.address_id=d.address_id JOIN users ru ON ru.id=r.user_id "
            "WHERE d.user_id=u.id AND ru.max_user_id>?)", (DEMO_PERSON_MAX_ID, DEMO_PERSON_MAX_ID))
        for r in rows:
            for sql in ("DELETE FROM user_addresses WHERE user_id=?", "DELETE FROM users WHERE id=?"):
                await self._exec(sql, (r["id"],))
        return len(rows)

    async def backdate_reading(self, reading_id: int, created_at: datetime) -> None:
        """Сгенерированной истории показаний ставим дату подачи в её месяце, а не «сейчас»."""
        await self._exec("UPDATE readings SET created_at=? WHERE id=?", (ts(created_at), reading_id))
