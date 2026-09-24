"""
Management Company Service Request & Emergency Dispatcher Service.
Complies with PP RF No. 40 (26.01.2026) regarding resident-UK communication via MAX.
Includes SLAs: Emergency (30 min localization / 1h SLA), Urgent (24 h), Planned (3 days).
Persistent SQLite storage via app.db.database.Database with 100% backward compatibility.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
import json
import uuid
import sqlite3
import threading
import logging

from app.models.domain import TicketPriority, TicketStatus
from app.models.schemas import TicketCreateRequest, TicketResponse
from app.db.database import Database, get_db, REFERENCE_USER_ID, REFERENCE_TICKET

logger = logging.getLogger("max_ticket_service")

INITIAL_TICKETS: List[Dict[str, Any]] = [
    {
        "id": "TCK-2026-0819",
        "user_id": REFERENCE_USER_ID,
        "created_at": datetime(2026, 9, 18, 14, 30),
        "address": "ул. Ленина, д. 42, кв. 15",
        "category": "Сантехника",
        "description": "Слабый напор горячей воды на верхних этажах",
        "priority": TicketPriority.URGENT,
        "status": TicketStatus.IN_PROGRESS,
        "sla_hours": 24,
        "assigned_master": "Иванов С. М. (Дежурный слесарь-сантехник)",
        "status_history": [
            {"status": "new", "time": "2026-09-18T14:30:00", "comment": "Заявка зарегистрирована в MAX"},
            {"status": "in_progress", "time": "2026-09-18T15:10:00", "comment": "Мастер выехал на объект"}
        ]
    }
]


class TicketService:
    def __init__(self, db: Optional[Database] = None):
        self._db = db or get_db()
        self._lock = threading.RLock()
        self._tickets: List[Dict[str, Any]] = list(INITIAL_TICKETS)
        self._sync_from_db()

    @property
    def db(self) -> Database:
        return self._db

    def _sync_from_db(self):
        """Loads tickets from SQLite into in-memory list or seeds reference ticket."""
        rows = self._db.execute_query("SELECT * FROM tickets ORDER BY created_at DESC;")
        if not rows:
            # Seed reference ticket into DB
            self.reset()
        else:
            with self._lock:
                self._tickets.clear()
                for r in rows:
                    try:
                        cr_at = datetime.fromisoformat(r["created_at"])
                    except Exception:
                        cr_at = datetime.now()
                    try:
                        hist = json.loads(r["status_history_json"]) if r["status_history_json"] else []
                    except Exception:
                        hist = []
                    self._tickets.append({
                        "id": r["id"],
                        "user_id": r["user_id"],
                        "created_at": cr_at,
                        "address": r["address"],
                        "category": r["category"],
                        "description": r["description"],
                        "priority": TicketPriority(r["priority"]),
                        "status": TicketStatus(r["status"]),
                        "sla_hours": int(r["sla_hours"]),
                        "assigned_master": r["assigned_master"],
                        "status_history": hist
                    })

    def _row_to_response(self, r: Any) -> TicketResponse:
        try:
            cr_at = datetime.fromisoformat(r["created_at"])
        except Exception:
            cr_at = datetime.now()
        try:
            hist = json.loads(r["status_history_json"]) if r["status_history_json"] else []
        except Exception:
            hist = []

        return TicketResponse(
            id=r["id"],
            created_at=cr_at,
            category=r["category"],
            description=r["description"],
            priority=TicketPriority(r["priority"]),
            status=TicketStatus(r["status"]),
            sla_hours=int(r["sla_hours"]),
            assigned_master=r["assigned_master"],
            status_history=hist
        )

    def get_all_tickets(self) -> List[TicketResponse]:
        """Returns all registered tickets ordered by created_at DESC."""
        rows = self._db.execute_query("SELECT * FROM tickets ORDER BY created_at DESC;")
        if rows:
            return [self._row_to_response(r) for r in rows]
        with self._lock:
            return [TicketResponse(**t) for t in self._tickets]

    def list_tickets(self, user_id: Optional[int] = None) -> List[TicketResponse]:
        """Dispatch alias for listing tickets, optionally filtered by user_id."""
        if user_id:
            rows = self._db.execute_query(
                "SELECT * FROM tickets WHERE user_id = ? ORDER BY created_at DESC;", (user_id,)
            )
            return [self._row_to_response(r) for r in rows]
        return self.get_all_tickets()

    def get_ticket(self, ticket_id: str) -> Optional[TicketResponse]:
        """Returns specific ticket by ID."""
        row = self._db.execute_one("SELECT * FROM tickets WHERE id = ?;", (ticket_id,))
        if row:
            return self._row_to_response(row)
        with self._lock:
            for t in self._tickets:
                if t["id"] == ticket_id:
                    return TicketResponse(**t)
        return None

    def create_ticket(self, req: TicketCreateRequest, user_id: Optional[int] = None) -> TicketResponse:
        """Creates a new emergency or planned ticket in SQLite and memory."""
        sla = 1 if req.priority == TicketPriority.EMERGENCY else (24 if req.priority == TicketPriority.URGENT else 72)
        uid = user_id or REFERENCE_USER_ID
        now = datetime.now()
        now_iso = now.isoformat()

        history_entry = {
            "status": "new",
            "time": now_iso,
            "comment": f"Заявка создана через MAX (Срок регламента: {sla} ч по ПП РФ № 40)"
        }
        history_json = json.dumps([history_entry])
        priority_val = req.priority.value if hasattr(req.priority, "value") else str(req.priority)
        status_val = TicketStatus.NEW.value if hasattr(TicketStatus.NEW, "value") else str(TicketStatus.NEW)
        master = "Диспетчерская служба района"
        photos_json = json.dumps(req.photo_urls) if req.photo_urls else "[]"

        # Entropy expansion & retry loop (32 bits of entropy: f"TCK-2026-{uuid.uuid4().hex[:8].upper()}")
        max_retries = 5
        ticket_id = None
        for attempt in range(max_retries):
            u = uuid.uuid4()
            hex_part = getattr(u, "hex", None) or str(u).replace("-", "")
            ticket_id = f"TCK-2026-{hex_part[:8].upper()}"

            try:
                self._db.execute_write(
                    """INSERT INTO tickets (
                        id, user_id, address, category, description, priority, status,
                        sla_hours, assigned_master, photo_urls_json, status_history_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                    (
                        ticket_id,
                        uid,
                        req.address,
                        req.category,
                        req.description,
                        priority_val,
                        status_val,
                        sla,
                        master,
                        photos_json,
                        history_json,
                        now_iso,
                        now_iso
                    )
                )
                break
            except Exception as e:
                is_unique_err = "UNIQUE constraint failed" in str(e) or isinstance(e, sqlite3.IntegrityError)
                if is_unique_err and attempt < max_retries - 1:
                    logger.warning(
                        "Collision on ticket ID %s (attempt %d/%d), retrying: %s",
                        ticket_id, attempt + 1, max_retries, e
                    )
                    continue
                logger.error("Failed to persist ticket %s to SQLite: %s", ticket_id, e)
                raise

        ticket_dict = {
            "id": ticket_id,
            "user_id": uid,
            "created_at": now,
            "category": req.category,
            "description": req.description,
            "priority": req.priority,
            "status": TicketStatus.NEW,
            "sla_hours": sla,
            "assigned_master": master,
            "status_history": [history_entry]
        }

        with self._lock:
            self._tickets.insert(0, ticket_dict)

        return TicketResponse(**ticket_dict)

    def update_ticket_status(
        self,
        ticket_id: str,
        new_status: TicketStatus,
        comment: Optional[str] = None,
        assigned_master: Optional[str] = None
    ) -> Optional[TicketResponse]:
        """Updates ticket status and master assignment in SQLite and memory."""
        now = datetime.now()
        now_iso = now.isoformat()
        status_str = new_status.value if hasattr(new_status, "value") else str(new_status)

        row = self._db.execute_one("SELECT * FROM tickets WHERE id = ?;", (ticket_id,))
        if not row:
            # Fallback check in-memory
            with self._lock:
                for t in self._tickets:
                    if t["id"] == ticket_id:
                        t["status"] = new_status
                        if assigned_master:
                            t["assigned_master"] = assigned_master
                        entry = {
                            "status": status_str,
                            "time": now_iso,
                            "comment": comment or f"Статус обновлен диспетчером: {new_status}"
                        }
                        t["status_history"].append(entry)
                        return TicketResponse(**t)
            return None

        try:
            hist = json.loads(row["status_history_json"]) if row["status_history_json"] else []
        except Exception:
            hist = []

        entry = {
            "status": status_str,
            "time": now_iso,
            "comment": comment or f"Статус обновлен диспетчером: {new_status}"
        }
        hist.append(entry)
        new_hist_json = json.dumps(hist)
        effective_master = assigned_master if assigned_master else row["assigned_master"]

        try:
            self._db.execute_write(
                """UPDATE tickets SET
                    status = ?, assigned_master = ?, status_history_json = ?, updated_at = ?
                WHERE id = ?;""",
                (status_str, effective_master, new_hist_json, now_iso, ticket_id)
            )
        except Exception as e:
            logger.warning("Failed to update ticket %s in SQLite: %s", ticket_id, e)

        # Update in-memory cache
        with self._lock:
            for t in self._tickets:
                if t["id"] == ticket_id:
                    t["status"] = new_status
                    if assigned_master:
                        t["assigned_master"] = assigned_master
                    t["status_history"].append(entry)
                    break

        return self.get_ticket(ticket_id)

    def reset(self):
        """Resets ticket records to pristine reference ticket."""
        with self._lock:
            self._tickets = [
                {
                    "id": "TCK-2026-0819",
                    "user_id": REFERENCE_USER_ID,
                    "created_at": datetime(2026, 9, 18, 14, 30),
                    "address": "ул. Ленина, д. 42, кв. 15",
                    "category": "Сантехника",
                    "description": "Слабый напор горячей воды на верхних этажах",
                    "priority": TicketPriority.URGENT,
                    "status": TicketStatus.IN_PROGRESS,
                    "sla_hours": 24,
                    "assigned_master": "Иванов С. М. (Дежурный слесарь-сантехник)",
                    "status_history": [
                        {"status": "new", "time": "2026-09-18T14:30:00", "comment": "Заявка зарегистрирована в MAX"},
                        {"status": "in_progress", "time": "2026-09-18T15:10:00", "comment": "Мастер выехал на объект"}
                    ]
                }
            ]
        try:
            self._db.execute_write("DELETE FROM tickets;")
            self._db.execute_write(
                """INSERT INTO tickets (
                    id, user_id, address, category, description, priority, status,
                    sla_hours, assigned_master, photo_urls_json, status_history_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                (
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
                )
            )
        except Exception as e:
            logger.warning("Error resetting tickets in SQLite: %s", e)


ticket_service = TicketService()
