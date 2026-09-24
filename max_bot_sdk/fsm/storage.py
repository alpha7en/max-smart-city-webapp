"""
FSM Storage backends: BaseStorage, MemoryStorage, and SQLiteStorage.
Zero emoji policy strictly enforced.
Standalone SDK principle: no imports from app/.
"""

import asyncio
import json
import logging
import os
import sqlite3
import threading
from typing import Optional, Dict, Any, Union

logger = logging.getLogger("max_bot_sdk.storage")


class BaseStorage:
    """Abstract interface for FSM state and data storage."""

    async def get_state(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]]
    ) -> Optional[str]:
        return self.sync_get_state(chat_id, user_id)

    async def set_state(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]],
        state: Optional[str]
    ) -> None:
        self.sync_set_state(chat_id, user_id, state)

    async def get_data(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]]
    ) -> Dict[str, Any]:
        return self.sync_get_data(chat_id, user_id)

    async def set_data(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]],
        data: Dict[str, Any]
    ) -> None:
        self.sync_set_data(chat_id, user_id, data)

    async def update_data(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]],
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        return self.sync_update_data(chat_id, user_id, data)

    async def clear(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]]
    ) -> None:
        self.sync_clear(chat_id, user_id)

    async def close(self) -> None:
        pass

    def sync_get_state(self, chat_id: Optional[Union[str, int]], user_id: Optional[Union[str, int]]) -> Optional[str]:
        raise NotImplementedError

    def sync_set_state(self, chat_id: Optional[Union[str, int]], user_id: Optional[Union[str, int]], state: Optional[str]) -> None:
        raise NotImplementedError

    def sync_get_data(self, chat_id: Optional[Union[str, int]], user_id: Optional[Union[str, int]]) -> Dict[str, Any]:
        raise NotImplementedError

    def sync_set_data(self, chat_id: Optional[Union[str, int]], user_id: Optional[Union[str, int]], data: Dict[str, Any]) -> None:
        raise NotImplementedError

    def sync_update_data(self, chat_id: Optional[Union[str, int]], user_id: Optional[Union[str, int]], data: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def sync_clear(self, chat_id: Optional[Union[str, int]], user_id: Optional[Union[str, int]]) -> None:
        raise NotImplementedError


class MemoryStorage(BaseStorage):
    """In-memory dictionary storage backend for fast testing and local execution."""

    def __init__(self):
        self._states: Dict[str, str] = {}
        self._data: Dict[str, Dict[str, Any]] = {}

    def _make_key(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]]
    ) -> str:
        c = str(chat_id) if chat_id is not None else ""
        u = str(user_id) if user_id is not None else ""
        return f"{c}:{u}"

    def sync_get_state(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]]
    ) -> Optional[str]:
        key = self._make_key(chat_id, user_id)
        return self._states.get(key)

    async def get_state(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]]
    ) -> Optional[str]:
        return self.sync_get_state(chat_id, user_id)

    def sync_set_state(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]],
        state: Optional[str]
    ) -> None:
        key = self._make_key(chat_id, user_id)
        if state is None:
            self._states.pop(key, None)
        else:
            self._states[key] = str(state)

    async def set_state(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]],
        state: Optional[str]
    ) -> None:
        self.sync_set_state(chat_id, user_id, state)

    def sync_get_data(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]]
    ) -> Dict[str, Any]:
        key = self._make_key(chat_id, user_id)
        return dict(self._data.get(key, {}))

    async def get_data(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]]
    ) -> Dict[str, Any]:
        return self.sync_get_data(chat_id, user_id)

    def sync_set_data(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]],
        data: Dict[str, Any]
    ) -> None:
        key = self._make_key(chat_id, user_id)
        self._data[key] = dict(data)

    async def set_data(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]],
        data: Dict[str, Any]
    ) -> None:
        self.sync_set_data(chat_id, user_id, data)

    def sync_update_data(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]],
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        key = self._make_key(chat_id, user_id)
        current = self._data.setdefault(key, {})
        current.update(data)
        return dict(current)

    async def update_data(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]],
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        return self.sync_update_data(chat_id, user_id, data)

    def sync_clear(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]]
    ) -> None:
        key = self._make_key(chat_id, user_id)
        self._states.pop(key, None)
        self._data.pop(key, None)

    async def clear(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]]
    ) -> None:
        self.sync_clear(chat_id, user_id)

    async def close(self) -> None:
        self._states.clear()
        self._data.clear()


class SQLiteStorage(BaseStorage):
    """
    Lightweight persistent SQLite FSM storage with WAL mode.
    Standalone implementation using standard library sqlite3 without external dependencies.
    Schema conforms strictly to:
    fsm_storage(chat_id TEXT, user_id TEXT, state TEXT, data_json TEXT, updated_at TEXT, PRIMARY KEY(chat_id, user_id))
    """

    def __init__(self, db_path: str = "data/fsm_storage.db"):
        self.db_path = db_path
        self._thread_lock = threading.RLock()
        self._key_locks: Dict[str, asyncio.Lock] = {}
        parent_dir = os.path.dirname(db_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
        self._init_db()

    def _get_key_lock(self, chat_id: Optional[Union[str, int]], user_id: Optional[Union[str, int]]) -> asyncio.Lock:
        key = f"{self._to_str(chat_id)}:{self._to_str(user_id)}"
        with self._thread_lock:
            if key not in self._key_locks:
                self._key_locks[key] = asyncio.Lock()
            return self._key_locks[key]

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
        except Exception as e:
            logger.debug("Failed to set PRAGMA on sqlite connection: %s", e)
        return conn

    def _init_db(self) -> None:
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS fsm_storage (
                        chat_id TEXT,
                        user_id TEXT,
                        state TEXT,
                        data_json TEXT,
                        updated_at TEXT,
                        PRIMARY KEY (chat_id, user_id)
                    );
                """)
        finally:
            conn.close()

    def _to_str(self, val: Optional[Union[str, int]]) -> str:
        return str(val) if val is not None else ""

    def sync_get_state(self, chat_id: Optional[Union[str, int]], user_id: Optional[Union[str, int]]) -> Optional[str]:
        c = self._to_str(chat_id)
        u = self._to_str(user_id)
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "SELECT state FROM fsm_storage WHERE chat_id = ? AND user_id = ?",
                (c, u)
            )
            row = cur.fetchone()
            return row["state"] if row and row["state"] else None
        finally:
            conn.close()

    async def get_state(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]]
    ) -> Optional[str]:
        return await asyncio.to_thread(self.sync_get_state, chat_id, user_id)

    def sync_set_state(self, chat_id: Optional[Union[str, int]], user_id: Optional[Union[str, int]], state: Optional[str]) -> None:
        c = self._to_str(chat_id)
        u = self._to_str(user_id)
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO fsm_storage (chat_id, user_id, state, data_json, updated_at)
                    VALUES (?, ?, ?, '{}', datetime('now'))
                    ON CONFLICT(chat_id, user_id) DO UPDATE SET
                        state = excluded.state,
                        updated_at = datetime('now');
                """, (c, u, state))
        finally:
            conn.close()

    async def set_state(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]],
        state: Optional[str]
    ) -> None:
        await asyncio.to_thread(self.sync_set_state, chat_id, user_id, state)

    def sync_get_data(self, chat_id: Optional[Union[str, int]], user_id: Optional[Union[str, int]]) -> Dict[str, Any]:
        c = self._to_str(chat_id)
        u = self._to_str(user_id)
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "SELECT data_json FROM fsm_storage WHERE chat_id = ? AND user_id = ?",
                (c, u)
            )
            row = cur.fetchone()
            if row and row["data_json"]:
                try:
                    return json.loads(row["data_json"])
                except Exception:
                    return {}
            return {}
        finally:
            conn.close()

    async def get_data(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]]
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(self.sync_get_data, chat_id, user_id)

    def sync_set_data(self, chat_id: Optional[Union[str, int]], user_id: Optional[Union[str, int]], data: Dict[str, Any]) -> None:
        c = self._to_str(chat_id)
        u = self._to_str(user_id)
        encoded = json.dumps(data, ensure_ascii=False)
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO fsm_storage (chat_id, user_id, state, data_json, updated_at)
                    VALUES (?, ?, NULL, ?, datetime('now'))
                    ON CONFLICT(chat_id, user_id) DO UPDATE SET
                        data_json = excluded.data_json,
                        updated_at = datetime('now');
                """, (c, u, encoded))
        finally:
            conn.close()

    async def set_data(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]],
        data: Dict[str, Any]
    ) -> None:
        await asyncio.to_thread(self.sync_set_data, chat_id, user_id, data)

    def sync_update_data(self, chat_id: Optional[Union[str, int]], user_id: Optional[Union[str, int]], data: Dict[str, Any]) -> Dict[str, Any]:
        c = self._to_str(chat_id)
        u = self._to_str(user_id)
        with self._thread_lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                cur = conn.execute(
                    "SELECT data_json FROM fsm_storage WHERE chat_id = ? AND user_id = ?",
                    (c, u)
                )
                row = cur.fetchone()
                current = {}
                if row and row["data_json"]:
                    try:
                        current = json.loads(row["data_json"])
                    except Exception:
                        current = {}
                current.update(data)
                encoded = json.dumps(current, ensure_ascii=False)
                conn.execute("""
                    INSERT INTO fsm_storage (chat_id, user_id, state, data_json, updated_at)
                    VALUES (?, ?, NULL, ?, datetime('now'))
                    ON CONFLICT(chat_id, user_id) DO UPDATE SET
                        data_json = excluded.data_json,
                        updated_at = datetime('now');
                """, (c, u, encoded))
                conn.commit()
                return current
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                conn.close()

    async def update_data(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]],
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        lock = self._get_key_lock(chat_id, user_id)
        async with lock:
            return await asyncio.to_thread(self.sync_update_data, chat_id, user_id, data)

    def sync_clear(self, chat_id: Optional[Union[str, int]], user_id: Optional[Union[str, int]]) -> None:
        c = self._to_str(chat_id)
        u = self._to_str(user_id)
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    "DELETE FROM fsm_storage WHERE chat_id = ? AND user_id = ?",
                    (c, u)
                )
        finally:
            conn.close()

    async def clear(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]]
    ) -> None:
        await asyncio.to_thread(self.sync_clear, chat_id, user_id)

    async def close(self) -> None:
        with self._thread_lock:
            self._key_locks.clear()
