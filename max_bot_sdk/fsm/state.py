"""
FSM State definitions and grouping utilities for MAX Bot SDK.
Zero emoji policy strictly enforced.
"""

from enum import Enum
from typing import Optional, Any, Set


class UserState(str, Enum):
    """Standard conversational states for Smart City MAX Bot."""
    IDLE = "idle"

    # Meters
    WAITING_METER_SELECT = "waiting_meter_select"
    WAITING_METER_INPUT = "waiting_meter_input"
    WAITING_METER_PHOTO = "waiting_meter_photo"
    WAITING_METER_CONFIRMATION = "waiting_meter_confirmation"

    # Arshin antifraud
    WAITING_ARSHIN_SERIAL = "waiting_arshin_serial"

    # Address and ELS management
    WAITING_ADDRESS = "waiting_address"
    WAITING_ELS = "waiting_els"

    # Emergency and service tickets (PP RF No. 40)
    WAITING_TICKET_CATEGORY = "waiting_ticket_category"
    WAITING_TICKET_DESC = "waiting_ticket_desc"
    WAITING_TICKET_PHOTO = "waiting_ticket_photo"

    # Inspector ARM
    WAITING_INSPECTOR_PHOTO = "waiting_inspector_photo"
    WAITING_INSPECTOR_READING = "waiting_inspector_reading"

    # Payments
    WAITING_PAYMENT_CONFIRM = "waiting_payment_confirm"
    WAITING_QR_CONFIRM = "waiting_qr_confirm"


class State:
    """Represents a conversational state with equality support."""
    def __init__(self, name: Optional[str] = None):
        self._name = name

    @property
    def name(self) -> str:
        return self._name or "unnamed_state"

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"<State '{self.name}'>"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, State):
            return self.name == other.name
        if isinstance(other, (str, Enum)):
            val = other.value if hasattr(other, "value") else str(other)
            return self.name == val
        return False

    def __hash__(self) -> int:
        return hash(self.name)


class StatesGroup:
    """Base class for grouping states together."""
    @classmethod
    def all_states(cls) -> Set[str]:
        states = set()
        for attr_name in dir(cls):
            if attr_name.startswith("_"):
                continue
            val = getattr(cls, attr_name)
            if isinstance(val, (State, str, Enum)):
                state_name = val.value if hasattr(val, "value") else (val.name if isinstance(val, State) else str(val))
                states.add(state_name)
        return states
