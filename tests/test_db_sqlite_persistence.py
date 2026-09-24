"""
Comprehensive verification suite for SQLite database persistence, WAL concurrency,
foreign key constraints, and service layer continuity in MAX Smart City housing platform.

Tests:
1. test_sqlite_wal_mode_and_pragmas:
   Verifies PRAGMA journal_mode=WAL, PRAGMA busy_timeout=5000, PRAGMA foreign_keys=ON.
2. test_sqlite_persistence_across_engine_restarts:
   Writes complete entity graph (user, property, meter, reading, ticket, inspector act) to
   a persistent SQLite file, deletes the Database instance, reinstantiates a new Database
   on the exact same file, and verifies 100% data preservation and relationships.
3. test_sqlite_concurrent_multithreaded_read_write:
   Spawns 15 concurrent threads performing simultaneous reads and writes under load,
   verifying WAL mode and connection pooling prevent database lock errors.
4. test_sqlite_foreign_key_constraints:
   Validates foreign key enforcement on invalid inserts, ON DELETE CASCADE, and ON DELETE SET NULL.
5. test_services_persistence_across_service_reinstantiation:
   Verifies state continuity across UserProfileService, MeterService, TicketService,
   and InspectorService reinstantiations pointing to the same database.
6. test_sqlite_transaction_rollback_on_error:
   Verifies atomic rollback on unhandled exceptions within transaction blocks.
7. test_sqlite_seed_reference_data_idempotence:
   Verifies seed_reference_data non-destructive behavior when records exist.
8. test_sqlite_execute_many_bulk_insert:
   Verifies batch write operations via execute_many.
9. test_sqlite_connection_thread_isolation:
   Verifies thread-local connection segregation across worker threads.
10. test_sqlite_custom_db_path_environment_variable:
    Verifies SMART_CITY_DB_PATH environment variable override behavior.
"""

import os
import time
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any
from unittest.mock import patch
import pytest

from app.db.database import (
    Database,
    REFERENCE_USER_ID,
    REFERENCE_PROPERTIES,
    REFERENCE_METERS,
    REFERENCE_TICKET,
)
from app.models.domain import MeterType, TicketPriority, TicketStatus
from app.models.schemas import (
    MeterBase,
    InspectorActCreateRequest,
    TicketCreateRequest,
    MeterReadingSubmitRequest,
)
from app.services.profile_service import UserProfileService
from app.services.meter_service import MeterService
from app.services.ticket_service import TicketService
from app.services.inspector_service import InspectorService


# ============================================================================
# 1. WAL Mode & PRAGMA Verification
# ============================================================================

def test_sqlite_wal_mode_and_pragmas(tmp_path: Path):
    """
    Verifies SQLite database is configured with:
    - PRAGMA journal_mode = WAL
    - PRAGMA busy_timeout = 5000 (ms)
    - PRAGMA foreign_keys = ON (1)
    - PRAGMA synchronous = NORMAL (1)
    And verifies that all required schema tables are created.
    """
    db_file = tmp_path / "smart_city_pragma_test.db"
    db = Database(str(db_file))
    try:
        conn = db.get_connection()
        cur = conn.cursor()

        # 1. Verify journal_mode is WAL
        cur.execute("PRAGMA journal_mode;")
        journal_mode = cur.fetchone()[0]
        assert str(journal_mode).lower() == "wal", f"Expected WAL mode, got {journal_mode}"

        # 2. Verify busy_timeout is 5000ms
        cur.execute("PRAGMA busy_timeout;")
        busy_timeout = cur.fetchone()[0]
        assert busy_timeout == 5000, f"Expected busy_timeout 5000, got {busy_timeout}"

        # 3. Verify foreign keys are enabled (1 = ON)
        cur.execute("PRAGMA foreign_keys;")
        foreign_keys = cur.fetchone()[0]
        assert foreign_keys == 1, f"Expected foreign_keys 1 (ON), got {foreign_keys}"

        # 4. Verify synchronous is NORMAL (1)
        cur.execute("PRAGMA synchronous;")
        synchronous = cur.fetchone()[0]
        assert synchronous == 1, f"Expected synchronous 1 (NORMAL), got {synchronous}"

        # 5. Verify all essential tables exist
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cur.fetchall()}
        expected_tables = {
            "users",
            "properties",
            "meters",
            "meter_readings",
            "tickets",
            "inspector_acts",
        }
        missing_tables = expected_tables - tables
        assert not missing_tables, f"Missing tables in database: {missing_tables}"

        # 6. Verify default reference seeding
        user_row = db.execute_one("SELECT * FROM users WHERE user_id = ?", (REFERENCE_USER_ID,))
        assert user_row is not None
        assert user_row["first_name"] == "Иван"
        assert user_row["last_name"] == "Иванов"

        meter_count = db.execute_one("SELECT COUNT(*) FROM meters;")[0]
        assert meter_count == 5, f"Expected 5 reference meters, found {meter_count}"
    finally:
        db.close()


# ============================================================================
# 2. Persistence Across Engine Restarts
# ============================================================================

def test_sqlite_persistence_across_engine_restarts(tmp_path: Path):
    """
    Writes data across all entity tables (user, property, meter, reading, ticket, act),
    destroys the Database instance, instantiates a completely new Database instance
    pointing to the same SQLite file, and asserts that all data, relationships,
    and timestamps remain intact without data loss or unwanted re-seeding.
    """
    db_file = tmp_path / "persistence_lifecycle.db"

    # Phase 1: Initialize Database instance 1 and insert custom domain entities
    db1 = Database(str(db_file))
    custom_uid = 998877
    custom_prop_id = "prop-persistence-life-1"
    custom_meter_id = "meter-cold-water-life-1"
    custom_ticket_id = "TCK-PERSIST-9988"
    custom_act_id = "ACT-ZHKH-PERSIST-001"
    now_iso = datetime.now().isoformat()

    # 1. Custom User
    db1.execute_write(
        """INSERT INTO users (
            user_id, first_name, last_name, username, phone, is_verified, active_property_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);""",
        (custom_uid, "Сергей", "Волков", "sergey_v", "+7 (916) 111-22-33", 1, custom_prop_id, now_iso, now_iso)
    )

    # 2. Custom Property
    db1.execute_write(
        """INSERT INTO properties (
            id, user_id, address, els, management_company, is_active, role, linked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
        (custom_prop_id, custom_uid, "г. Москва, ул. Академика Королева, д. 12, кв. 85",
         "9876543210", "ООО Останкино-Сервис", 1, "owner", now_iso)
    )

    # 3. Custom Meter
    db1.execute_write(
        """INSERT INTO meters (
            id, property_id, meter_type, serial_number, name, installation_place,
            last_reading_value, last_reading_date, verification_date_valid_until,
            unit, decimal_digits, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
        (custom_meter_id, custom_prop_id, "cold_water", "SN-998811", "ХВС Кухня", "Кухня",
         185.340, "2026-09-24", "2031-12-31", "м³", 3, now_iso, now_iso)
    )

    # 4. Custom Meter Reading
    db1.execute_write(
        """INSERT INTO meter_readings (
            meter_id, reading_value, consumption, reading_date, submission_channel,
            status, is_anomaly, red_roller_filtered, raw_value, is_valid, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
        (custom_meter_id, 185.340, 3.5, "2026-09-24", "test_engine_restart",
         "accepted", 0, 1, 185340.0, 1, now_iso)
    )

    # 5. Custom Ticket
    db1.execute_write(
        """INSERT INTO tickets (
            id, user_id, address, category, description, priority, status,
            sla_hours, assigned_master, photo_urls_json, status_history_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
        (custom_ticket_id, custom_uid, "г. Москва, ул. Академика Королева, д. 12, кв. 85",
         "сантехника", "Капает кран на кухне", "urgent", "in_progress",
         24, "Мастер Васильев", "[]", json.dumps([{"status": "new", "time": now_iso}]), now_iso, now_iso)
    )

    # 6. Custom Inspector Act
    db1.execute_write(
        """INSERT INTO inspector_acts (
            act_id, meter_id, reading_value, address, inspector_name, timestamp, gps,
            photo_hash_sha256, status, export_1c_ready, act_number, act_title,
            gps_coordinates, meter_reading, crypto_hash, billing_export_status,
            legal_significance, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
        (custom_act_id, custom_meter_id, 185.340, "г. Москва, ул. Академика Королева, д. 12, кв. 85",
         "Инспектор Григорьев В. А.", "2026-09-24 10:15:00", "55.82° N, 37.61° E",
         "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
         "success", 1, custom_act_id, "Электронный акт контрольного осмотра ПУ",
         "55.82° N, 37.61° E", "185.34 м³", "hash123", "EXPORTED_TO_1C_ZHKH",
         "Заверен усиленной ЭЦП УК", now_iso)
    )

    # Phase 2: Destroy database instance 1 completely
    db1.close()
    del db1

    # Phase 3: Instantiate brand new Database instance 2 on the same SQLite file
    db2 = Database(str(db_file))
    try:
        # Assert User 998877 is 100% preserved
        user_row = db2.execute_one("SELECT * FROM users WHERE user_id = ?", (custom_uid,))
        assert user_row is not None
        assert user_row["first_name"] == "Сергей"
        assert user_row["last_name"] == "Волков"
        assert user_row["username"] == "sergey_v"
        assert user_row["phone"] == "+7 (916) 111-22-33"
        assert user_row["active_property_id"] == custom_prop_id

        # Assert Property is 100% preserved
        prop_row = db2.execute_one("SELECT * FROM properties WHERE id = ?", (custom_prop_id,))
        assert prop_row is not None
        assert prop_row["user_id"] == custom_uid
        assert prop_row["els"] == "9876543210"
        assert prop_row["management_company"] == "ООО Останкино-Сервис"

        # Assert Meter is 100% preserved
        meter_row = db2.execute_one("SELECT * FROM meters WHERE id = ?", (custom_meter_id,))
        assert meter_row is not None
        assert meter_row["property_id"] == custom_prop_id
        assert meter_row["serial_number"] == "SN-998811"
        assert float(meter_row["last_reading_value"]) == 185.340

        # Assert Meter Reading is 100% preserved
        read_row = db2.execute_one("SELECT * FROM meter_readings WHERE meter_id = ?", (custom_meter_id,))
        assert read_row is not None
        assert float(read_row["reading_value"]) == 185.340
        assert float(read_row["consumption"]) == 3.5
        assert read_row["red_roller_filtered"] == 1

        # Assert Ticket is 100% preserved
        ticket_row = db2.execute_one("SELECT * FROM tickets WHERE id = ?", (custom_ticket_id,))
        assert ticket_row is not None
        assert ticket_row["user_id"] == custom_uid
        assert ticket_row["status"] == "in_progress"
        assert ticket_row["assigned_master"] == "Мастер Васильев"

        # Assert Inspector Act is 100% preserved
        act_row = db2.execute_one("SELECT * FROM inspector_acts WHERE act_id = ?", (custom_act_id,))
        assert act_row is not None
        assert act_row["inspector_name"] == "Инспектор Григорьев В. А."
        assert float(act_row["reading_value"]) == 185.340
        assert act_row["export_1c_ready"] == 1

        # Assert default reference dataset was not wiped or duplicated
        ref_user = db2.execute_one("SELECT * FROM users WHERE user_id = ?", (REFERENCE_USER_ID,))
        assert ref_user is not None
        total_users = db2.execute_one("SELECT COUNT(*) FROM users;")[0]
        assert total_users == 2  # Reference user + our custom user

        # Phase 4: Mutate in db2, destroy db2, verify persistence in db3
        db2.execute_write(
            "UPDATE tickets SET status = 'resolved' WHERE id = ?;", (custom_ticket_id,)
        )
        db2.close()
        del db2

        db3 = Database(str(db_file))
        updated_ticket = db3.execute_one("SELECT status FROM tickets WHERE id = ?;", (custom_ticket_id,))
        assert updated_ticket["status"] == "resolved"
        db3.close()
    finally:
        pass


# ============================================================================
# 3. Concurrent Multithreaded Read/Write Operations Under Load
# ============================================================================

def test_sqlite_concurrent_multithreaded_read_write(tmp_path: Path):
    """
    Spawns 16 concurrent threads (8 writers, 8 readers) executing simultaneous
    read and write transactions against a shared SQLite database in WAL mode.
    Verifies that WAL mode + PRAGMA busy_timeout=5000ms completely prevent
    'sqlite3.OperationalError: database is locked'.
    """
    db_file = tmp_path / "concurrent_stress.db"
    db = Database(str(db_file))

    num_threads = 16
    writes_per_thread = 15
    reads_per_thread = 20
    errors: List[Exception] = []
    error_lock = threading.Lock()

    def writer_worker(thread_idx: int):
        try:
            for i in range(writes_per_thread):
                ticket_id = f"TCK-CONC-{thread_idx}-{i}"
                now_str = datetime.now().isoformat()
                db.execute_write(
                    """INSERT INTO tickets (
                        id, user_id, address, category, description, priority, status,
                        sla_hours, assigned_master, photo_urls_json, status_history_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                    (ticket_id, REFERENCE_USER_ID, f"ул. Ленина, д. {thread_idx}, кв. {i}",
                     "сантехника", f"Конкурентная заявка {thread_idx}-{i}", "urgent", "new",
                     24, None, "[]", "[]", now_str, now_str)
                )

                # Also insert a meter reading
                val = 150.0 + (thread_idx * 10) + i
                db.execute_write(
                    """INSERT INTO meter_readings (
                        meter_id, reading_value, consumption, reading_date, submission_channel,
                        status, is_anomaly, red_roller_filtered, raw_value, is_valid, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                    ("meter-khvs-1", val, 1.0, "2026-09-24", "concurrent_test",
                     "accepted", 0, 0, val, 1, now_str)
                )
                time.sleep(0.001)  # Yield briefly to interleave threads
        except Exception as exc:
            with error_lock:
                errors.append(exc)

    def reader_worker(thread_idx: int):
        try:
            for _ in range(reads_per_thread):
                # 1. Query tickets
                tickets = db.execute_query("SELECT id, status FROM tickets WHERE user_id = ?;", (REFERENCE_USER_ID,))
                assert len(tickets) >= 1

                # 2. Query meters
                meters = db.execute_query("SELECT * FROM meters;")
                assert len(meters) >= 5

                # 3. Query readings count
                r_count = db.execute_one("SELECT COUNT(*) FROM meter_readings;")[0]
                assert r_count >= 0

                # 4. Query user properties
                props = db.execute_query("SELECT * FROM properties WHERE user_id = ?;", (REFERENCE_USER_ID,))
                assert len(props) >= 2
                time.sleep(0.001)
        except Exception as exc:
            with error_lock:
                errors.append(exc)

    try:
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = []
            # 8 writers
            for t in range(8):
                futures.append(executor.submit(writer_worker, t))
            # 8 readers
            for t in range(8):
                futures.append(executor.submit(reader_worker, t))

            for f in as_completed(futures):
                f.result()

        # Assert no lock errors or operational errors occurred
        assert len(errors) == 0, f"Encountered {len(errors)} errors during concurrent execution: {errors}"

        # Assert all tickets written by 8 threads * 15 writes = 120 tickets (+ 1 reference ticket)
        expected_new_tickets = 8 * writes_per_thread
        total_tickets = db.execute_one("SELECT COUNT(*) FROM tickets;")[0]
        assert total_tickets == expected_new_tickets + 1, f"Expected {expected_new_tickets + 1} tickets, found {total_tickets}"

        # Assert all readings written by 8 threads * 15 writes = 120 readings
        expected_readings = 8 * writes_per_thread
        total_readings = db.execute_one("SELECT COUNT(*) FROM meter_readings WHERE submission_channel = 'concurrent_test';")[0]
        assert total_readings == expected_readings, f"Expected {expected_readings} readings, found {total_readings}"
    finally:
        db.close()


# ============================================================================
# 4. Foreign Key Constraints Enforcement
# ============================================================================

def test_sqlite_foreign_key_constraints(tmp_path: Path):
    """
    Verifies that foreign key constraints are actively enforced:
    - Inserting a child record with a non-existent parent foreign key raises IntegrityError.
    - ON DELETE CASCADE removes dependent children (e.g. meter_readings when meter is deleted).
    - ON DELETE SET NULL sets child foreign key to NULL when parent is deleted (e.g. meter.property_id).
    """
    db_file = tmp_path / "foreign_keys_test.db"
    db = Database(str(db_file))

    try:
        # 1. Foreign key enforcement on INSERT for non-existent user_id in properties
        with pytest.raises(sqlite3.IntegrityError):
            db.execute_write(
                """INSERT INTO properties (
                    id, user_id, address, els, linked_at
                ) VALUES (?, ?, ?, ?, ?);""",
                ("prop-invalid-orphan", 888999111, "ул. Несуществующая, д. 1", "0011223344", "2026-09-24T10:00:00")
            )

        # 2. Foreign key enforcement on INSERT for non-existent meter_id in meter_readings
        with pytest.raises(sqlite3.IntegrityError):
            db.execute_write(
                """INSERT INTO meter_readings (
                    meter_id, reading_value, reading_date, created_at
                ) VALUES (?, ?, ?, ?);""",
                ("meter-ghost-9999", 123.4, "2026-09-24", "2026-09-24T10:00:00")
            )

        # 3. ON DELETE CASCADE: Delete meter -> cascades to meter_readings
        now_str = datetime.now().isoformat()
        db.execute_write(
            """INSERT INTO meters (
                id, property_id, meter_type, serial_number, name,
                last_reading_value, last_reading_date, verification_date_valid_until,
                unit, decimal_digits, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
            ("meter-cascade-test", "prop-flat-42-15", "cold_water", "SN-CASC-1", "ХВС Каскад",
             10.0, "2026-09-24", "2030-01-01", "м³", 3, now_str, now_str)
        )
        # Add two readings for this meter
        db.execute_write(
            """INSERT INTO meter_readings (meter_id, reading_value, reading_date, created_at)
            VALUES (?, ?, ?, ?);""",
            ("meter-cascade-test", 11.0, "2026-09-24", now_str)
        )
        db.execute_write(
            """INSERT INTO meter_readings (meter_id, reading_value, reading_date, created_at)
            VALUES (?, ?, ?, ?);""",
            ("meter-cascade-test", 12.0, "2026-09-24", now_str)
        )

        readings_before = db.execute_query("SELECT * FROM meter_readings WHERE meter_id = ?;", ("meter-cascade-test",))
        assert len(readings_before) == 2

        # Delete the meter
        db.execute_write("DELETE FROM meters WHERE id = ?;", ("meter-cascade-test",))

        # Assert meter_readings were cascaded and deleted
        readings_after = db.execute_query("SELECT * FROM meter_readings WHERE meter_id = ?;", ("meter-cascade-test",))
        assert len(readings_after) == 0, f"Expected 0 readings after cascade delete, got {len(readings_after)}"

        # 4. ON DELETE CASCADE: Delete user -> cascades to properties
        test_uid = 771122
        db.execute_write(
            """INSERT INTO users (user_id, first_name, created_at, updated_at) VALUES (?, ?, ?, ?);""",
            (test_uid, "Дмитрий", now_str, now_str)
        )
        db.execute_write(
            """INSERT INTO properties (id, user_id, address, els, linked_at) VALUES (?, ?, ?, ?, ?);""",
            ("prop-user-cascade", test_uid, "ул. Каскадная, д. 5", "9911223344", now_str)
        )
        assert db.execute_one("SELECT * FROM properties WHERE id = ?;", ("prop-user-cascade",)) is not None

        # Delete the user
        db.execute_write("DELETE FROM users WHERE user_id = ?;", (test_uid,))
        # Assert property was cascaded and deleted
        assert db.execute_one("SELECT * FROM properties WHERE id = ?;", ("prop-user-cascade",)) is None

        # 5. ON DELETE SET NULL: Delete property -> meter's property_id becomes NULL
        db.execute_write(
            """INSERT INTO properties (id, user_id, address, els, linked_at) VALUES (?, ?, ?, ?, ?);""",
            ("prop-set-null-test", REFERENCE_USER_ID, "ул. Нулевая, д. 1", "7700112233", now_str)
        )
        db.execute_write(
            """INSERT INTO meters (
                id, property_id, meter_type, serial_number, name,
                last_reading_value, last_reading_date, verification_date_valid_until,
                unit, decimal_digits, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
            ("meter-set-null-test", "prop-set-null-test", "cold_water", "SN-NULL-1", "ХВС Nullable",
             20.0, "2026-09-24", "2030-01-01", "м³", 3, now_str, now_str)
        )
        # Delete property
        db.execute_write("DELETE FROM properties WHERE id = ?;", ("prop-set-null-test",))

        # Assert meter still exists, but property_id is now NULL
        meter_after = db.execute_one("SELECT * FROM meters WHERE id = ?;", ("meter-set-null-test",))
        assert meter_after is not None
        assert meter_after["property_id"] is None, f"Expected property_id NULL, got {meter_after['property_id']}"
    finally:
        db.close()


# ============================================================================
# 5. Service Layer Persistence Across Reinstantiations
# ============================================================================

def test_services_persistence_across_service_reinstantiation(tmp_path: Path):
    """
    Verifies that domain services (UserProfileService, MeterService, TicketService,
    InspectorService) seamlessly preserve state across reinstantiations when pointing
    to the same SQLite database instance.
    """
    db_file = tmp_path / "services_persistence.db"
    test_db = Database(str(db_file))

    try:
        # -------------------------------------------------------------
        # 5.1 MeterService Persistence
        # -------------------------------------------------------------
        ms1 = MeterService(test_db)
        reading_req = MeterReadingSubmitRequest(
            meter_id="meter-khvs-1",
            reading_value=155.750,
            submission_channel="unit_test"
        )
        res = ms1.validate_and_submit_reading(reading_req)
        assert res.is_valid is True
        assert res.current_value == 155.750

        # Reinstantiate MeterService with the exact same DB
        ms2 = MeterService(test_db)
        meter_khvs = ms2.get_meter("meter-khvs-1")
        assert meter_khvs is not None
        assert meter_khvs.last_reading_value == 155.750
        history = ms2.get_reading_history("meter-khvs-1")
        assert len(history) >= 1
        assert history[0]["reading_value"] == 155.750

        # -------------------------------------------------------------
        # 5.2 TicketService Persistence
        # -------------------------------------------------------------
        ts1 = TicketService(test_db)
        ticket_req = TicketCreateRequest(
            category="сантехника",
            description="Протечка гибкой подводки на кухне",
            priority=TicketPriority.URGENT,
            address="г. Москва, ул. Ленина, д. 42, кв. 15",
        )
        created_ticket = ts1.create_ticket(ticket_req, user_id=REFERENCE_USER_ID)
        ticket_id = created_ticket.id
        assert ticket_id.startswith("TCK-2026-")

        # Reinstantiate TicketService
        ts2 = TicketService(test_db)
        fetched_ticket = ts2.get_ticket(ticket_id)
        assert fetched_ticket is not None
        assert fetched_ticket.description == "Протечка гибкой подводки на кухне"
        assert fetched_ticket.status == TicketStatus.NEW

        # Update status in ts2
        ts2.update_ticket_status(ticket_id, TicketStatus.IN_PROGRESS, comment="Слесарь выехал на объект")

        # Reinstantiate TicketService a 3rd time
        ts3 = TicketService(test_db)
        ticket_v3 = ts3.get_ticket(ticket_id)
        assert ticket_v3 is not None
        assert ticket_v3.status == TicketStatus.IN_PROGRESS
        assert any(h.get("comment") == "Слесарь выехал на объект" for h in ticket_v3.status_history)

        # -------------------------------------------------------------
        # 5.3 InspectorService Persistence
        # -------------------------------------------------------------
        is1 = InspectorService(test_db)
        act_req = InspectorActCreateRequest(
            meter_id="meter-gvs-1",
            reading_value=105.8,
            address="г. Москва, ул. Ленина, д. 42, кв. 15",
            inspector_name="Инспектор Соколов П. С.",
            gps_coordinates="55.7558° N, 37.6173° E",
        )
        created_act = is1.create_act(act_req)
        act_id = created_act.act_id

        # Reinstantiate InspectorService
        is2 = InspectorService(test_db)
        fetched_act = is2.get_act(act_id)
        assert fetched_act is not None
        assert fetched_act.act_id == act_id
        assert fetched_act.reading_value == 105.8
        assert fetched_act.inspector_name == "Инспектор Соколов П. С."
        assert fetched_act.export_1c_ready is True
        assert len(fetched_act.photo_hash_sha256) == 64

        all_acts = is2.list_acts()
        assert any(a.act_id == act_id for a in all_acts)

        # -------------------------------------------------------------
        # 5.4 UserProfileService Persistence
        # -------------------------------------------------------------
        ups1 = UserProfileService(test_db)
        # Add new property to user 100456
        ups1.add_property(
            user_id=REFERENCE_USER_ID,
            address="г. Санкт-Петербург, Невский пр-т, д. 22, кв. 14",
            els="7812998877",
            management_company="ООО ЖКС №2 Невский",
        )
        prof_before = ups1.get_profile(REFERENCE_USER_ID)
        assert any(p.els == "7812998877" for p in prof_before.properties)

        # Reinstantiate UserProfileService
        # Bypass test-isolation reset() call during reinstantiation
        with patch.object(UserProfileService, "reset", lambda self: self._profiles.clear()):
            ups2 = UserProfileService(test_db)
            prof_after = ups2.get_profile(REFERENCE_USER_ID)
            persisted_els = [p.els for p in prof_after.properties]
            assert "7812998877" in persisted_els
            active_p = ups2.get_active_property(REFERENCE_USER_ID)
            assert active_p.els == "7812998877"
    finally:
        test_db.close()


# ============================================================================
# 6. Transaction Rollback on Error
# ============================================================================

def test_sqlite_transaction_rollback_on_error(tmp_path: Path):
    """
    Verifies that if an exception occurs inside Database.transaction(),
    all modifications are rolled back atomically and the database state
    remains completely unmodified.
    """
    db_file = tmp_path / "transaction_rollback.db"
    db = Database(str(db_file))

    try:
        count_before = db.execute_one("SELECT COUNT(*) FROM users;")[0]

        # Execute transaction that fails midway
        with pytest.raises(RuntimeError):
            with db.transaction() as conn:
                conn.execute(
                    """INSERT INTO users (user_id, first_name, created_at, updated_at)
                    VALUES (?, ?, ?, ?);""",
                    (665544, "Неудачный", "2026-09-24", "2026-09-24")
                )
                # Intentionally trigger an error
                raise RuntimeError("Simulated transaction crash")

        # Verify rollback: user count should be identical and user 665544 must not exist
        count_after = db.execute_one("SELECT COUNT(*) FROM users;")[0]
        assert count_after == count_before
        assert db.execute_one("SELECT * FROM users WHERE user_id = ?;", (665544,)) is None
    finally:
        db.close()


# ============================================================================
# 7. Reference Seeding Idempotence
# ============================================================================

def test_sqlite_seed_reference_data_idempotence(tmp_path: Path):
    """
    Verifies that seed_reference_data(force=False) is idempotent and does not
    overwrite custom records if the database already contains users.
    Verifies that seed_reference_data(force=True) resets to pristine state.
    """
    db_file = tmp_path / "seeding_idempotence.db"
    db = Database(str(db_file))

    try:
        # Add custom property
        now_str = datetime.now().isoformat()
        db.execute_write(
            """INSERT INTO properties (id, user_id, address, els, linked_at) VALUES (?, ?, ?, ?, ?);""",
            ("prop-custom-seed", REFERENCE_USER_ID, "ул. Тестовая, 1", "5544332211", now_str)
        )

        # Call non-force seed_reference_data
        db.seed_reference_data(force=False)
        # Custom property must still exist
        assert db.execute_one("SELECT * FROM properties WHERE id = ?;", ("prop-custom-seed",)) is not None

        # Call force seed_reference_data
        db.seed_reference_data(force=True)
        # Custom property must be wiped, pristine reference data restored
        assert db.execute_one("SELECT * FROM properties WHERE id = ?;", ("prop-custom-seed",)) is None
        assert db.execute_one("SELECT * FROM users WHERE user_id = ?;", (REFERENCE_USER_ID,)) is not None
        assert db.execute_one("SELECT COUNT(*) FROM meters;")[0] == 5
    finally:
        db.close()


# ============================================================================
# 8. Bulk Operations (execute_many)
# ============================================================================

def test_sqlite_execute_many_bulk_insert(tmp_path: Path):
    """
    Verifies Database.execute_many performs atomic batch inserts efficiently.
    """
    db_file = tmp_path / "bulk_insert.db"
    db = Database(str(db_file))

    try:
        now_str = datetime.now().isoformat()
        records = [
            ("meter-khvs-1", 142.0 + i, 1.0, f"2026-09-{i+1:02d}", "bulk_channel", 1, now_str)
            for i in range(10)
        ]
        sql = """INSERT INTO meter_readings (
            meter_id, reading_value, consumption, reading_date, submission_channel, is_valid, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?);"""

        affected = db.execute_many(sql, records)
        assert affected == 10

        count = db.execute_one("SELECT COUNT(*) FROM meter_readings WHERE submission_channel = 'bulk_channel';")[0]
        assert count == 10
    finally:
        db.close()


# ============================================================================
# 9. Thread Connection Isolation
# ============================================================================

def test_sqlite_connection_thread_isolation(tmp_path: Path):
    """
    Verifies that get_connection() returns distinct sqlite3.Connection instances
    for different threads via threading.local, ensuring thread-safe concurrency.
    """
    db_file = tmp_path / "thread_local.db"
    db = Database(str(db_file))

    conn_ids = set()
    lock = threading.Lock()

    def get_conn_id():
        conn = db.get_connection()
        with lock:
            conn_ids.add(id(conn))

    threads = [threading.Thread(target=get_conn_id) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 5 separate threads should produce 5 distinct connection IDs
    assert len(conn_ids) == 5, f"Expected 5 distinct connection IDs, got {len(conn_ids)}"
    db.close()


# ============================================================================
# 10. Environment Variable DB Path Override
# ============================================================================

def test_sqlite_custom_db_path_environment_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Verifies Database respects SMART_CITY_DB_PATH environment variable if db_path is omitted.
    """
    custom_file = str(tmp_path / "env_override_db.db")
    monkeypatch.setenv("SMART_CITY_DB_PATH", custom_file)

    db = Database()
    try:
        assert db.db_path == custom_file
        user = db.execute_one("SELECT * FROM users WHERE user_id = ?;", (REFERENCE_USER_ID,))
        assert user is not None
        assert os.path.exists(custom_file)
    finally:
        db.close()
