"""
Database management layer for MAX Smart City housing platform.
Provides lightweight, zero-dependency SQLite persistence using standard library sqlite3.
Configured with Write-Ahead Logging (WAL mode), busy timeout 5000ms, and foreign key constraints.
Automatically seeds reference dataset for resident Ivan Ivanov (100456).
"""

import os
import json
import sqlite3
import threading
import logging
from pathlib import Path
from contextlib import contextmanager
from typing import Generator, Optional, List, Dict, Any

logger = logging.getLogger("max_smart_city_db")

# Default database location: ./data/smart_city.db relative to project root
DEFAULT_DB_FILE = "./data/smart_city.db"

# Reference dataset constants
REFERENCE_USER_ID = 100456
REFERENCE_USER = {
    "user_id": REFERENCE_USER_ID,
    "first_name": "Иван",
    "last_name": "Иванов",
    "username": "ivan_ivanov",
    "phone": "+7 (999) 123-45-67",
    "is_verified": 1,
    "active_property_id": "prop-flat-42-15",
    "created_at": "2026-08-20T10:00:00",
    "updated_at": "2026-08-20T10:00:00"
}

REFERENCE_PROPERTIES = [
    {
        "id": "prop-flat-42-15",
        "user_id": REFERENCE_USER_ID,
        "address": "г. Москва, ул. Ленина, д. 42, кв. 15",
        "els": "1004567890",
        "management_company": "ООО УК Столица-Сервис",
        "is_active": 1,
        "role": "owner",
        "linked_at": "2026-08-20T10:00:00"
    },
    {
        "id": "prop-dacha-8",
        "user_id": REFERENCE_USER_ID,
        "address": "Московская обл., д. Барвиха, д. 8",
        "els": "2008891024",
        "management_company": "ТСЖ Рублево-Сервис",
        "is_active": 0,
        "role": "tenant",
        "linked_at": "2026-08-20T10:00:00"
    }
]

REFERENCE_METERS = [
    {
        "id": "meter-khvs-1",
        "property_id": "prop-flat-42-15",
        "meter_type": "cold_water",
        "serial_number": "2809142",
        "name": "ХВС (Холодная вода)",
        "installation_place": "Санузел",
        "last_reading_value": 142.0,
        "last_reading_date": "2026-08-20",
        "verification_date_valid_until": "2029-10-18",
        "unit": "м³",
        "decimal_digits": 3,
        "created_at": "2026-08-20T10:00:00",
        "updated_at": "2026-08-20T10:00:00"
    },
    {
        "id": "meter-gvs-1",
        "property_id": "prop-flat-42-15",
        "meter_type": "hot_water",
        "serial_number": "3910844",
        "name": "ГВС (Горячая вода)",
        "installation_place": "Санузел",
        "last_reading_value": 98.0,
        "last_reading_date": "2026-08-20",
        "verification_date_valid_until": "2028-04-12",
        "unit": "м³",
        "decimal_digits": 3,
        "created_at": "2026-08-20T10:00:00",
        "updated_at": "2026-08-20T10:00:00"
    },
    {
        "id": "meter-el-1",
        "property_id": "prop-flat-42-15",
        "meter_type": "el_multi",
        "serial_number": "01458291",
        "name": "Электроэнергия (Меркурий 208)",
        "installation_place": "Щит на лестничной клетке",
        "last_reading_value": 1840.0,
        "last_reading_date": "2026-08-20",
        "verification_date_valid_until": "2032-11-05",
        "unit": "кВт*ч",
        "decimal_digits": 1,
        "created_at": "2026-08-20T10:00:00",
        "updated_at": "2026-08-20T10:00:00"
    },
    {
        "id": "meter-heat-1",
        "property_id": "prop-flat-42-15",
        "meter_type": "heat",
        "serial_number": "7741209",
        "name": "Отопление (Теплосчетчик)",
        "installation_place": "Коридор",
        "last_reading_value": 14.2,
        "last_reading_date": "2026-08-20",
        "verification_date_valid_until": "2027-09-30",
        "unit": "Гкал",
        "decimal_digits": 2,
        "created_at": "2026-08-20T10:00:00",
        "updated_at": "2026-08-20T10:00:00"
    },
    {
        "id": "meter-gas-1",
        "property_id": "prop-flat-42-15",
        "meter_type": "gas",
        "serial_number": "5540912",
        "name": "Газоснабжение (ВК-G4)",
        "installation_place": "Кухня",
        "last_reading_value": 340.0,
        "last_reading_date": "2026-08-20",
        "verification_date_valid_until": "2030-06-15",
        "unit": "м³",
        "decimal_digits": 3,
        "created_at": "2026-08-20T10:00:00",
        "updated_at": "2026-08-20T10:00:00"
    }
]

REFERENCE_TICKET = {
    "id": "TCK-2026-0819",
    "user_id": REFERENCE_USER_ID,
    "address": "ул. Ленина, д. 42, кв. 15",
    "category": "Сантехника",
    "description": "Слабый напор горячей воды на верхних этажах",
    "priority": "urgent",
    "status": "in_progress",
    "sla_hours": 24,
    "assigned_master": "Иванов С. М. (Дежурный слесарь-сантехник)",
    "photo_urls_json": "[]",
    "status_history_json": json.dumps([
        {"status": "new", "time": "2026-09-18T14:30:00", "comment": "Заявка зарегистрирована в MAX"},
        {"status": "in_progress", "time": "2026-09-18T15:10:00", "comment": "Мастер выехал на объект"}
    ]),
    "created_at": "2026-09-18T14:30:00",
    "updated_at": "2026-09-18T15:10:00"
}


class Database:
    """
    Thread-safe SQLite database manager.
    Supports WAL mode, connection per thread, busy timeout, and automatic data seeding.
    """

    def __init__(self, db_path: Optional[str] = None):
        # Resolve target database path
        env_path = os.environ.get("SMART_CITY_DB_PATH") or os.environ.get("DB_PATH")
        self._raw_path = db_path or env_path or DEFAULT_DB_FILE

        self._is_memory = (self._raw_path == ":memory:") or ("mode=memory" in self._raw_path)
        self._local = threading.local()
        self._lock = threading.RLock()
        self._root_conn: Optional[sqlite3.Connection] = None
        self._connect_uri = False

        if not self._is_memory:
            # Ensure target directory exists for file databases
            target_dir = os.path.dirname(os.path.abspath(self._raw_path))
            if target_dir:
                os.makedirs(target_dir, exist_ok=True)
        else:
            # For in-memory testing, use shared in-memory URI so multiple threads share identical state
            self._connect_uri = True
            self._raw_path = "file:smart_city_shared_mem?mode=memory&cache=shared"
            self._root_conn = sqlite3.connect(
                self._raw_path,
                uri=True,
                timeout=5.0,
                check_same_thread=False
            )
            self._configure_connection(self._root_conn)

        # Initialize schema and seed reference data
        self.init_db()

    @property
    def db_path(self) -> str:
        return self._raw_path

    def _configure_connection(self, conn: sqlite3.Connection) -> None:
        """Applies essential PRAGMA settings for performance, safety and concurrency."""
        conn.row_factory = sqlite3.Row
        if not self._is_memory and not self._connect_uri:
            try:
                conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute("PRAGMA synchronous = NORMAL;")
            except Exception as e:
                logger.warning("Could not set WAL journal mode: %s", e)
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA foreign_keys = ON;")

    def get_connection(self) -> sqlite3.Connection:
        """Returns thread-local sqlite3 connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            if self._is_memory or self._connect_uri:
                conn = sqlite3.connect(
                    self._raw_path,
                    uri=True,
                    timeout=5.0,
                    check_same_thread=False
                )
            else:
                conn = sqlite3.connect(
                    self._raw_path,
                    timeout=5.0,
                    check_same_thread=False
                )
            self._configure_connection(conn)
            self._local.conn = conn
        return conn

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for atomic write transactions with reentrant locking."""
        with self._lock:
            conn = self.get_connection()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def execute_query(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        """Executes a read query and returns all matching rows."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()

    def execute_one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        """Executes a read query and returns the first matching row or None."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchone()

    def execute_write(self, sql: str, params: tuple = ()) -> int:
        """Executes a write query within an atomic transaction and returns affected count or lastrowid."""
        with self.transaction() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return cur.lastrowid or cur.rowcount

    def execute_many(self, sql: str, seq_of_params: List[tuple]) -> int:
        """Executes bulk write operations in a single transaction."""
        with self.transaction() as conn:
            cur = conn.cursor()
            cur.executemany(sql, seq_of_params)
            return cur.rowcount

    def init_db(self) -> None:
        """Creates all relational schema tables and seeds initial reference dataset."""
        with self.transaction() as conn:
            # 1. users
            conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT NOT NULL DEFAULT 'Иван',
                last_name TEXT DEFAULT 'Иванов',
                username TEXT,
                phone TEXT,
                is_verified INTEGER NOT NULL DEFAULT 0,
                active_property_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """)

            # 2. properties
            conn.execute("""
            CREATE TABLE IF NOT EXISTS properties (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                address TEXT NOT NULL,
                els TEXT NOT NULL,
                management_company TEXT NOT NULL DEFAULT 'ООО УК Столица-Сервис',
                is_active INTEGER NOT NULL DEFAULT 0,
                role TEXT NOT NULL DEFAULT 'owner',
                linked_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_properties_user_id ON properties (user_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_properties_els ON properties (els);")

            # 3. meters
            conn.execute("""
            CREATE TABLE IF NOT EXISTS meters (
                id TEXT PRIMARY KEY,
                property_id TEXT,
                meter_type TEXT NOT NULL,
                serial_number TEXT NOT NULL,
                name TEXT NOT NULL,
                installation_place TEXT NOT NULL DEFAULT 'Квартира',
                last_reading_value REAL NOT NULL,
                last_reading_date TEXT NOT NULL,
                verification_date_valid_until TEXT NOT NULL,
                unit TEXT NOT NULL DEFAULT 'м³',
                decimal_digits INTEGER NOT NULL DEFAULT 3,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (property_id) REFERENCES properties (id) ON DELETE SET NULL
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_meters_property_id ON meters (property_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_meters_serial_number ON meters (serial_number);")

            # 4. meter_readings
            conn.execute("""
            CREATE TABLE IF NOT EXISTS meter_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meter_id TEXT NOT NULL,
                reading_value REAL NOT NULL,
                reading_value_t2 REAL,
                reading_value_t3 REAL,
                consumption REAL NOT NULL DEFAULT 0.0,
                reading_date TEXT NOT NULL,
                submission_channel TEXT NOT NULL DEFAULT 'max_miniapp',
                status TEXT NOT NULL DEFAULT 'accepted',
                is_anomaly INTEGER NOT NULL DEFAULT 0,
                anomaly_reason TEXT,
                red_roller_filtered INTEGER NOT NULL DEFAULT 0,
                raw_value REAL,
                photo_base64 TEXT,
                photo_hash TEXT,
                comment TEXT,
                is_valid INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY (meter_id) REFERENCES meters (id) ON DELETE CASCADE
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_meter_readings_meter_id ON meter_readings (meter_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_meter_readings_date ON meter_readings (reading_date);")

            # 5. tickets
            conn.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY,
                user_id INTEGER,
                address TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                priority TEXT NOT NULL,
                status TEXT NOT NULL,
                sla_hours INTEGER NOT NULL,
                assigned_master TEXT,
                photo_urls_json TEXT NOT NULL DEFAULT '[]',
                status_history_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE SET NULL
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_user_id ON tickets (user_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets (status);")

            # 6. inspector_acts
            conn.execute("""
            CREATE TABLE IF NOT EXISTS inspector_acts (
                act_id TEXT PRIMARY KEY,
                meter_id TEXT,
                reading_value REAL NOT NULL,
                address TEXT NOT NULL,
                inspector_name TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                gps TEXT NOT NULL,
                photo_hash_sha256 TEXT,
                status TEXT NOT NULL DEFAULT 'success',
                export_1c_ready INTEGER NOT NULL DEFAULT 1,
                act_number TEXT,
                act_title TEXT,
                gps_coordinates TEXT,
                meter_reading TEXT,
                crypto_hash TEXT,
                billing_export_status TEXT,
                legal_significance TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (meter_id) REFERENCES meters (id) ON DELETE SET NULL
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_inspector_acts_meter_id ON inspector_acts (meter_id);")

        # Auto-seed pristine reference data if database is brand new
        self.seed_reference_data(force=False)

    def seed_reference_data(self, force: bool = False) -> None:
        """
        Populates initial reference data for resident Ivan Ivanov (100456),
        including 2 properties, 5 utility meters, and reference ticket TCK-2026-0819.
        If force=True, wipes existing records and re-seeds.
        """
        with self.transaction() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users;")
            user_count = cur.fetchone()[0]

            if user_count > 0 and not force:
                return

            if force:
                # Clear existing data in reverse foreign key order
                cur.execute("DELETE FROM inspector_acts;")
                cur.execute("DELETE FROM meter_readings;")
                cur.execute("DELETE FROM meters;")
                cur.execute("DELETE FROM tickets;")
                cur.execute("DELETE FROM properties;")
                cur.execute("DELETE FROM users;")

            # 1. Seed user 100456
            cur.execute("""
            INSERT OR REPLACE INTO users (
                user_id, first_name, last_name, username, phone, is_verified, active_property_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                REFERENCE_USER["user_id"],
                REFERENCE_USER["first_name"],
                REFERENCE_USER["last_name"],
                REFERENCE_USER["username"],
                REFERENCE_USER["phone"],
                REFERENCE_USER["is_verified"],
                REFERENCE_USER["active_property_id"],
                REFERENCE_USER["created_at"],
                REFERENCE_USER["updated_at"]
            ))

            # 2. Seed properties
            for prop in REFERENCE_PROPERTIES:
                cur.execute("""
                INSERT OR REPLACE INTO properties (
                    id, user_id, address, els, management_company, is_active, role, linked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    prop["id"],
                    prop["user_id"],
                    prop["address"],
                    prop["els"],
                    prop["management_company"],
                    prop["is_active"],
                    prop["role"],
                    prop["linked_at"]
                ))

            # 3. Seed meters
            for m in REFERENCE_METERS:
                cur.execute("""
                INSERT OR REPLACE INTO meters (
                    id, property_id, meter_type, serial_number, name, installation_place,
                    last_reading_value, last_reading_date, verification_date_valid_until,
                    unit, decimal_digits, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    m["id"],
                    m["property_id"],
                    m["meter_type"],
                    m["serial_number"],
                    m["name"],
                    m["installation_place"],
                    m["last_reading_value"],
                    m["last_reading_date"],
                    m["verification_date_valid_until"],
                    m["unit"],
                    m["decimal_digits"],
                    m["created_at"],
                    m["updated_at"]
                ))

            # 4. Seed ticket
            cur.execute("""
            INSERT OR REPLACE INTO tickets (
                id, user_id, address, category, description, priority, status,
                sla_hours, assigned_master, photo_urls_json, status_history_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                REFERENCE_TICKET["id"],
                REFERENCE_TICKET["user_id"],
                REFERENCE_TICKET["address"],
                REFERENCE_TICKET["category"],
                REFERENCE_TICKET["description"],
                REFERENCE_TICKET["priority"],
                REFERENCE_TICKET["status"],
                REFERENCE_TICKET["sla_hours"],
                REFERENCE_TICKET["assigned_master"],
                REFERENCE_TICKET["photo_urls_json"],
                REFERENCE_TICKET["status_history_json"],
                REFERENCE_TICKET["created_at"],
                REFERENCE_TICKET["updated_at"]
            ))

    def reset(self) -> None:
        """Resets the entire database to the pristine reference state."""
        self.seed_reference_data(force=True)

    def close(self) -> None:
        """Closes all connections."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None
        if self._root_conn is not None:
            try:
                self._root_conn.close()
            except Exception:
                pass
            self._root_conn = None


# Global singleton instance
db = Database()

def get_db() -> Database:
    """Dependency helper / accessor for Database instance."""
    return db
