"""
Management Company Service Request & Emergency Dispatcher Service.
Complies with PP RF No. 40 (26.01.2026) regarding resident-UK communication via MAX.
Includes SLAs: Emergency (30 min localization), Urgent (24 h), Planned (3 days).
"""

from typing import List, Optional, Dict
from datetime import datetime
import uuid
from app.models.domain import TicketPriority, TicketStatus
from app.models.schemas import TicketCreateRequest, TicketResponse

INITIAL_TICKETS: List[Dict] = [
    {
        "id": "TCK-2026-0819",
        "created_at": datetime(2026, 9, 18, 14, 30),
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
    def __init__(self):
        self._tickets: List[Dict] = list(INITIAL_TICKETS)

    def get_all_tickets(self) -> List[TicketResponse]:
        return [TicketResponse(**t) for t in self._tickets]

    def create_ticket(self, req: TicketCreateRequest) -> TicketResponse:
        sla = 1 if req.priority == TicketPriority.EMERGENCY else (24 if req.priority == TicketPriority.URGENT else 72)
        ticket_id = f"TCK-2026-{str(uuid.uuid4())[:4].upper()}"
        now = datetime.now()
        
        ticket_dict = {
            "id": ticket_id,
            "created_at": now,
            "category": req.category,
            "description": req.description,
            "priority": req.priority,
            "status": TicketStatus.NEW,
            "sla_hours": sla,
            "assigned_master": "Диспетчерская служба района",
            "status_history": [
                {
                    "status": "new",
                    "time": now.isoformat(),
                    "comment": f"Заявка создана через MAX (Срок регламента: {sla} ч по ПП РФ № 40)"
                }
            ]
        }
        self._tickets.insert(0, ticket_dict)
        return TicketResponse(**ticket_dict)

ticket_service = TicketService()
