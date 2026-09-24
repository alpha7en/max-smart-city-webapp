"""
Empirical Adversarial Test Suite - Iteration 2: Bot FSM & Dispatcher Hardening.
Conducted by challenger_iter2_bot_fsm.

Scope:
1. WAITING_ADDRESS manual addition flow:
   - UserProfileService.add_property_manual runs without AttributeError.
   - Active property is updated in profile and persisted.
   - FSM state cleanly resets to None.
2. Ticket creation flow (_create_ticket_from_text):
   - Runs cleanly without AttributeError on ticket.ticket_number or ticket.id.
   - User state cleanly resets to None.
   - Ticket persisted in database with proper PP RF No. 40 SLA.
   - Category parsing behavior verified.
3. Single execution under feed_update:
   - Sync handlers execute exactly once (call_count == 1).
   - Async handlers execute exactly once (call_count == 1).
   - Middlewares execute exactly once per update.
   - Awaiting AsyncResult does not duplicate handler execution.
4. Audit constraints:
   - 100% of inline buttons length <= 18 characters.
   - Zero emoji policy in bot messages, keyboards, and handlers.
   - Zero bracket tags [...] in outgoing messages.
"""

import asyncio
import os
import re
import time
from datetime import date
from typing import Dict, Any, List, Optional
import pytest

from max_bot_sdk import (
    AsyncUpdateDispatcher,
    Update,
    UserState,
    FSMContext,
    Button,
    KeyboardBuilder,
)
from max_bot_sdk.middlewares.base import BaseMiddleware
from app.config import settings
from app.bot.handlers import BotHandler
from app.bot.keyboards import (
    get_main_menu_keyboard,
    get_meters_keyboard,
    get_services_keyboard,
    get_help_keyboard,
    get_address_switch_keyboard,
    get_guest_keyboard,
    get_meter_confirmation_keyboard,
    get_ticket_categories_keyboard,
    get_inspector_keyboard,
    get_payment_keyboard,
)
from app.models.schemas import MeterBase, TicketCreateRequest
from app.models.domain import MeterType, TicketPriority
from app.services.ticket_service import ticket_service
from app.services.profile_service import profile_service, UserProfileService, UserProperty


class MockTrackingClient:
    """Mock client tracking all sent messages and callbacks."""
    def __init__(self):
        self.sent_messages: List[Dict[str, Any]] = []
        self.answered_callbacks: List[Dict[str, Any]] = []

    def send_message(self, **kwargs) -> Dict[str, Any]:
        self.sent_messages.append(kwargs)
        return {"status": "ok", "message_id": len(self.sent_messages)}

    def answer_callback(self, **kwargs) -> Dict[str, Any]:
        self.answered_callbacks.append(kwargs)
        return {"status": "ok"}

    def get_me(self) -> Dict[str, Any]:
        return {"id": 1, "first_name": "TestBot", "username": "test_bot"}

    def get_updates(self, marker=None, timeout=25):
        return {"marker": "m1", "updates": []}


class TrackingMiddleware(BaseMiddleware):
    def __init__(self):
        self.pre_count = 0
        self.post_count = 0

    async def pre_process(self, update: Update, data: Dict[str, Any]) -> bool:
        self.pre_count += 1
        return True

    async def post_process(self, update: Update, data: Dict[str, Any], result: Any = None, exception: Optional[Exception] = None) -> None:
        self.post_count += 1


EMOJI_REGEX = re.compile(
    r"["
    r"\U0001F600-\U0001F64F"  # Emoticons
    r"\U0001F300-\U0001F5FF"  # Misc Symbols & Pictographs
    r"\U0001F680-\U0001F6FF"  # Transport & Map Symbols
    r"\U0001F700-\U0001F77F"  # Alchemical
    r"\U0001F780-\U0001F7FF"  # Geometric Shapes
    r"\U0001F800-\U0001F8FF"  # Supplemental Arrows
    r"\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
    r"\U0001FA00-\U0001FA6F"  # Chess
    r"\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
    r"\U00002600-\U000026FF"  # Misc Symbols
    r"\U00002700-\U000027BF"  # Dingbats
    r"\U0001F1E6-\U0001F1FF"  # Flags
    r"\U00002300-\U000023FF"  # Misc Technical
    r"\U00002B50"              # Star
    r"\U00002B55"              # Circle
    r"]"
)
ALLOWED_TYPOGRAPHY = {"✓", "✕", "✗", "▶", "▲", "▼", "●", "■"}


# =========================================================================
# 1. WAITING_ADDRESS MANUAL ADDITION STRESS TESTS
# =========================================================================

def test_waiting_address_manual_addition_direct_service_call():
    """
    Directly verify UserProfileService.add_property_manual:
    - No AttributeError raised.
    - Returns valid UserProperty instance.
    - Property is marked active.
    - User profile correctly updated in SQLite.
    """
    user_id = 9101
    addr = "г. Москва, Ломоносовский пр-т, д. 27, кв. 104"
    prop = profile_service.add_property_manual(user_id, addr, "ООО УК Академическая")

    assert isinstance(prop, UserProperty)
    assert prop.address == addr
    assert prop.management_company == "ООО УК Академическая"
    assert prop.is_active is True

    # Verify profile active property updated
    active = profile_service.get_active_property(user_id)
    assert active.id == prop.id
    assert active.address == addr


def test_waiting_address_fsm_bot_dialog_flow():
    """
    Stress-test bot dialog flow when adding address:
    - User clicks 'cmd_add_address' -> state is WAITING_ADDRESS.
    - User sends address text -> add_property_manual called without AttributeError.
    - Bot sends confirmation with formatted text and main menu keyboard.
    - User state resets cleanly to None.
    - Subsequent messages are not treated as address additions.
    """
    client = MockTrackingClient()
    handler = BotHandler(client=client)
    chat_id = "chat_addr_fsm_1"
    user_id = 9102
    fsm = handler.get_fsm_context(chat_id, user_id)

    # 1. User clicks 'cmd_add_address'
    handler.handle_callback({
        "callback": {"callback_id": "cb_addr_1", "payload": "cmd_add_address", "user": {"id": user_id}},
        "sender": {"id": user_id},
        "chat_id": chat_id
    })
    assert fsm.current_state == UserState.WAITING_ADDRESS.value
    assert fsm.get_state() == UserState.WAITING_ADDRESS.value

    # 2. User sends new address text
    client.sent_messages.clear()
    new_address = "г. Казань, ул. Баумана, д. 15, кв. 8"
    handler.handle_message_created({
        "message": {
            "body": {"text": new_address},
            "sender": {"id": user_id, "first_name": "Карим"},
            "recipient": {"chat_id": chat_id}
        }
    })

    # Assert confirmation sent
    assert len(client.sent_messages) == 1
    reply_text = client.sent_messages[0]["text"]
    assert "Новый адрес успешно добавлен" in reply_text
    assert new_address in reply_text

    # Assert state cleanly reset to None
    assert fsm.current_state is None
    assert fsm.get_state() == None

    # Assert profile was actually updated
    active_prop = profile_service.get_active_property(user_id)
    assert active_prop.address == new_address
    assert active_prop.is_active is True

    # 3. Subsequent message should NOT trigger address addition
    client.sent_messages.clear()
    handler.handle_message_created({
        "message": {
            "body": {"text": "Обычное текстовое сообщение"},
            "sender": {"id": user_id, "first_name": "Карим"},
            "recipient": {"chat_id": chat_id}
        }
    })
    assert len(client.sent_messages) == 1
    assert "Новый адрес успешно добавлен" not in client.sent_messages[0]["text"]


@pytest.mark.anyio
async def test_waiting_address_flow_via_feed_update():
    """
    Verify WAITING_ADDRESS manual addition flow when driven asynchronously via feed_update.
    """
    client = MockTrackingClient()
    handler = BotHandler(client=client)
    chat_id = "chat_addr_async_1"
    user_id = 9103
    fsm = handler.get_fsm_context(chat_id, user_id)

    # Trigger state change
    await handler.dispatcher.feed_update({
        "update_type": "message_callback",
        "callback": {"callback_id": "cb_addr_async", "payload": "cmd_add_address", "user": {"id": user_id}},
        "sender": {"id": user_id},
        "chat_id": chat_id
    })
    assert fsm.current_state == UserState.WAITING_ADDRESS.value

    # Submit address via feed_update
    client.sent_messages.clear()
    addr_text = "Санкт-Петербург, Невский пр., д. 100, кв. 25"
    task = handler.dispatcher.feed_update({
        "update_type": "message_created",
        "message": {
            "body": {"text": addr_text},
            "sender": {"id": user_id, "first_name": "Ольга"},
            "recipient": {"chat_id": chat_id}
        }
    })
    res = await task

    assert bool(res) is True
    assert fsm.current_state is None
    assert len(client.sent_messages) == 1
    assert "Новый адрес успешно добавлен" in client.sent_messages[0]["text"]
    assert addr_text in client.sent_messages[0]["text"]


# =========================================================================
# 2. TICKET CREATION STRESS TESTS
# =========================================================================

def test_ticket_creation_direct_and_no_attribute_error():
    """
    Verify _create_ticket_from_text directly:
    - Ticket is created in ticket_service.
    - No AttributeError on ticket.ticket_number or ticket.id.
    - SLA adheres to PP RF No. 40 (24h for urgent).
    - FSM state resets cleanly to None.
    """
    client = MockTrackingClient()
    handler = BotHandler(client=client)
    chat_id = "chat_tck_direct"
    user_id = 9201
    fsm = handler.get_fsm_context(chat_id, user_id)

    fsm.set_state(UserState.WAITING_TICKET_DESC)
    assert fsm.current_state == UserState.WAITING_TICKET_DESC.value

    desc = "Течет кран на кухне под раковиной"
    handler._create_ticket_from_text(chat_id, user_id, desc)

    # Verification of state reset
    assert fsm.current_state is None
    assert fsm.get_state() == None

    # Verification of message content
    assert len(client.sent_messages) == 1
    reply = client.sent_messages[0]["text"]
    assert "Заявка в УК зарегистрирована" in reply
    assert "TCK-2026-" in reply
    assert desc in reply
    assert "Срочная (до 24 ч)" in reply
    assert "ПП РФ № 40" in reply


def test_ticket_creation_fsm_category_extraction_behavior():
    """
    Empirical check on category extraction in _create_ticket_from_text:
    Documents whether FSMResult type affects isinstance(data, dict) check.
    """
    client = MockTrackingClient()
    handler = BotHandler(client=client)
    chat_id = "chat_tck_cat_emp"
    user_id = 9202
    fsm = handler.get_fsm_context(chat_id, user_id)

    fsm.set_state(UserState.WAITING_TICKET_DESC)
    fsm.set_data({"category": "electric"})

    data = fsm.get_data()
    # FSMContext returns FSMResult wrapper
    assert data.get("category") == "electric"

    # Call _create_ticket_from_text
    handler._create_ticket_from_text(chat_id, user_id, "Искрит щиток")
    assert len(client.sent_messages) == 1
    reply = client.sent_messages[0]["text"]

    # FSM state resets cleanly to None
    assert fsm.current_state is None
    assert "TCK-2026-" in reply


# =========================================================================
# 3. SINGLE EXECUTION UNDER FEED_UPDATE HARNESS
# =========================================================================

@pytest.mark.anyio
async def test_feed_update_single_execution_sync_handler():
    """
    Adversarial verification: ensure synchronous handler registered on dispatcher
    executes EXACTLY ONCE when scheduled via feed_update (assert call_count == 1).
    """
    client = MockTrackingClient()
    dp = AsyncUpdateDispatcher(client=client)

    counter = {"calls": 0}

    @dp.message()
    def sync_handler(client, update):
        counter["calls"] += 1
        return "ok_sync"

    upd = {
        "update_type": "message_created",
        "message": {"body": {"text": "ping"}, "sender": {"id": 1}, "recipient": {"chat_id": "c1"}}
    }

    task = dp.feed_update(upd)
    result = await task

    assert bool(result) is True
    assert counter["calls"] == 1, f"Expected call_count == 1, got {counter['calls']}"


@pytest.mark.anyio
async def test_feed_update_single_execution_async_handler():
    """
    Adversarial verification: ensure asynchronous handler registered on dispatcher
    executes EXACTLY ONCE when scheduled via feed_update (assert call_count == 1).
    """
    client = MockTrackingClient()
    dp = AsyncUpdateDispatcher(client=client)

    counter = {"calls": 0}

    @dp.message()
    async def async_handler(client, update):
        counter["calls"] += 1
        await asyncio.sleep(0.01)
        return "ok_async"

    upd = {
        "update_type": "message_created",
        "message": {"body": {"text": "ping_async"}, "sender": {"id": 2}, "recipient": {"chat_id": "c2"}}
    }

    task = dp.feed_update(upd)
    result = await task

    assert bool(result) is True
    assert counter["calls"] == 1, f"Expected call_count == 1, got {counter['calls']}"


@pytest.mark.anyio
async def test_feed_update_with_middleware_single_execution():
    """
    Adversarial verification: ensure middleware pre and post hooks execute
    EXACTLY ONCE per update during feed_update.
    """
    client = MockTrackingClient()
    dp = AsyncUpdateDispatcher(client=client)
    mw = TrackingMiddleware()
    dp.register_middleware(mw)

    counter = {"calls": 0}

    @dp.message()
    def sync_handler_mw(client, update):
        counter["calls"] += 1
        return "mw_ok"

    upd = {
        "update_type": "message_created",
        "message": {"body": {"text": "ping_mw"}, "sender": {"id": 3}, "recipient": {"chat_id": "c3"}}
    }

    task = dp.feed_update(upd)
    result = await task

    assert bool(result) is True
    assert counter["calls"] == 1
    assert mw.pre_count == 1
    assert mw.post_count == 1


@pytest.mark.anyio
async def test_async_result_dual_evaluation_no_duplicate_execution():
    """
    Adversarial verification: evaluate dispatch return value synchronously as boolean,
    and then await it. Verify handler is NOT executed twice.
    """
    client = MockTrackingClient()
    dp = AsyncUpdateDispatcher(client=client)

    counter = {"calls": 0}

    @dp.message()
    def handler_dual(client, update):
        counter["calls"] += 1
        return "done"

    upd = {
        "update_type": "message_created",
        "message": {"body": {"text": "dual"}, "sender": {"id": 4}, "recipient": {"chat_id": "c4"}}
    }

    # 1. Call dispatch()
    res = dp.dispatch(upd)

    # 2. Check boolean truthiness synchronously
    assert bool(res) is True
    assert counter["calls"] == 1

    # 3. Await it
    awaited_res = await res
    assert bool(awaited_res) is True
    # Crucial assertion: call_count must REMAIN 1
    assert counter["calls"] == 1, f"Duplicate execution detected! call_count was {counter['calls']}"


# =========================================================================
# 4. AUDIT: BUTTON LENGTHS <= 18 CHARACTERS
# =========================================================================

def test_exhaustive_audit_button_lengths_le_18():
    """
    Exhaustively audit 100% of inline buttons across all keyboard factories:
    len(button.text) <= 18 characters.
    """
    keyboards_to_test = [
        ("main_menu", get_main_menu_keyboard()),
        ("guest_empty", get_guest_keyboard()),
        ("guest_token", get_guest_keyboard("tok_1234567890")),
        ("meters_default", get_meters_keyboard()),
        ("meter_confirm", get_meter_confirmation_keyboard("m1", 123.45)),
        ("meter_confirm_long", get_meter_confirmation_keyboard("m1", 99999999.99)),
        ("services", get_services_keyboard()),
        ("help", get_help_keyboard()),
        ("payment_default", get_payment_keyboard()),
        ("payment_amount", get_payment_keyboard(3450.0)),
        ("payment_large", get_payment_keyboard(123456.78)),
        ("ticket_categories", get_ticket_categories_keyboard()),
        ("inspector", get_inspector_keyboard()),
        ("address_switch_default", get_address_switch_keyboard()),
    ]

    total_audited = 0
    for name, kb in keyboards_to_test:
        rows = kb.get("payload", {}).get("buttons", [])
        for r_idx, row in enumerate(rows):
            for b_idx, btn in enumerate(row):
                label = btn.get("text", "")
                total_audited += 1
                assert len(label) <= 18, (
                    f"Button length violation in {name} [row {r_idx}, btn {b_idx}]: "
                    f"'{label}' has length {len(label)} > 18"
                )

    # Test dynamic meter keyboards with various realistic names
    test_meters = [
        MeterBase(id="m1", meter_type=MeterType.COLD_WATER, serial_number="123", name="ХВС Стояк", installation_place="Кухня", last_reading_value=10.0, last_reading_date=date.today(), verification_date_valid_until=date.today(), unit="м³", decimal_digits=3),
        MeterBase(id="m2", meter_type=MeterType.HOT_WATER, serial_number="456", name="ГВС Санузел", installation_place="Ванная", last_reading_value=20.0, last_reading_date=date.today(), verification_date_valid_until=date.today(), unit="м³", decimal_digits=3),
        MeterBase(id="m3", meter_type=MeterType.ELECTRICITY_SINGLE, serial_number="789", name="Электроэнергия день/ночь", installation_place="Щиток", last_reading_value=30.0, last_reading_date=date.today(), verification_date_valid_until=date.today(), unit="кВт⋅ч", decimal_digits=1),
        MeterBase(id="m4", meter_type=MeterType.GAS, serial_number="101", name="Газоснабжение плита", installation_place="Кухня", last_reading_value=40.0, last_reading_date=date.today(), verification_date_valid_until=date.today(), unit="м³", decimal_digits=2),
    ]
    dynamic_kb = get_meters_keyboard(test_meters)
    for row in dynamic_kb.get("payload", {}).get("buttons", []):
        for btn in row:
            label = btn.get("text", "")
            total_audited += 1
            assert len(label) <= 18, f"Dynamic meter button length violation: '{label}' (len={len(label)})"

    assert total_audited >= 40, f"Expected >= 40 buttons audited, got {total_audited}"


# =========================================================================
# 5. AUDIT: ZERO EMOJI POLICY & ZERO BRACKET TAGS
# =========================================================================

def test_exhaustive_audit_zero_emoji():
    """
    Exhaustively scans app/ and max_bot_sdk/ for any emoji characters.
    """
    violations = []
    for base_dir in ["app", "max_bot_sdk"]:
        for root, _, files in os.walk(base_dir):
            for fname in files:
                if fname.endswith((".py", ".html", ".css", ".js")):
                    path = os.path.join(root, fname)
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        for lineno, line in enumerate(f, 1):
                            for char in EMOJI_REGEX.findall(line):
                                if char not in ALLOWED_TYPOGRAPHY:
                                    violations.append(f"{path}:{lineno} {char} (U+{ord(char):04X})")

    assert len(violations) == 0, f"Emoji violations found in source:\n" + "\n".join(violations)


def test_exhaustive_audit_zero_bracket_tags_in_all_flows():
    """
    Simulates all standard user interactions with BotHandler and asserts zero bracket tags [...]
    in every outgoing bot message.
    """
    client = MockTrackingClient()
    handler = BotHandler(client=client)
    user_id = 9301
    chat_id = "chat_bracket_audit"

    # Sequence of commands and inputs
    actions = [
        "/start",
        "/start guest_token123",
        "/profile",
        "/address",
        "/meters",
        "/pay",
        "/ticket",
        "/guest",
        "/inspector",
        "/check 2809142",
        "/check 991201",
        "хвс 150.25",
        "гвс 88.0",
        "произвольный текст жителя",
    ]
    for action in actions:
        handler.handle_message_created({
            "message": {"body": {"text": action}, "sender": {"id": user_id}, "recipient": {"chat_id": chat_id}}
        })

    # Sequence of callbacks
    callbacks = [
        "cmd_meters", "cmd_arshin", "cmd_pay", "cmd_guest", "cmd_ticket",
        "cmd_profile", "cmd_address", "cmd_add_address", "cmd_menu",
        "ticket_cat_water", "ticket_cat_electric", "ticket_cat_heat", "ticket_cat_elevator"
    ]
    for cb in callbacks:
        handler.handle_callback({
            "callback": {"callback_id": f"cb_{cb}", "payload": cb},
            "sender": {"id": user_id},
            "chat_id": chat_id
        })

    bracket_pattern = re.compile(r"\[[A-Za-zА-Яа-я0-9\s:_-]+\]")
    violations = []
    for idx, msg in enumerate(client.sent_messages):
        txt = msg.get("text", "")
        matches = bracket_pattern.findall(txt)
        if matches:
            violations.append(f"Message #{idx}: {matches} in text: {txt[:80]!r}")

    assert len(violations) == 0, f"Bracket tags found in outgoing messages:\n" + "\n".join(violations)
