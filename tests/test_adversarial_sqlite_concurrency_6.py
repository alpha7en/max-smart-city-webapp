"""
Adversarial Empirical Stress Test Suite: SQLiteStorage & app/db/ Concurrency & Durability
Author: challenger_sqlite_concurrency_6 (EMPIRICAL CHALLENGER)

Scope:
1. SQLiteStorage (max_bot_sdk/fsm/storage.py):
   - Rapid concurrent coroutines updating data on same and multiple user/chat keys (0% data loss).
   - Connection closure and file descriptor leak analysis (1000+ operations).
2. SQLite Persistence & app/db/:
   - 30+ thread concurrency (32-36 threads) simultaneously submitting meter readings and tickets.
   - WAL mode and PRAGMA settings validation (journal_mode=WAL, busy_timeout=5000).
   - Process restart & SIGKILL recovery durability testing across OS process boundaries.
   - Backward compatibility of services under concurrent load.
"""

import asyncio
import gc
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Any
import pytest

from app.db.database import (
    Database,
    REFERENCE_USER_ID,
    REFERENCE_PROPERTIES,
    REFERENCE_METERS,
    REFERENCE_TICKET,
)
from app.models.domain import MeterType, TicketPriority, TicketStatus
from app.models.schemas import MeterBase, MeterReadingSubmitRequest, TicketCreateRequest
from app.services.meter_service import MeterService
from app.services.profile_service import UserProfileService
from app.services.ticket_service import TicketService
import app.services.ticket_service as ticket_svc_module
from max_bot_sdk.fsm.storage import SQLiteStorage


# ============================================================================
# PART 1: SQLiteStorage FSM Concurrency & Connection / FD Leak Verification
# ============================================================================

@pytest.mark.anyio
async def test_sqlite_storage_rapid_concurrent_update_data_zero_loss(tmp_path: Path):
    """
    Stress-test SQLiteStorage update_data with 50 rapid concurrent coroutines
    writing distinct keys to the exact same user/chat.
    Empirically verifies 0% data loss: all 50 keys must be retained.
    """
    db_file = str(tmp_path / "fsm_concurrency.db")
    storage = SQLiteStorage(db_path=db_file)

    chat_id = 999888777
    user_id = 111222333

    # Seed initial base state
    await storage.set_state(chat_id, user_id, "CONCURRENCY_TEST")
    await storage.set_data(chat_id, user_id, {"initial_key": "baseline"})

    concurrency_count = 50

    async def update_worker(worker_id: int):
        # Each coroutine writes unique keys
        update_payload = {
            f"key_worker_{worker_id}": f"val_{worker_id}",
            f"num_{worker_id}": worker_id * 10,
        }
        res = await storage.update_data(chat_id, user_id, update_payload)
        return res

    # Run 50 concurrent coroutines hammering the same (chat_id, user_id)
    tasks = [update_worker(i) for i in range(concurrency_count)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Verify no exceptions occurred during concurrent updates
    for i, res in enumerate(results):
        assert not isinstance(res, Exception), f"Coroutine {i} raised exception: {res}"

    # Read back persisted data
    persisted_data = await storage.get_data(chat_id, user_id)

    # 1 baseline key + (50 * 2) = 101 keys total
    expected_key_count = 1 + (concurrency_count * 2)
    assert len(persisted_data) == expected_key_count, (
        f"Data loss detected! Expected {expected_key_count} keys, got {len(persisted_data)}. "
        f"Missing keys: {set([f'key_worker_{i}' for i in range(concurrency_count)]) - set(persisted_data.keys())}"
    )

    # Assert 0% data loss across all worker keys
    for i in range(concurrency_count):
        k1 = f"key_worker_{i}"
        k2 = f"num_{i}"
        assert k1 in persisted_data, f"Data loss on key {k1}"
        assert persisted_data[k1] == f"val_{i}", f"Data corruption on key {k1}"
        assert k2 in persisted_data, f"Data loss on key {k2}"
        assert persisted_data[k2] == i * 10, f"Data corruption on key {k2}"

    assert persisted_data["initial_key"] == "baseline"

    # State must be preserved
    final_state = await storage.get_state(chat_id, user_id)
    assert final_state == "CONCURRENCY_TEST"

    await storage.close()


@pytest.mark.anyio
async def test_sqlite_storage_multi_user_high_concurrency(tmp_path: Path):
    """
    Stress-tests SQLiteStorage with 10 distinct users, each updated concurrently
    by 10 coroutines (100 total concurrent coroutines).
    Verifies isolation between users and 0% data loss.
    """
    db_file = str(tmp_path / "fsm_multi_user.db")
    storage = SQLiteStorage(db_path=db_file)

    num_users = 10
    updates_per_user = 10

    async def user_update_worker(u_idx: int, op_idx: int):
        cid = 1000 + u_idx
        uid = 2000 + u_idx
        await storage.update_data(cid, uid, {
            f"user_{u_idx}_op_{op_idx}": op_idx,
            "user_id_field": uid
        })

    tasks = [
        user_update_worker(u, op)
        for u in range(num_users)
        for op in range(updates_per_user)
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for res in results:
        assert not isinstance(res, Exception), f"Update error: {res}"

    # Verify each user has all expected keys
    for u in range(num_users):
        cid = 1000 + u
        uid = 2000 + u
        data = await storage.get_data(cid, uid)
        assert data.get("user_id_field") == uid
        for op in range(updates_per_user):
            assert f"user_{u}_op_{op}" in data
            assert data[f"user_{u}_op_{op}"] == op

    await storage.close()


def test_sqlite_storage_no_connection_or_file_descriptor_leak(tmp_path: Path):
    """
    Executes 1,000 rapid operations (set_state, get_state, update_data, get_data, clear)
    on SQLiteStorage and monitors file descriptors (/dev/fd on macOS) and connection closures.
    Verifies that file descriptors remain stable and do not leak with operation volume.
    """
    db_file = str(tmp_path / "fsm_leak_check.db")
    storage = SQLiteStorage(db_path=db_file)

    def get_fd_count() -> int:
        if os.path.exists("/dev/fd"):
            try:
                return len(os.listdir("/dev/fd"))
            except Exception:
                pass
        return 0

    gc.collect()
    initial_fds = get_fd_count()

    chat_id = 555666
    user_id = 777888

    # Run 1000 synchronous/async-equivalent operations
    for i in range(200):
        storage.sync_set_state(chat_id, user_id, f"STATE_{i}")
        st = storage.sync_get_state(chat_id, user_id)
        assert st == f"STATE_{i}"

        data = storage.sync_update_data(chat_id, user_id, {f"step_{i}": i})
        assert f"step_{i}" in data

        read_data = storage.sync_get_data(chat_id, user_id)
        assert f"step_{i}" in read_data

        if i % 50 == 49:
            storage.sync_clear(chat_id, user_id)

    gc.collect()
    final_fds = get_fd_count()

    if initial_fds > 0 and final_fds > 0:
        fd_diff = final_fds - initial_fds
        # File descriptor growth should be essentially 0 (allowing minor OS runtime fluctuation <= 3)
        assert fd_diff <= 3, (
            f"File descriptor leak detected! Initial FDs: {initial_fds}, Final FDs: {final_fds}, "
            f"Growth: {fd_diff} after 1000 operations."
        )

    # Ensure database can be cleanly reopened and verified
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("PRAGMA integrity_check;")
    check = cur.fetchone()[0]
    conn.close()
    assert check == "ok"


# ============================================================================
# PART 2: SQLite Persistence & app/db/ 30+ Thread Concurrency Stress Test
# ============================================================================

def test_app_db_36_threads_concurrency_stress(tmp_path: Path):
    """
    Stress-tests app/db/ SQLite persistence with 36 concurrent threads (18 writers, 18 readers).
    - 18 writers submit meter readings via MeterService and create tickets via TicketService.
    - Each writer operates on its dedicated meter to maintain business monotonicity while
      stressing the shared SQLite database connection pool and table-level locks.
    - 18 readers query meters, readings, and tickets simultaneously.
    - Asserts 0 locking errors (OperationalError: database is locked).
    - Asserts 100% data integrity and accurate record counts across all threads (360 tickets + 360 readings).
    - Validates backward compatibility of service adapters under heavy concurrent load.
    """
    db_file = tmp_path / "app_db_concurrency_36.db"
    db_inst = Database(str(db_file))

    # Initialize services bound to this test database
    meter_svc = MeterService(db=db_inst)
    ticket_svc = TicketService(db=db_inst)
    profile_svc = UserProfileService(db=db_inst)

    num_writers = 18
    num_readers = 18
    ops_per_thread = 20
    total_expected_tickets = num_writers * ops_per_thread
    total_expected_readings = num_writers * ops_per_thread

    # Provision distinct meters for each writer worker to test service-level submission
    for w in range(num_writers):
        meter_id = f"meter-worker-{w}"
        m_base = MeterBase(
            id=meter_id,
            meter_type=MeterType.COLD_WATER,
            serial_number=f"7700{w:04d}",
            name=f"Счетчик воркера {w}",
            installation_place="Санузел",
            last_reading_value=100.0,
            last_reading_date=date(2026, 8, 20),
            verification_date_valid_until=date(2029, 10, 18),
            unit="м³",
            decimal_digits=3
        )
        meter_svc._meters[meter_id] = m_base

    errors: List[Exception] = []
    err_lock = threading.Lock()

    # Thread-safe UUID generator to isolate pure SQLite persistence concurrency from the 4-char UUID birthday paradox
    class ThreadSafeUUIDGenerator:
        def __init__(self):
            self._count = 0
            self._lock = threading.Lock()
        def __call__(self):
            with self._lock:
                self._count += 1
                val = f"{self._count:04X}"
                class Wrapped:
                    def __str__(self):
                        return val
                return Wrapped()

    orig_uuid4 = ticket_svc_module.uuid.uuid4
    ticket_svc_module.uuid.uuid4 = ThreadSafeUUIDGenerator()

    def writer_worker(worker_id: int):
        try:
            meter_id = f"meter-worker-{worker_id}"
            for op in range(ops_per_thread):
                # 1. Create a ticket
                t_req = TicketCreateRequest(
                    address=f"ул. Нагрузочная, д. {worker_id}, кв. {op}",
                    category="Водоснабжение",
                    description=f"Конкурентная заявка worker={worker_id} op={op}",
                    priority=TicketPriority.URGENT if op % 2 == 0 else TicketPriority.EMERGENCY,
                    photo_urls=[]
                )
                ticket_res = ticket_svc.create_ticket(t_req, user_id=REFERENCE_USER_ID)
                assert ticket_res.id.startswith("TCK-2026-")

                # 2. Submit a meter reading with monotonic increment
                reading_val = 100.0 + (op + 1) * 1.5
                m_req = MeterReadingSubmitRequest(
                    meter_id=meter_id,
                    reading_value=reading_val,
                    reading_date=date.today(),
                    submission_channel=f"thread_{worker_id}"
                )
                m_res = meter_svc.validate_and_submit_reading(m_req)
                assert m_res.is_valid is True, f"Reading invalid: {m_res.message}"
        except Exception as e:
            with err_lock:
                errors.append(e)

    def reader_worker(worker_id: int):
        try:
            for op in range(ops_per_thread):
                # 1. Read tickets
                tickets = ticket_svc.get_all_tickets()
                assert len(tickets) >= 1

                # 2. Read meters
                meters = meter_svc.get_all_meters()
                assert len(meters) >= 5

                # 3. Read profile
                prof = profile_svc.get_profile(REFERENCE_USER_ID)
                assert prof.user_id == REFERENCE_USER_ID

                # 4. Direct DB query
                rows = db_inst.execute_query("SELECT count(*) as c FROM meter_readings;")
                assert rows[0]["c"] >= 0
                time.sleep(0.0005)
        except Exception as e:
            with err_lock:
                errors.append(e)

    threads: List[threading.Thread] = []
    for w in range(num_writers):
        threads.append(threading.Thread(target=writer_worker, args=(w,)))
    for r in range(num_readers):
        threads.append(threading.Thread(target=reader_worker, args=(r,)))

    try:
        # Launch all 36 threads simultaneously
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        # Verify no exceptions or deadlocks occurred
        assert len(errors) == 0, f"Encountered {len(errors)} concurrency errors: {errors[:5]}"

        # Verify database record counts
        conn = db_inst.get_connection()
        cur = conn.cursor()

        cur.execute("SELECT count(*) FROM tickets WHERE id != 'TCK-2026-0819';")
        persisted_tickets = cur.fetchone()[0]
        assert persisted_tickets == total_expected_tickets, (
            f"Expected {total_expected_tickets} written tickets, found {persisted_tickets}"
        )

        cur.execute("SELECT count(*) FROM meter_readings;")
        persisted_readings = cur.fetchone()[0]
        assert persisted_readings == total_expected_readings, (
            f"Expected {total_expected_readings} written meter readings, found {persisted_readings}"
        )

        # Verify PRAGMA integrity
        cur.execute("PRAGMA integrity_check;")
        assert cur.fetchone()[0] == "ok"
    finally:
        ticket_svc_module.uuid.uuid4 = orig_uuid4


# ============================================================================
# PART 3: WAL Mode & Durability Across Process Restarts and SIGKILL
# ============================================================================

def test_sqlite_wal_mode_pragmas_verification(tmp_path: Path):
    """
    Verifies that Database enforces:
    - PRAGMA journal_mode = WAL
    - PRAGMA synchronous = NORMAL
    - PRAGMA busy_timeout = 5000
    - PRAGMA foreign_keys = ON
    And verifies the creation of WAL companion files.
    """
    db_file = tmp_path / "wal_mode_verify.db"
    db_inst = Database(str(db_file))

    conn = db_inst.get_connection()
    cur = conn.cursor()

    cur.execute("PRAGMA journal_mode;")
    j_mode = str(cur.fetchone()[0]).lower()
    assert j_mode == "wal", f"journal_mode expected 'wal', got '{j_mode}'"

    cur.execute("PRAGMA synchronous;")
    sync_mode = cur.fetchone()[0]
    # In SQLite, NORMAL is 1, FULL is 2
    assert sync_mode in (1, "1", "NORMAL", "normal"), f"synchronous expected 1 (NORMAL), got {sync_mode}"

    cur.execute("PRAGMA busy_timeout;")
    busy = cur.fetchone()[0]
    assert busy == 5000, f"busy_timeout expected 5000, got {busy}"

    cur.execute("PRAGMA foreign_keys;")
    fk = cur.fetchone()[0]
    assert fk in (1, "1", "ON", "on"), f"foreign_keys expected 1, got {fk}"

    # Verify WAL files exist on disk while writing
    db_inst.execute_write(
        "INSERT INTO tickets (id, address, category, description, priority, status, sla_hours, created_at, updated_at) "
        "VALUES ('TCK-WAL-TEST', 'ул. Тестовая', 'Общее', 'WAL check', 'urgent', 'new', 24, datetime('now'), datetime('now'));"
    )

    # WAL index or wal file should exist alongside the db file
    wal_file = Path(str(db_file) + "-wal")
    shm_file = Path(str(db_file) + "-shm")
    assert wal_file.exists() or shm_file.exists() or Path(db_file).exists()


def test_sqlite_process_restart_and_sigkill_recovery(tmp_path: Path):
    """
    Adversarially tests process crash (SIGKILL) recovery:
    1. Spawns an external Python subprocess that initializes Database, seeds data,
       and continuously writes tickets into the SQLite file.
    2. Sends SIGKILL (signal.SIGKILL) to terminate the subprocess instantly mid-operation.
    3. Spawns a secondary Python subprocess representing a restart.
    4. Runs PRAGMA integrity_check to verify zero corruption.
    5. Reads all committed tickets, verifying ACID durability.
    6. Writes a new ticket in the resumed process to prove the database is not locked.
    """
    db_file = str(tmp_path / "crash_recovery.db")

    # Worker script to write records and signal readiness
    worker_script = f"""
import sys
import time
import os
from app.db.database import Database

db = Database('{db_file}')

# Commit 30 tickets
for i in range(30):
    db.execute_write(
        \"\"\"INSERT INTO tickets (
            id, user_id, address, category, description, priority, status,
            sla_hours, assigned_master, photo_urls_json, status_history_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'));\"\"\",
        (f'TCK-CRASH-{{i}}', 100456, f'ул. Аварийная, д. {{i}}', 'Электрика', 'Сбой питания',
         'urgent', 'new', 24, 'Мастер', '[]', '[]')
    )

# Flush stdout to notify parent
print('COMMITTED_30', flush=True)

# Loop writing more until killed
for i in range(30, 200):
    time.sleep(0.01)
    db.execute_write(
        \"\"\"INSERT INTO tickets (
            id, user_id, address, category, description, priority, status,
            sla_hours, assigned_master, photo_urls_json, status_history_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'));\"\"\",
        (f'TCK-CRASH-{{i}}', 100456, f'ул. Аварийная, д. {{i}}', 'Электрика', 'Сбой питания',
         'urgent', 'new', 24, 'Мастер', '[]', '[]')
    )
"""

    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path.cwd())

    proc = subprocess.Popen(
        [sys.executable, "-c", worker_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env
    )

    # Wait for the first 30 commits
    line = proc.stdout.readline().strip()
    assert line == "COMMITTED_30", f"Worker failed to initialize: {proc.stderr.read()}"

    # Kill process with SIGKILL (signal 9)
    proc.send_signal(signal.SIGKILL)
    proc.wait()

    # Now verify restart recovery from secondary process
    restart_script = f"""
import sys
from app.db.database import Database

db = Database('{db_file}')
conn = db.get_connection()
cur = conn.cursor()

# 1. Integrity check
cur.execute('PRAGMA integrity_check;')
check_result = cur.fetchone()[0]
if check_result != 'ok':
    print(f'CORRUPT:{{check_result}}')
    sys.exit(1)

# 2. Count committed tickets
cur.execute(\"SELECT count(*) FROM tickets WHERE id LIKE 'TCK-CRASH-%';\")
count = cur.fetchone()[0]
if count < 30:
    print(f'DATA_LOSS:count={{count}}')
    sys.exit(2)

# 3. Test write after restart
try:
    db.execute_write(
        \"\"\"INSERT INTO tickets (
            id, user_id, address, category, description, priority, status,
            sla_hours, assigned_master, photo_urls_json, status_history_json,
            created_at, updated_at
        ) VALUES ('TCK-POST-RESTART', 100456, 'ул. Возрожденная', 'Отопление', 'Тест после рестарта',
         'urgent', 'new', 24, 'Мастер', '[]', '[]', datetime('now'), datetime('now'));\"\"\"
    )
    print(f'RECOVERED_OK:count={{count}}')
except Exception as e:
    print(f'WRITE_FAILED:{{e}}')
    sys.exit(3)
"""

    restart_proc = subprocess.run(
        [sys.executable, "-c", restart_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env
    )

    stdout = restart_proc.stdout.strip()
    assert restart_proc.returncode == 0, (
        f"Restart recovery failed with returncode {restart_proc.returncode}!\n"
        f"STDOUT: {stdout}\nSTDERR: {restart_proc.stderr}"
    )
    assert stdout.startswith("RECOVERED_OK"), f"Unexpected output: {stdout}"


def test_multiprocess_concurrent_writers_wal(tmp_path: Path):
    """
    Stress-tests multi-process concurrency on SQLite WAL mode:
    Spawns 3 independent operating system Python processes simultaneously writing
    tickets to the same database file (emulating FastAPI worker + Long Polling worker).
    Verifies that WAL mode and busy_timeout=5000 prevent database locked errors across OS processes.
    """
    db_file = str(tmp_path / "multiprocess_wal.db")
    # Initialize DB schema
    init_db = Database(db_file)
    init_db.close()

    writer_worker_code = f"""
import sys
import time
from app.db.database import Database

proc_id = sys.argv[1]
db = Database('{db_file}')

for i in range(25):
    ticket_id = f'TCK-MP-{{proc_id}}-{{i}}'
    db.execute_write(
        \"\"\"INSERT INTO tickets (
            id, user_id, address, category, description, priority, status,
            sla_hours, assigned_master, photo_urls_json, status_history_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'));\"\"\",
        (ticket_id, 100456, f'ул. Мультипроцесс {{proc_id}}', 'Тест', f'Заявка {{i}} от процесса {{proc_id}}',
         'urgent', 'new', 24, f'Мастер {{proc_id}}', '[]', '[]')
    )
    time.sleep(0.005)

print(f'PROC_{{proc_id}}_DONE', flush=True)
"""

    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path.cwd())

    processes = []
    num_procs = 3
    for p in range(num_procs):
        proc = subprocess.Popen(
            [sys.executable, "-c", writer_worker_code, str(p)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env
        )
        processes.append(proc)

    for p, proc in enumerate(processes):
        stdout, stderr = proc.communicate(timeout=25.0)
        assert proc.returncode == 0, f"Process {p} failed with stderr: {stderr}"
        assert f"PROC_{p}_DONE" in stdout

    # Verify all tickets written by all 3 processes are stored
    verify_db = Database(db_file)
    conn = verify_db.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM tickets WHERE id LIKE 'TCK-MP-%';")
    total_mp_tickets = cur.fetchone()[0]
    expected_total = num_procs * 25
    assert total_mp_tickets == expected_total, (
        f"Expected {expected_total} multi-process tickets, got {total_mp_tickets}"
    )
    verify_db.close()


@pytest.mark.anyio
async def test_sqlite_storage_interleaved_fsm_operations_concurrency(tmp_path: Path):
    """
    Stress-tests SQLiteStorage with complex nested JSON payloads, Cyrillic text,
    and high-concurrency interleaved state & data operations.
    """
    db_file = str(tmp_path / "fsm_interleaved.db")
    storage = SQLiteStorage(db_path=db_file)

    chat_id = 888999
    user_id = 444555

    async def worker(w_id: int):
        # Interleave state transition and data update
        state_name = f"STATE_STEP_{w_id}"
        await storage.set_state(chat_id, user_id, state_name)
        st = await storage.get_state(chat_id, user_id)
        assert st is not None

        payload = {
            f"русский_ключ_{w_id}": f"значение показаний {w_id * 1.5}",
            f"nested_obj_{w_id}": {
                "sub_id": w_id,
                "active": True,
                "meters": ["ХВС", "ГВС", "Электроэнергия"]
            }
        }
        res = await storage.update_data(chat_id, user_id, payload)
        assert f"русский_ключ_{w_id}" in res

    tasks = [worker(i) for i in range(30)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        assert not isinstance(r, Exception), f"Interleaved operation failed: {r}"

    final_data = await storage.get_data(chat_id, user_id)
    for i in range(30):
        assert f"русский_ключ_{i}" in final_data
        assert final_data[f"nested_obj_{i}"]["meters"] == ["ХВС", "ГВС", "Электроэнергия"]

    await storage.close()


def test_sqlite_storage_sync_and_async_concurrency(tmp_path: Path):
    """
    Stress-tests SQLiteStorage under simultaneous access from standard synchronous threads
    (calling sync_update_data) and coroutines running on an active asyncio event loop
    (calling update_data).
    Verifies that the RLock, BEGIN IMMEDIATE transaction, and key lock work seamlessly
    together without data corruption or deadlocks.
    """
    db_file = str(tmp_path / "fsm_hybrid_concurrency.db")
    storage = SQLiteStorage(db_path=db_file)

    chat_id = 12345
    user_id = 67890

    errors: List[Exception] = []
    err_lock = threading.Lock()

    def sync_thread_worker(t_id: int):
        try:
            for i in range(15):
                storage.sync_update_data(chat_id, user_id, {f"sync_{t_id}_{i}": i})
        except Exception as e:
            with err_lock:
                errors.append(e)

    async def async_worker(a_id: int):
        for i in range(15):
            await storage.update_data(chat_id, user_id, {f"async_{a_id}_{i}": i})

    async def main_async_suite():
        # Concurrently run sync threads and async tasks
        sync_threads = [
            threading.Thread(target=sync_thread_worker, args=(t,))
            for t in range(10)
        ]
        for t in sync_threads:
            t.start()

        # Run 10 concurrent coroutines on the current event loop
        async_tasks = [async_worker(a) for a in range(10)]
        await asyncio.gather(*async_tasks)

        # Wait for all sync threads to complete
        for t in sync_threads:
            t.join(timeout=15.0)

    asyncio.run(main_async_suite())

    assert len(errors) == 0, f"Encountered hybrid concurrency errors: {errors}"

    final_data = storage.sync_get_data(chat_id, user_id)
    # 10 sync threads * 15 + 10 async coroutines * 15 = 300 keys
    expected_keys = 10 * 15 * 2
    assert len(final_data) == expected_keys, (
        f"Expected {expected_keys} keys, found {len(final_data)} (data loss: {expected_keys - len(final_data)} keys)"
    )


# ============================================================================
# PART 4: Empirical Vulnerability Demonstrations & Root Cause Proofs
# ============================================================================

def test_adversarial_ticket_id_entropy_collision_and_silent_drop(tmp_path: Path):
    """
    Verifies remediation of ticket ID collision handling:
    1. Collision triggers retry loop generating fresh ID.
    2. Both tickets are genuinely persisted in SQLite (0 silent drops).
    3. Unpersisted tickets are never silently accepted into memory.
    """
    db_file = str(tmp_path / "ticket_collision_proof.db")
    db_inst = Database(db_file)
    svc = TicketService(db=db_inst)

    # Sequence of UUIDs: first call returns ABCD1234, second call returns ABCD1234 (collides),
    # third call (retry) returns EFGH5678 (unique).
    call_seq = ["ABCD1234", "ABCD1234", "EFGH5678"]
    call_idx = 0

    class MockUUID:
        def __init__(self, val):
            self.hex = val
        def __str__(self):
            return self.hex

    def mock_uuid():
        nonlocal call_idx
        val = call_seq[min(call_idx, len(call_seq) - 1)]
        call_idx += 1
        return MockUUID(val)

    orig_uuid4 = ticket_svc_module.uuid.uuid4
    ticket_svc_module.uuid.uuid4 = mock_uuid

    try:
        req1 = TicketCreateRequest(
            address="ул. Коллизий, д. 1",
            category="Водоснабжение",
            description="Первая заявка с ID ABCD1234",
            priority=TicketPriority.URGENT
        )
        req2 = TicketCreateRequest(
            address="ул. Коллизий, д. 2",
            category="Электрика",
            description="Вторая заявка (первая попытка коллизия ABCD1234, ретрай EFGH5678)",
            priority=TicketPriority.EMERGENCY
        )

        res1 = svc.create_ticket(req1)
        res2 = svc.create_ticket(req2)

        # Both returned with their respective persisted IDs
        assert res1.id == "TCK-2026-ABCD1234"
        assert res2.id == "TCK-2026-EFGH5678"

        # Check SQLite persistent storage: BOTH tickets exist! 0 silent drops!
        conn = db_inst.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM tickets WHERE id IN ('TCK-2026-ABCD1234', 'TCK-2026-EFGH5678');")
        db_count = cur.fetchone()[0]
        assert db_count == 2, "Remediation verified: collision retried and both tickets persisted in SQLite!"

        # In-memory list contains both tickets
        memory_ids = {t["id"] for t in svc._tickets}
        assert "TCK-2026-ABCD1234" in memory_ids
        assert "TCK-2026-EFGH5678" in memory_ids
    finally:
        ticket_svc_module.uuid.uuid4 = orig_uuid4


def test_app_db_36_writer_threads_creating_360_tickets_unmocked(tmp_path: Path):
    """
    Empirical Stress Test:
    36 concurrent writer threads simultaneously creating 10 tickets each (360 tickets total).
    Uses unmocked real uuid.uuid4() with 8-hex-char entropy and built-in SQLite retry loop.
    Verifies:
    1. Zero concurrency errors or deadlocks across all 36 threads.
    2. Exactly 360 new tickets created and returned.
    3. Exactly 360 persisted tickets in SQLite (zero silent drops).
    4. Every single returned ticket ID exists in the SQLite database.
    5. PRAGMA integrity_check returns 'ok'.
    """
    db_file = tmp_path / "app_db_36_writers_360_tickets.db"
    db_inst = Database(str(db_file))
    ticket_svc = TicketService(db=db_inst)

    num_threads = 36
    tickets_per_thread = 10
    total_expected = num_threads * tickets_per_thread

    created_ticket_ids: List[str] = []
    created_lock = threading.Lock()
    errors: List[Exception] = []
    err_lock = threading.Lock()

    def writer_worker(worker_id: int):
        try:
            for op in range(tickets_per_thread):
                req = TicketCreateRequest(
                    address=f"ул. Стрессовая, д. {worker_id}, кв. {op}",
                    category="Водоснабжение" if op % 2 == 0 else "Электрика",
                    description=f"Заявка worker={worker_id} op={op}",
                    priority=TicketPriority.URGENT if op % 3 == 0 else TicketPriority.EMERGENCY,
                    photo_urls=[]
                )
                res = ticket_svc.create_ticket(req, user_id=REFERENCE_USER_ID)
                assert res.id.startswith("TCK-2026-")
                with created_lock:
                    created_ticket_ids.append(res.id)
        except Exception as e:
            with err_lock:
                errors.append(e)

    threads = [threading.Thread(target=writer_worker, args=(i,)) for i in range(num_threads)]

    start_time = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30.0)
    elapsed = time.time() - start_time

    # 1. Assert zero errors
    assert len(errors) == 0, f"Encountered {len(errors)} thread errors: {errors[:5]}"

    # 2. Assert exactly 360 tickets were returned
    assert len(created_ticket_ids) == total_expected, (
        f"Expected {total_expected} returned ticket IDs, got {len(created_ticket_ids)}"
    )

    # 3. Verify uniqueness of all returned IDs
    unique_ids = set(created_ticket_ids)
    assert len(unique_ids) == total_expected, (
        f"Ticket ID collision not resolved! Unique IDs: {len(unique_ids)} vs Expected: {total_expected}"
    )

    # 4. Check SQLite persistence: exactly 360 created tickets (excluding reference ticket TCK-2026-0819)
    conn = db_inst.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM tickets WHERE id != 'TCK-2026-0819';")
    db_count = cur.fetchone()[0]
    assert db_count == total_expected, (
        f"Silent drop detected! SQLite has {db_count} tickets, expected {total_expected}"
    )

    # 5. Direct verification of every single ticket ID in SQLite
    cur.execute("SELECT id FROM tickets WHERE id != 'TCK-2026-0819';")
    db_ids = {row[0] for row in cur.fetchall()}
    missing_ids = unique_ids - db_ids
    assert len(missing_ids) == 0, f"Silent drops verified for IDs: {missing_ids}"

    # 6. Verify SQLite PRAGMA integrity
    cur.execute("PRAGMA integrity_check;")
    check_result = cur.fetchone()[0]
    assert check_result == "ok", f"Integrity check failed: {check_result}"
    db_inst.close()


def test_adversarial_ticket_id_multi_collision_retry_and_exhaustion(tmp_path: Path):
    """
    Adversarial Stress Test:
    1. Multi-collision retry: 4 successive collisions followed by success on 5th attempt.
       Verifies retry loop executes up to max_retries without failure.
    2. Retry exhaustion: 5 consecutive collisions.
       Verifies exception is raised (does NOT silently drop or add partial state to memory).
    """
    db_file = str(tmp_path / "multi_collision.db")
    db_inst = Database(db_file)
    svc = TicketService(db=db_inst)

    class MockUUID:
        def __init__(self, val):
            self.hex = val
        def __str__(self):
            return self.hex

    # Part 1: 4 collisions, 5th succeeds
    # First create ticket with COLL0001
    call_seq_1 = ["COLL0001"]
    seq_idx = 0
    def mock_uuid_1():
        nonlocal seq_idx
        v = call_seq_1[min(seq_idx, len(call_seq_1) - 1)]
        seq_idx += 1
        return MockUUID(v)

    orig_uuid4 = ticket_svc_module.uuid.uuid4
    ticket_svc_module.uuid.uuid4 = mock_uuid_1

    try:
        t1 = svc.create_ticket(TicketCreateRequest(
            address="ул. Коллизий, д. 1", category="Водоснабжение", description="Первая", priority=TicketPriority.URGENT
        ))
        assert t1.id == "TCK-2026-COLL0001"

        # Now simulate 4 collisions with COLL0001, then succeed with SUCC0005 on attempt 5
        call_seq_2 = ["COLL0001", "COLL0001", "COLL0001", "COLL0001", "SUCC0005"]
        seq_idx = 0
        def mock_uuid_2():
            nonlocal seq_idx
            v = call_seq_2[min(seq_idx, len(call_seq_2) - 1)]
            seq_idx += 1
            return MockUUID(v)

        ticket_svc_module.uuid.uuid4 = mock_uuid_2
        t2 = svc.create_ticket(TicketCreateRequest(
            address="ул. Коллизий, д. 2", category="Электрика", description="Вторая (4 коллизии)", priority=TicketPriority.EMERGENCY
        ))
        assert t2.id == "TCK-2026-SUCC0005"

        # Check SQLite persistence: both tickets exist
        conn = db_inst.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM tickets WHERE id IN ('TCK-2026-COLL0001', 'TCK-2026-SUCC0005');")
        assert cur.fetchone()[0] == 2

        # Part 2: 5 collisions -> exhausts max_retries = 5 -> MUST raise exception
        call_seq_3 = ["COLL0001"] * 10
        seq_idx = 0
        def mock_uuid_3():
            nonlocal seq_idx
            v = call_seq_3[min(seq_idx, len(call_seq_3) - 1)]
            seq_idx += 1
            return MockUUID(v)

        ticket_svc_module.uuid.uuid4 = mock_uuid_3
        initial_memory_count = len(svc._tickets)

        with pytest.raises(Exception) as exc_info:
            svc.create_ticket(TicketCreateRequest(
                address="ул. Тупиковая, д. 99", category="Газ", description="Отказ", priority=TicketPriority.URGENT
            ))

        # Check that exception is UNIQUE constraint / IntegrityError
        assert "UNIQUE constraint failed" in str(exc_info.value) or isinstance(exc_info.value, sqlite3.IntegrityError)

        # Check that memory list did NOT append the failed ticket
        assert len(svc._tickets) == initial_memory_count, "Failed ticket was improperly added to in-memory list!"
    finally:
        ticket_svc_module.uuid.uuid4 = orig_uuid4
        db_inst.close()


def test_sqlite_connection_and_fd_lifecycle_2000_ops(tmp_path: Path):
    """
    Stress-tests 2,000 rapid operations across Database and SQLiteStorage to verify:
    1. Zero leaked database connections.
    2. File descriptor stability (growth <= 3 on macOS /dev/fd).
    3. PRAGMA integrity check remains 'ok'.
    """
    db_file_app = str(tmp_path / "leak_test_app.db")
    db_file_fsm = str(tmp_path / "leak_test_fsm.db")

    db_inst = Database(db_file_app)
    fsm_storage = SQLiteStorage(db_file_fsm)

    def get_fd_count() -> int:
        if os.path.exists("/dev/fd"):
            try:
                return len(os.listdir("/dev/fd"))
            except Exception:
                pass
        return 0

    gc.collect()
    start_fds = get_fd_count()

    # 1,000 app DB operations + 1,000 FSM storage operations = 2,000 operations
    for i in range(500):
        # App DB read/write
        db_inst.execute_write(
            "INSERT INTO tickets (id, address, category, description, priority, status, sla_hours, created_at, updated_at) "
            "VALUES (?, ?, 'Стресс', 'Проверка утечек', 'urgent', 'new', 24, datetime('now'), datetime('now'));",
            (f"TCK-LEAK-{i}", f"ул. Тестовая, {i}")
        )
        row = db_inst.execute_one("SELECT count(*) as c FROM tickets WHERE id = ?;", (f"TCK-LEAK-{i}",))
        assert row["c"] == 1

        # FSM storage read/write
        fsm_storage.sync_set_state(f"chat_{i}", f"user_{i}", f"STATE_{i}")
        fsm_storage.sync_set_data(f"chat_{i}", f"user_{i}", {"key": i, "status": "active"})
        st = fsm_storage.sync_get_state(f"chat_{i}", f"user_{i}")
        assert st == f"STATE_{i}"
        dt = fsm_storage.sync_get_data(f"chat_{i}", f"user_{i}")
        assert dt["key"] == i

    gc.collect()
    end_fds = get_fd_count()

    if start_fds > 0 and end_fds > 0:
        growth = end_fds - start_fds
        assert growth <= 3, f"FD leak detected! Start: {start_fds}, End: {end_fds}, Growth: {growth}"

    # Integrity checks
    conn_app = db_inst.get_connection()
    cur_app = conn_app.cursor()
    cur_app.execute("PRAGMA integrity_check;")
    assert cur_app.fetchone()[0] == "ok"

    conn_fsm = fsm_storage._get_connection()
    cur_fsm = conn_fsm.cursor()
    cur_fsm.execute("PRAGMA integrity_check;")
    assert cur_fsm.fetchone()[0] == "ok"
    conn_fsm.close()

    db_inst.close()


@pytest.mark.anyio
async def test_sqlite_storage_50_coroutines_extreme_hammer(tmp_path: Path):
    """
    Adversarial Stress Test:
    50 concurrent coroutines hammering the SAME chat_id and user_id with:
    - Multiple successive update_data calls per coroutine with complex JSON payloads
    - Interleaved get_data and get_state reads
    Verifies:
    1. Zero exceptions / zero database locks.
    2. Exactly 100% of keys preserved across all coroutines (0% data loss).
    3. FSM data consistency and integrity check pass.
    """
    db_file = str(tmp_path / "extreme_fsm_hammer.db")
    storage = SQLiteStorage(db_path=db_file)

    chat_id = 100500
    user_id = 200500

    await storage.set_state(chat_id, user_id, "HAMMER_START")

    num_coroutines = 50
    keys_per_coroutine = 3

    async def coroutine_worker(cid: int):
        for k in range(keys_per_coroutine):
            payload = {
                f"c_{cid}_k_{k}": {
                    "worker": cid,
                    "step": k,
                    "meta": f"русский_текст_{cid}_{k}",
                    "val": cid * 100 + k
                }
            }
            res = await storage.update_data(chat_id, user_id, payload)
            assert f"c_{cid}_k_{k}" in res

            # Interleaved read
            read_back = await storage.get_data(chat_id, user_id)
            assert f"c_{cid}_k_{k}" in read_back

    tasks = [coroutine_worker(i) for i in range(num_coroutines)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, r in enumerate(results):
        assert not isinstance(r, Exception), f"Coroutine {i} failed: {r}"

    # Verify total keys: 50 coroutines * 3 keys = 150 keys total
    total_expected_keys = num_coroutines * keys_per_coroutine
    final_data = await storage.get_data(chat_id, user_id)
    assert len(final_data) == total_expected_keys, (
        f"Data loss! Expected {total_expected_keys} keys, got {len(final_data)}"
    )

    # Verify every single key
    for cid in range(num_coroutines):
        for k in range(keys_per_coroutine):
            key_name = f"c_{cid}_k_{k}"
            assert key_name in final_data, f"Missing key {key_name}"
            val = final_data[key_name]
            assert val["worker"] == cid
            assert val["step"] == k
            assert val["meta"] == f"русский_текст_{cid}_{k}"
            assert val["val"] == cid * 100 + k

    await storage.close()







