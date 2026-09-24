"""
Empirical Adversarial Verification Suite: AsyncUpdateDispatcher & Bot FSM Dialogs.
Conducted by challenger_bot_fsm_6.

Scope:
1. AsyncUpdateDispatcher:
   - Synchronous handlers execute EXACTLY ONCE when passed to feed_update (assert call count == 1).
   - Synchronous handlers execute EXACTLY ONCE when awaited via async pipelines (assert call count == 1).
   - Synchronous handlers execute EXACTLY ONCE with and without middlewares.
   - Asynchronous handlers execute EXACTLY ONCE.
   - Message delivery verification: assert exactly 1 message delivered, no duplicates.
   - Non-blocking rapid flooding of updates: 100 updates across 20 users, ingestion < 0.08s, all completed.
2. Bot FSM Dialogs:
   - Ticket creation from text (_create_ticket_from_text) without AttributeError on ticket.ticket_number or ticket.id.
   - User state correctly reset (fsm.reset_state()) and user NOT stuck in WAITING_TICKET_DESC.
   - Cancellation, category selection, and state transitions.
3. Audits:
   - 100% of inline buttons length <= 18 chars.
   - Zero emoji policy across codebase and outgoing bot messages.
   - Zero bracket tags [...] in outgoing bot messages.
"""

import asyncio
import os
import re
import tempfile
import time
import unicodedata
from datetime import date
from typing import Dict, Any, List, Optional
import pytest

from max_bot_sdk import (
    AsyncUpdateDispatcher,
    Update,
    UserState,
    State,
    StatesGroup,
    FSMContext,
    MemoryStorage,
    SQLiteStorage,
    Button,
    KeyboardBuilder,
)
from max_bot_sdk.middlewares.base import BaseMiddleware
from app.config import settings
from app.bot.handlers import BotHandler, get_bot_handler
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
from app.services.profile_service import profile_service, UserProperty


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


class CountingMiddleware(BaseMiddleware):
    """Middleware to verify middleware execution count."""
    def __init__(self):
        self.pre_count = 0
        self.post_count = 0

    async def pre_process(self, update: Update, data: Dict[str, Any]) -> bool:
        self.pre_count += 1
        return True

    async def post_process(self, update: Update, data: Dict[str, Any], result: Any = None, exception: Optional[Exception] = None) -> None:
        self.post_count += 1


# Strict Unicode Emoji regex pattern
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
# 1. DISPATCHER: EXACTLY-ONCE EXECUTION & RAPID FLOODING HARNESS
# =========================================================================

@pytest.mark.anyio
async def test_sync_handler_executes_exactly_once_via_feed_update():
    """
    Empirically verify that a synchronous handler passed to feed_update executes
    EXACTLY ONCE (assert call_count == 1, len(sent_messages) == 1, no duplicate messages).
    """
    client = MockTrackingClient()
    dp = AsyncUpdateDispatcher(client=client)

    call_count = 0

    @dp.message()
    def sync_message_handler(client, update):
        nonlocal call_count
        call_count += 1
        client.send_message(
            chat_id=update.effective_chat_id,
            user_id=update.sender_user_id,
            text=f"Processed sync: {update.text}"
        )
        return "sync_result"

    upd = {
        "update_type": "message_created",
        "message": {
            "body": {"text": "hello sync"},
            "sender": {"id": 101},
            "recipient": {"chat_id": "chat_101"}
        }
    }

    # Dispatch via feed_update
    task = dp.feed_update(upd)
    res = await task

    assert res is True
    assert call_count == 1, f"Expected exactly 1 execution, got {call_count}"
    assert len(client.sent_messages) == 1, f"Expected 1 sent message, got {len(client.sent_messages)}"
    assert client.sent_messages[0]["text"] == "Processed sync: hello sync"


@pytest.mark.anyio
async def test_sync_handler_executes_exactly_once_when_awaited_in_async_pipeline():
    """
    Empirically verify that awaiting dp.dispatch(update) for a synchronous handler
    executes EXACTLY ONCE (assert call_count == 1).
    """
    client = MockTrackingClient()
    dp = AsyncUpdateDispatcher(client=client)

    call_count = 0

    @dp.command("sync_cmd")
    def sync_command_handler(client, update):
        nonlocal call_count
        call_count += 1
        client.send_message(
            chat_id=update.effective_chat_id,
            user_id=update.sender_user_id,
            text="Sync command executed"
        )
        return 42

    upd = {
        "update_type": "message_created",
        "message": {
            "body": {"text": "/sync_cmd"},
            "sender": {"id": 102},
            "recipient": {"chat_id": "chat_102"}
        }
    }

    # Directly await dispatch()
    res = await dp.dispatch(upd)

    assert bool(res) is True
    assert call_count == 1, f"Expected exactly 1 execution, got {call_count}"
    assert len(client.sent_messages) == 1, f"Expected 1 sent message, got {len(client.sent_messages)}"


@pytest.mark.anyio
async def test_sync_handler_with_middlewares_executes_exactly_once():
    """
    Empirically verify that a synchronous handler with an active middleware pipeline
    executes EXACTLY ONCE under feed_update and under direct await.
    """
    client = MockTrackingClient()
    dp = AsyncUpdateDispatcher(client=client)
    mw = CountingMiddleware()
    dp.register_middleware(mw)

    call_count = 0

    @dp.message()
    def sync_mw_handler(client, update):
        nonlocal call_count
        call_count += 1
        client.send_message(
            chat_id=update.effective_chat_id,
            user_id=update.sender_user_id,
            text=f"Processed mw: {update.text}"
        )
        return "mw_done"

    upd = {
        "update_type": "message_created",
        "message": {
            "body": {"text": "hello mw"},
            "sender": {"id": 103},
            "recipient": {"chat_id": "chat_103"}
        }
    }

    task = dp.feed_update(upd)
    await task

    assert call_count == 1, f"Expected exactly 1 execution, got {call_count}"
    assert mw.pre_count == 1, f"Expected 1 pre-process, got {mw.pre_count}"
    assert mw.post_count == 1, f"Expected 1 post-process, got {mw.post_count}"
    assert len(client.sent_messages) == 1, f"Expected 1 sent message, got {len(client.sent_messages)}"


@pytest.mark.anyio
async def test_async_handler_executes_exactly_once():
    """
    Empirically verify that an asynchronous handler executes EXACTLY ONCE
    when passed to feed_update and awaited.
    """
    client = MockTrackingClient()
    dp = AsyncUpdateDispatcher(client=client)

    call_count = 0

    @dp.message()
    async def async_msg_handler(client, update):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)
        client.send_message(
            chat_id=update.effective_chat_id,
            user_id=update.sender_user_id,
            text="Async ok"
        )
        return "async_done"

    upd = {
        "update_type": "message_created",
        "message": {
            "body": {"text": "test async"},
            "sender": {"id": 104},
            "recipient": {"chat_id": "chat_104"}
        }
    }

    task = dp.feed_update(upd)
    await task

    assert call_count == 1, f"Expected exactly 1 execution, got {call_count}"
    assert len(client.sent_messages) == 1


@pytest.mark.anyio
async def test_rapid_concurrent_flooding_non_blocking_and_no_duplicates():
    """
    Flood feed_update with 100 updates across 20 users.
    Verify:
    - Non-blocking ingestion (< 0.08s)
    - Exactly 100 executions across all users (0 duplicate executions)
    - Zero dropped updates
    - All active tasks drained via wait_closed()
    """
    client = MockTrackingClient()
    dp = AsyncUpdateDispatcher(client=client)

    exec_counts: Dict[str, int] = {}
    lock = asyncio.Lock()

    @dp.command("flood_cmd")
    async def handle_flood(update: Update, state: FSMContext):
        uid = str(update.sender_user_id)
        await asyncio.sleep(0.05)
        async with lock:
            exec_counts[uid] = exec_counts.get(uid, 0) + 1

    num_updates = 100
    num_users = 20

    t_start = time.monotonic()
    tasks = []
    for i in range(num_updates):
        uid = (i % num_users) + 1
        upd = {
            "update_type": "message_created",
            "message": {
                "body": {"text": f"/flood_cmd {i}"},
                "sender": {"id": uid},
                "recipient": {"chat_id": f"chat_{uid}"}
            }
        }
        tasks.append(dp.feed_update(upd))
    t_ingest = time.monotonic() - t_start

    assert t_ingest < 0.08, f"feed_update blocked! Ingestion took {t_ingest:.4f}s"
    assert len(dp._active_tasks) == num_updates

    await dp.wait_closed(timeout=3.0)
    assert len(dp._active_tasks) == 0

    total_executions = sum(exec_counts.values())
    assert total_executions == num_updates, f"Expected {num_updates} total executions, got {total_executions}"
    for uid in range(1, num_users + 1):
        assert exec_counts[str(uid)] == 5, f"User {uid} executions mismatch: {exec_counts[str(uid)]}"


# =========================================================================
# 2. BOT FSM DIALOGS: TICKET CREATION & STATE TRANSITIONS
# =========================================================================

def test_ticket_creation_from_text_no_attribute_error_and_state_reset():
    """
    Empirically verify ticket creation from text (_create_ticket_from_text):
    - Ensure no AttributeError on ticket.ticket_number or ticket.id.
    - Verify that user state is correctly reset and user is NOT stuck in WAITING_TICKET_DESC.
    - Verify reply message contains ticket ID and registration confirmation.
    """
    client = MockTrackingClient()
    handler = BotHandler(client=client)
    chat_id = "chat_tck_1"
    user_id = 9001
    fsm = handler.get_fsm_context(chat_id, user_id)

    # 1. User opens ticket flow
    handler.handle_callback({
        "callback": {"callback_id": "cb_tck_start", "payload": "cmd_ticket", "user": {"id": user_id}},
        "sender": {"id": user_id},
        "chat_id": chat_id
    })
    assert fsm.current_state == UserState.WAITING_TICKET_DESC.value
    assert fsm.get_state() == UserState.WAITING_TICKET_DESC.value

    # 2. User selects category 'Электрика'
    handler.handle_callback({
        "callback": {"callback_id": "cb_tck_cat", "payload": "ticket_cat_electric", "user": {"id": user_id}},
        "sender": {"id": user_id},
        "chat_id": chat_id
    })
    assert fsm.current_state == UserState.WAITING_TICKET_DESC.value
    data = fsm.get_data()
    assert data.get("category") == "electric"

    # 3. User submits description text
    client.sent_messages.clear()
    desc = "Искрит щиток в коридоре на 4 этаже"
    handler.handle_message_created({
        "message": {
            "body": {"text": desc},
            "sender": {"id": user_id, "first_name": "Игорь"},
            "recipient": {"chat_id": chat_id}
        }
    })

    # Assert exactly 1 reply message sent
    assert len(client.sent_messages) == 1, f"Expected 1 reply message, got {len(client.sent_messages)}"
    sent = client.sent_messages[0]["text"]
    assert "Заявка в УК зарегистрирована" in sent
    assert "TCK-2026-" in sent
    assert "Электрика" in sent or "electric" in sent or "Срочная" in sent
    assert desc in sent

    # Crucial: User state MUST be reset to None (IDLE), NOT stuck in WAITING_TICKET_DESC
    assert fsm.current_state is None, f"User still stuck in state: {fsm.current_state}"
    assert fsm.get_state() == None

    # Subsequent normal text should NOT be treated as a ticket
    client.sent_messages.clear()
    handler.handle_message_created({
        "message": {
            "body": {"text": "обычное сообщение"},
            "sender": {"id": user_id, "first_name": "Игорь"},
            "recipient": {"chat_id": chat_id}
        }
    })
    assert len(client.sent_messages) == 1
    assert "Заявка в УК зарегистрирована" not in client.sent_messages[0]["text"]


def test_ticket_creation_cancellation_flow():
    """
    Test user entering WAITING_TICKET_DESC and clicking 'Отмена' (cmd_menu).
    State must immediately reset to None, and main menu must be shown.
    """
    client = MockTrackingClient()
    handler = BotHandler(client=client)
    chat_id = "chat_tck_cancel"
    user_id = 9002
    fsm = handler.get_fsm_context(chat_id, user_id)

    # 1. Start ticket creation
    handler.handle_callback({
        "callback": {"callback_id": "cb_cancel_1", "payload": "cmd_ticket", "user": {"id": user_id}},
        "sender": {"id": user_id},
        "chat_id": chat_id
    })
    assert fsm.current_state == UserState.WAITING_TICKET_DESC.value

    # 2. Click cancel
    client.sent_messages.clear()
    handler.handle_callback({
        "callback": {"callback_id": "cb_cancel_2", "payload": "cmd_menu", "user": {"id": user_id}},
        "sender": {"id": user_id},
        "chat_id": chat_id
    })
    assert fsm.current_state is None
    assert fsm.get_state() == None
    assert len(client.sent_messages) == 1
    assert "MAX Умный Дом" in client.sent_messages[0]["text"]


def test_fsm_all_dialog_state_transitions():
    """
    Exhaustively verify all conversational FSM state transitions:
    - WAITING_METER_INPUT -> reset on numeric reading
    - WAITING_ARSHIN_SERIAL -> reset on serial check
    - WAITING_ADDRESS -> exposes AttributeError on add_property_manual
    """
    client = MockTrackingClient()
    handler = BotHandler(client=client)
    chat_id = "chat_fsm_all"
    user_id = 9003
    fsm = handler.get_fsm_context(chat_id, user_id)

    # A. WAITING_METER_INPUT
    handler.handle_callback({
        "callback": {"callback_id": "cb_m1", "payload": "cmd_meters", "user": {"id": user_id}},
        "sender": {"id": user_id},
        "chat_id": chat_id
    })
    assert fsm.current_state == UserState.WAITING_METER_INPUT.value
    # Submit reading
    handler.handle_message_created({
        "message": {"body": {"text": "150.25"}, "sender": {"id": user_id}, "recipient": {"chat_id": chat_id}}
    })
    assert fsm.current_state is None

    # B. WAITING_ARSHIN_SERIAL
    handler.handle_callback({
        "callback": {"callback_id": "cb_a1", "payload": "cmd_arshin", "user": {"id": user_id}},
        "sender": {"id": user_id},
        "chat_id": chat_id
    })
    assert fsm.current_state == UserState.WAITING_ARSHIN_SERIAL.value
    # Submit serial
    handler.handle_message_created({
        "message": {"body": {"text": "2809142"}, "sender": {"id": user_id}, "recipient": {"chat_id": chat_id}}
    })
    assert fsm.current_state is None

    # C. WAITING_ADDRESS:
    # Verifies add_property_manual cleanly completes without AttributeError and resets FSM state
    handler.handle_callback({
        "callback": {"callback_id": "cb_addr1", "payload": "cmd_add_address", "user": {"id": user_id}},
        "sender": {"id": user_id},
        "chat_id": chat_id
    })
    assert fsm.current_state == UserState.WAITING_ADDRESS.value
    handler.handle_message_created({
        "message": {"body": {"text": "г. Москва, ул. Арбат, д. 10"}, "sender": {"id": user_id}, "recipient": {"chat_id": chat_id}}
    })
    assert fsm.current_state is None
    assert fsm.get_state() == None
    user_prop = profile_service.get_active_property(user_id)
    assert user_prop.address == "г. Москва, ул. Арбат, д. 10"


# =========================================================================
# 3. AUDIT: 100% INLINE BUTTONS LENGTH <= 18 CHARS
# =========================================================================

def test_audit_100_percent_inline_buttons_length_le_18():
    """
    Audits 100% of inline buttons across all keyboards and generators:
    Asserts len(label) <= 18 for every single button.
    """
    all_keyboards = [
        ("main_menu", get_main_menu_keyboard()),
        ("services", get_services_keyboard()),
        ("help", get_help_keyboard()),
        ("guest_tok", get_guest_keyboard("abc123xyz")),
        ("guest_none", get_guest_keyboard(None)),
        ("ticket_cats", get_ticket_categories_keyboard()),
        ("inspector", get_inspector_keyboard()),
        ("pay_none", get_payment_keyboard(None)),
        ("pay_100", get_payment_keyboard(100.0)),
        ("pay_4850", get_payment_keyboard(4850.50)),
        ("pay_huge", get_payment_keyboard(9999999.0)),
        ("confirm_meter", get_meter_confirmation_keyboard("khvs", 145.2)),
        ("meters_default", get_meters_keyboard(None)),
        ("addr_switch_default", get_address_switch_keyboard(None)),
    ]

    total_buttons_audited = 0
    for name, kb in all_keyboards:
        rows = kb.get("payload", {}).get("buttons", [])
        for r_idx, row in enumerate(rows):
            for b_idx, btn in enumerate(row):
                label = btn.get("text", "")
                total_buttons_audited += 1
                assert len(label) <= 18, (
                    f"Button length violation in {name} row {r_idx} btn {b_idx}: "
                    f"'{label}' (length={len(label)})"
                )

    # Dynamic variations with long strings
    fuzz_meters = [
        MeterBase(id="m_long", meter_type=MeterType.COLD_WATER, serial_number="999999999",
                  name="Очень длинное наименование счетчика воды", installation_place="Кухня",
                  last_reading_value=10.0, last_reading_date=date.today(),
                  verification_date_valid_until=date.today(), unit="м³", decimal_digits=3)
    ]
    kb_dynamic = get_meters_keyboard(fuzz_meters)
    for row in kb_dynamic.get("payload", {}).get("buttons", []):
        for btn in row:
            label = btn.get("text", "")
            total_buttons_audited += 1
            assert len(label) <= 18, f"Dynamic meter button too long: '{label}' (len={len(label)})"

    # Button sanitizer helper audit
    for s in ["1234567890123456789", "A" * 100, "Длинный текст кнопки для проверки"]:
        btn_cb = Button.callback(s, "payload")
        assert len(btn_cb["text"]) <= 18
        btn_link = Button.link(s, "https://max.ru")
        assert len(btn_link["text"]) <= 18
        btn_app = Button.open_app(s, "bot")
        assert len(btn_app["text"]) <= 18
        total_buttons_audited += 3

    assert total_buttons_audited >= 40, f"Expected >= 40 buttons audited, got {total_buttons_audited}"


# =========================================================================
# 4. AUDIT: ZERO EMOJI POLICY & ZERO BRACKET TAGS
# =========================================================================

def test_audit_zero_emoji_in_source_and_responses():
    """
    Audits zero emoji policy:
    1. Across all files in app/ and max_bot_sdk/
    2. Across all responses generated by BotHandler
    """
    # 1. Source code scan
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

    assert len(violations) == 0, f"Emoji violations found in source code:\n" + "\n".join(violations)

    # 2. Outgoing responses scan
    client = MockTrackingClient()
    handler = BotHandler(client=client)
    user_id = 9005
    chat_id = "chat_zero_emoji"

    commands = [
        "/start", "/meters", "/check 2809142", "/check 991201",
        "/pay", "/ticket", "/guest", "/inspector", "/profile", "/address"
    ]
    for cmd in commands:
        handler.handle_message_created({
            "message": {"body": {"text": cmd}, "sender": {"id": user_id}, "recipient": {"chat_id": chat_id}}
        })

    response_emojis = []
    for idx, msg in enumerate(client.sent_messages):
        text = msg.get("text", "")
        for char in EMOJI_REGEX.findall(text):
            if char not in ALLOWED_TYPOGRAPHY:
                response_emojis.append(f"Msg #{idx}: {char} (U+{ord(char):04X}) in {text[:40]}")

    assert len(response_emojis) == 0, f"Emoji violations in bot responses:\n" + "\n".join(response_emojis)


def test_audit_zero_bracket_tags_in_outgoing_messages():
    """
    Audits zero bracket tags [...] in outgoing bot messages.
    """
    client = MockTrackingClient()
    handler = BotHandler(client=client)
    user_id = 9006
    chat_id = "chat_zero_bracket"

    test_actions = [
        "/start", "/meters", "/check", "/check 2809142", "/check 991201",
        "/pay", "/ticket", "/guest", "/inspector", "/profile", "/address",
        "хвс 145.5", "гвс 99.1", "вода invalid"
    ]
    for action in test_actions:
        handler.handle_message_created({
            "message": {"body": {"text": action}, "sender": {"id": user_id}, "recipient": {"chat_id": chat_id}}
        })

    callbacks = [
        "cmd_meters", "cmd_arshin", "cmd_pay", "cmd_ticket", "cmd_guest",
        "cmd_inspector", "cmd_menu", "cmd_profile", "cmd_address",
        "ticket_cat_water", "ticket_cat_electric"
    ]
    for cb in callbacks:
        handler.handle_callback({
            "callback": {"callback_id": f"cb_{cb}", "payload": cb},
            "sender": {"id": user_id},
            "chat_id": chat_id
        })

    bracket_pattern = re.compile(r"\[[A-Za-zА-Яа-я0-9\s:_-]+\]")
    bracket_violations = []

    for idx, msg in enumerate(client.sent_messages):
        txt = msg.get("text", "")
        matches = bracket_pattern.findall(txt)
        if matches:
            bracket_violations.append(f"Msg #{idx}: {matches} in {txt[:60]!r}")

    assert len(bracket_violations) == 0, (
        f"Bracket tag violations found in bot responses:\n" + "\n".join(bracket_violations)
    )
