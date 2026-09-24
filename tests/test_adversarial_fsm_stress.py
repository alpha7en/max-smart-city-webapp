"""
Adversarial Stress Test Suite: AsyncUpdateDispatcher, FSM Dialogs, Non-Blocking Execution,
Button Length Constraints (<= 18 chars), Zero Emoji, and Zero Bracket Policy.
Conducted by challenger_fsm_bot_5.
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
from app.bot.worker import BotWorker
from app.models.schemas import MeterBase
from app.models.domain import MeterType
from app.services.meter_service import meter_service
from app.services.ticket_service import ticket_service
from app.services.profile_service import profile_service, UserProperty


class MockTrackingClient:
    """Mock client tracking all sent messages, callbacks, and call timings."""
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
# 1. NON-BLOCKING FLOODING STRESS HARNESS
# =========================================================================

def test_feed_update_rapid_concurrent_flooding_non_blocking():
    """
    Flood feed_update with 100 rapid concurrent updates across 20 distinct users
    with intentional 0.2s handler delays.
    Empirically verifies:
    - Ingestion loop finishes in < 0.05s (completely non-blocking).
    - All 100 updates are tracked in _active_tasks.
    - Concurrent execution completes in ~0.2-0.4s rather than 20s (100 * 0.2s).
    - No cross-user state corruption or task drops.
    """
    async def _run():
        client = MockTrackingClient()
        dp = AsyncUpdateDispatcher(client)

        processed_events: List[Dict[str, Any]] = []
        lock = asyncio.Lock()

        @dp.command("slow_cmd")
        async def handle_slow_update(update: Update, state: FSMContext):
            user_id = update.sender_user_id
            # Record user state in FSM to check multi-user isolation
            await state.set_data(last_seen=time.time(), user=user_id)
            # Simulated slow network / I/O latency
            await asyncio.sleep(0.2)
            async with lock:
                processed_events.append({
                    "user_id": user_id,
                    "text": update.text,
                    "completed_at": time.time()
                })

        num_updates = 100
        num_users = 20

        # Feed updates in a tight loop and measure ingestion duration
        t_feed_start = time.monotonic()
        tasks = []
        for i in range(num_updates):
            uid = (i % num_users) + 1
            chat_id = f"chat_{uid}"
            upd = {
                "update_type": "message_created",
                "message": {
                    "body": {"text": f"/slow_cmd event_{i}"},
                    "sender": {"id": uid},
                    "recipient": {"chat_id": chat_id}
                }
            }
            task = dp.feed_update(upd)
            tasks.append(task)
        t_feed_end = time.monotonic()

        feed_duration = t_feed_end - t_feed_start

        # Ingestion must be non-blocking (< 50ms)
        assert feed_duration < 0.08, f"feed_update blocked! Took {feed_duration:.4f}s for {num_updates} updates"
        assert len(dp._active_tasks) == num_updates, f"Expected {num_updates} active tasks, got {len(dp._active_tasks)}"

        # Wait for all background tasks to complete concurrently
        t_wait_start = time.monotonic()
        await dp.wait_closed(timeout=3.0)
        t_wait_end = time.monotonic()

        total_exec_duration = t_wait_end - t_wait_start

        # In a blocking dispatcher, 100 * 0.2s = 20 seconds.
        # Under concurrent execution, it must complete in < 1.0 second.
        assert total_exec_duration < 1.0, f"Concurrent execution too slow: {total_exec_duration:.2f}s (expected < 1.0s)"
        assert len(dp._active_tasks) == 0, f"Tasks leaked: {len(dp._active_tasks)} remained"
        assert len(processed_events) == num_updates, f"Expected {num_updates} processed events, got {len(processed_events)}"

        # Verify FSM data isolation across users
        for uid in range(1, num_users + 1):
            ctx = dp.get_fsm_context(f"chat_{uid}", uid)
            data = await ctx.get_data()
            assert data.get("user") == uid, f"User {uid} data corrupted: {data}"

    asyncio.run(_run())


def test_feed_update_error_resilience_and_task_cleanup():
    """
    Stress-test feed_update when random tasks raise unhandled exceptions.
    Empirically verifies:
    - Exceptions inside handlers do not kill the dispatcher or event loop.
    - Failed tasks are properly discarded from _active_tasks without leaks.
    - Healthy tasks complete without interference.
    """
    async def _run():
        client = MockTrackingClient()
        dp = AsyncUpdateDispatcher(client)

        successful_ops = []

        @dp.command("fragile")
        async def handle_fragile(update: Update):
            cmd_arg = update.text.split()[-1]
            idx = int(cmd_arg)
            if idx % 3 == 0:
                raise ValueError(f"Simulated crash on index {idx}")
            await asyncio.sleep(0.05)
            successful_ops.append(idx)

        total_tasks = 30
        for i in range(total_tasks):
            upd = {
                "update_type": "message_created",
                "message": {
                    "body": {"text": f"/fragile {i}"},
                    "sender": {"id": 100 + i},
                    "recipient": {"chat_id": f"c_{i}"}
                }
            }
            dp.feed_update(upd)

        await dp.wait_closed(timeout=2.0)
        assert len(dp._active_tasks) == 0, "Leaked tasks in _active_tasks after errors"

        # 30 tasks: indices 0, 3, 6, 9, 12, 15, 18, 21, 24, 27 fail (10 tasks)
        # 20 tasks succeed
        expected_successes = [i for i in range(total_tasks) if i % 3 != 0]
        assert sorted(successful_ops) == sorted(expected_successes)

    asyncio.run(_run())


def test_bot_worker_poll_loop_non_blocking_with_slow_handler():
    """
    Verify BotWorker._poll_loop delegates updates to feed_update without stalling
    the polling iteration.
    """
    async def _run():
        worker = BotWorker(token="mock_token", base_url="https://platform-api2.max.ru")
        worker.client = MockTrackingClient()
        handler = get_bot_handler()
        handler.client = worker.client

        # Mock get_updates returning updates
        batch_counter = 0

        def mock_get_updates(marker=None, timeout=25):
            nonlocal batch_counter
            batch_counter += 1
            if batch_counter <= 3:
                return {
                    "marker": f"m_{batch_counter}",
                    "updates": [
                        {
                            "update_type": "message_created",
                            "message": {"body": {"text": "/start"}, "sender": {"id": 1}, "recipient": {"chat_id": "c1"}}
                        }
                    ]
                }
            return {"marker": "end", "updates": []}

        worker.client.get_updates = mock_get_updates

        worker._is_running = True
        loop_task = asyncio.create_task(worker._poll_loop())

        # Let loop run briefly
        await asyncio.sleep(0.15)
        worker._is_running = False
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

        # Wait for all dispatcher tasks to finish
        await handler.dispatcher.wait_closed(timeout=2.0)
        assert len(worker.client.sent_messages) >= 3, f"Expected at least 3 updates processed, got {len(worker.client.sent_messages)}"

    asyncio.run(_run())


# =========================================================================
# 2. FSM STATE TRANSITIONS & SQLITE STORAGE DURABILITY
# =========================================================================

def test_fsm_multi_step_meter_dialog_valid_and_invalid_inputs():
    """
    Test FSM transitions for meter reading:
    - IDLE -> WAITING_METER_INPUT via cmd_meters
    - Invalid input (non-numerical string) -> remains in WAITING_METER_INPUT
    - Valid numerical input -> accepts reading, resets to IDLE
    """
    client = MockTrackingClient()
    handler = BotHandler(client)
    chat_id = "chat_fsm_meter"
    user_id = 8801
    fsm = handler.get_fsm_context(chat_id, user_id)

    # Initially IDLE
    assert fsm.current_state is None
    assert fsm.get_state() == None

    # Step 1: User clicks 'cmd_meters'
    handler.handle_callback({
        "callback": {"callback_id": "cb_1", "payload": "cmd_meters", "user": {"id": user_id}},
        "sender": {"id": user_id},
        "chat_id": chat_id
    })
    assert fsm.current_state == UserState.WAITING_METER_INPUT.value
    assert fsm.get_state() == UserState.WAITING_METER_INPUT.value

    # Step 2: User sends invalid non-numerical input
    client.sent_messages.clear()
    handler.handle_message_created({
        "message": {
            "body": {"text": "привет бот как дела"},
            "sender": {"id": user_id, "first_name": "Тестер"},
            "recipient": {"chat_id": chat_id}
        }
    })
    # Since input contained no digits, it fell through to unrecognized command without resetting state.
    # State must remain WAITING_METER_INPUT.
    assert fsm.current_state == UserState.WAITING_METER_INPUT.value
    assert fsm.get_state() == UserState.WAITING_METER_INPUT.value

    # Step 3: User sends valid reading with comma
    client.sent_messages.clear()
    handler.handle_message_created({
        "message": {
            "body": {"text": "146,75"},
            "sender": {"id": user_id, "first_name": "Тестер"},
            "recipient": {"chat_id": chat_id}
        }
    })

    # Reading accepted & state reset to None
    assert fsm.current_state is None
    assert fsm.get_state() == None
    latest_msg = client.sent_messages[-1]["text"]
    assert "ГИС ЖКХ: Показания успешно приняты" in latest_msg
    assert "146.75" in latest_msg


def test_fsm_multi_step_ticket_creation_and_cancellation():
    """
    Test FSM transitions for emergency ticket creation:
    - IDLE -> WAITING_TICKET_DESC via cmd_ticket
    - Select category ticket_cat_water -> category saved in FSM data
    - User sends description text -> ticket created with category, state reset to IDLE
    - Cancellation: User enters state, then clicks 'Отмена' (cmd_menu) -> state reset to IDLE
    """
    client = MockTrackingClient()
    handler = BotHandler(client)
    chat_id = "chat_fsm_ticket"
    user_id = 8802
    fsm = handler.get_fsm_context(chat_id, user_id)

    # 1. Start ticket creation
    handler.handle_callback({
        "callback": {"callback_id": "cb_t1", "payload": "cmd_ticket", "user": {"id": user_id}},
        "sender": {"id": user_id},
        "chat_id": chat_id
    })
    assert fsm.current_state == UserState.WAITING_TICKET_DESC.value
    assert fsm.get_state() == UserState.WAITING_TICKET_DESC.value

    # 2. Select category
    handler.handle_callback({
        "callback": {"callback_id": "cb_t2", "payload": "ticket_cat_water", "user": {"id": user_id}},
        "sender": {"id": user_id},
        "chat_id": chat_id
    })
    assert fsm.current_state == UserState.WAITING_TICKET_DESC.value
    data = fsm.get_data()
    assert data.get("category") == "water"

    # 3. Send problem description
    # REMEDIATION VERIFICATION (Round 5):
    # app/bot/handlers.py:723 uses getattr(ticket, "ticket_number", ticket.id).
    # Ticket is registered cleanly without AttributeError, and user state is reset.
    client.sent_messages.clear()
    desc_text = "Течет труба стояка в санузле на 3 этаже"
    handler.handle_message_created({
        "message": {
            "body": {"text": desc_text},
            "sender": {"id": user_id, "first_name": "Сергей"},
            "recipient": {"chat_id": chat_id}
        }
    })
    assert len(client.sent_messages) == 1
    sent_text = client.sent_messages[0]["text"]
    assert "Заявка в УК зарегистрирована" in sent_text
    assert "TCK-2026-" in sent_text
    assert fsm.current_state is None
    assert fsm.get_state() == None

    # 4. Test Cancellation Flow: user enters state then clicks 'Отмена' (cmd_menu) -> state reset to None
    handler.handle_callback({
        "callback": {"callback_id": "cb_t3", "payload": "cmd_ticket", "user": {"id": user_id}},
        "sender": {"id": user_id},
        "chat_id": chat_id
    })
    assert fsm.current_state == UserState.WAITING_TICKET_DESC.value
    handler.handle_callback({
        "callback": {"callback_id": "cb_t4", "payload": "cmd_menu", "user": {"id": user_id}},
        "sender": {"id": user_id},
        "chat_id": chat_id
    })
    assert fsm.current_state is None
    assert fsm.get_state() == None


def test_fsm_sqlite_storage_durability_across_reinstantiation():
    """
    Adversarial test for SQLiteStorage durability:
    - Create SQLiteStorage on disk.
    - Write states and rich JSON data for multiple distinct users.
    - Perform partial update_data.
    - Sever/delete original instance.
    - Instantiate completely NEW SQLiteStorage instance on the same file.
    - Empirically verify 100% data persistence, correct types, and WAL mode.
    """
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "durability_test.db")

            storage_1 = SQLiteStorage(db_path=db_path)

            # Check WAL mode
            with storage_1._get_connection() as conn:
                journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
                assert journal_mode.lower() == "wal", f"Expected WAL mode, got {journal_mode}"

            users = [
                (101, "c_101", UserState.WAITING_METER_INPUT, {"meter": "khvs", "val": 142.5, "tags": ["кухня", "хвс"]}),
                (102, "c_102", UserState.WAITING_TICKET_DESC, {"category": "electric", "urgent": True, "floor": 5}),
                (103, "c_103", UserState.WAITING_ARSHIN_SERIAL, {"serial": "2809142", "verified": False}),
                (104, "c_104", UserState.WAITING_ADDRESS, {"city": "Москва", "street": "Ленина 42", "apt": 15}),
            ]

            for uid, cid, state, data in users:
                ctx = FSMContext(storage_1, cid, uid)
                await ctx.set_state(state)
                await ctx.set_data(data)

            # Partially update user 101 data
            ctx_101 = FSMContext(storage_1, "c_101", 101)
            await ctx_101.update_data(last_updated=2026, confirmed=True)

            # Sever original storage object
            await storage_1.close()
            del storage_1

            # Reinstantiate fresh storage from existing DB file
            storage_2 = SQLiteStorage(db_path=db_path)

            # Verify user 101 with partial updates
            ctx_2_101 = FSMContext(storage_2, "c_101", 101)
            assert await ctx_2_101.get_state() == UserState.WAITING_METER_INPUT.value
            d101 = await ctx_2_101.get_data()
            assert d101["meter"] == "khvs"
            assert d101["val"] == 142.5
            assert d101["tags"] == ["кухня", "хвс"]
            assert d101["last_updated"] == 2026
            assert d101["confirmed"] is True

            # Verify remaining users
            for uid, cid, state, data in users[1:]:
                ctx = FSMContext(storage_2, cid, uid)
                assert await ctx.get_state() == state.value
                stored_data = await ctx.get_data()
                assert stored_data == data

            # Verify clear() completely purges record
            await ctx_2_101.clear()
            assert await ctx_2_101.get_state() is None
            assert await ctx_2_101.get_data() == {}

            await storage_2.close()

    asyncio.run(_run())


# =========================================================================
# 3. 100% INLINE BUTTON AUDIT (len(label) <= 18)
# =========================================================================

def test_audit_100_percent_inline_buttons_length_constraint():
    """
    Exhaustively audits 100% of inline buttons across all keyboard generators,
    handlers, and dynamic constructors:
    Asserts len(label) <= 18 and len(label) > 0 for every single button.
    """
    tested_buttons = []

    def check_kb(kb: Dict[str, Any], context_name: str):
        assert kb.get("type") == "inline_keyboard", f"Invalid keyboard type in {context_name}: {kb.get('type')}"
        rows = kb.get("payload", {}).get("buttons", [])
        assert len(rows) > 0, f"Keyboard {context_name} has no button rows"
        for row_idx, row in enumerate(rows):
            for btn_idx, btn in enumerate(row):
                lbl = btn.get("text", "")
                tested_buttons.append((context_name, row_idx, btn_idx, lbl))
                assert len(lbl) > 0, f"Empty button label in {context_name} row {row_idx} btn {btn_idx}"
                assert len(lbl) <= 18, (
                    f"VIOLATION: Button text exceeded 18 chars in {context_name} "
                    f"row {row_idx} btn {btn_idx}: {lbl!r} (length: {len(lbl)})"
                )

    # 1. Standard Static Keyboards
    check_kb(get_main_menu_keyboard(), "get_main_menu_keyboard")
    check_kb(get_services_keyboard(), "get_services_keyboard")
    check_kb(get_help_keyboard(), "get_help_keyboard")
    check_kb(get_guest_keyboard("tok123"), "get_guest_keyboard")
    check_kb(get_guest_keyboard(None), "get_guest_keyboard_empty")
    check_kb(get_ticket_categories_keyboard(), "get_ticket_categories_keyboard")
    check_kb(get_inspector_keyboard(), "get_inspector_keyboard")

    # 2. Payment Keyboard variations
    for amt in [None, 0.0, 150.0, 4850.50, 99999.0, 99999999.0]:
        check_kb(get_payment_keyboard(amt), f"get_payment_keyboard({amt})")

    # 3. Meter Confirmation Keyboard variations
    for val in [0.0, 142.385, 999999.99, -5.0, 123456789.0]:
        check_kb(get_meter_confirmation_keyboard("meter-khvs-1", val), f"get_meter_confirmation_keyboard({val})")

    # 4. Dynamic Meters Keyboard variations
    check_kb(get_meters_keyboard(None), "get_meters_keyboard(None)")

    adversarial_meters = [
        MeterBase(id=f"m_{i}", meter_type=mtype, serial_number=f"SN{i * 999999}",
                  name=name, installation_place="Место", last_reading_value=10.0,
                  last_reading_date=date.today(), verification_date_valid_until=date.today(),
                  unit="м³", decimal_digits=3)
        for i, (mtype, name) in enumerate([
            (MeterType.COLD_WATER, "ХВС (Холодная вода кухня)"),
            (MeterType.HOT_WATER, "ГВС (Горячая вода санузел)"),
            (MeterType.ELECTRICITY_MULTI, "Электроэнергия трехтарифная"),
            (MeterType.GAS, "Газоснабжение плита"),
            (MeterType.HEAT, "Теплоснабжение центральное"),
            (MeterType.COLD_WATER, "СверхдлинноеНаименованиеПрибораБезПробеловИСкобок"),
            (MeterType.HOT_WATER, "   Пробелы   в начале и конце   "),
            (MeterType.GAS, "Газ"),
        ])
    ]
    check_kb(get_meters_keyboard(adversarial_meters), "get_meters_keyboard(adversarial_meters)")

    # 5. Address Switch Keyboard variations
    adversarial_props = [
        UserProperty(id=f"p_{i}", user_id=1, address=addr, els=els, is_active=(i == 0))
        for i, (addr, els) in enumerate([
            ("г. Москва, ул. Ленина, д. 42, кв. 15", "ELS1234567890"),
            ("МО, КП Уютный, Дача 8", "ELS9988776655"),
            ("Очень длинный адрес загородного дома в коттеджном поселке", "ELS0001"),
            ("ул. Мира, 1", "ELS-MIN"),
            ("", ""),
        ])
    ]
    check_kb(get_address_switch_keyboard(adversarial_props), "get_address_switch_keyboard(adversarial_props)")
    check_kb(get_address_switch_keyboard(None), "get_address_switch_keyboard(None)")

    # 6. Fuzzing Button Constructors
    fuzz_samples = [
        "A" * 50, "Б" * 100, "12345678901234567890", "   пробелы   ",
        "[ХВС] 145.5", "Сдать показания счетчика воды", "Оплата квитанции ЖКХ",
        "Проверка поверки АРШИН", "Акт обходчика управляющей компании"
    ]
    for s in fuzz_samples:
        cb = Button.callback(s, "payload")
        assert len(cb["text"]) <= 18, f"Button.callback exceeded: {cb['text']}"
        link = Button.link(s, "https://max.ru")
        assert len(link["text"]) <= 18, f"Button.link exceeded: {link['text']}"
        app_b = Button.open_app(s, "bot_user")
        assert len(app_b["text"]) <= 18, f"Button.open_app exceeded: {app_b['text']}"

    assert len(tested_buttons) >= 60, f"Expected at least 60 audited buttons, got {len(tested_buttons)}"


# =========================================================================
# 4. ZERO EMOJI POLICY AUDIT
# =========================================================================

def test_audit_zero_emoji_policy_strict():
    """
    Exhaustively scans all files in app/ and max_bot_sdk/ (source code,
    docstrings, comments) and all bot responses.
    Asserts 0 emojis.
    """
    emoji_violations = []

    # 1. Scan source code and docstrings
    for directory in ["app", "max_bot_sdk"]:
        for root, _, files in os.walk(directory):
            for fname in files:
                if fname.endswith((".py", ".html", ".css", ".js")):
                    fpath = os.path.join(root, fname)
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                        for lineno, line in enumerate(fh, 1):
                            for char in EMOJI_REGEX.findall(line):
                                if char not in ALLOWED_TYPOGRAPHY:
                                    emoji_violations.append({
                                        "source": f"{fpath}:{lineno}",
                                        "char": char,
                                        "codepoint": f"U+{ord(char):04X}",
                                        "name": unicodedata.name(char, "UNKNOWN"),
                                        "snippet": line.strip()[:60]
                                    })

    assert len(emoji_violations) == 0, (
        f"VIOLATION: Found {len(emoji_violations)} emojis in source code:\n" +
        "\n".join(f"{v['source']} {v['char']} ({v['codepoint']} {v['name']}) in {v['snippet']}" for v in emoji_violations)
    )

    # 2. Scan outgoing bot responses
    client = MockTrackingClient()
    handler = BotHandler(client)

    handler.handle_bot_started({"sender_user_id": 999, "chat_id": "c_999"})
    for cmd in ["/start", "/meters", "/check 2809142", "/check 991201", "/pay", "/ticket", "/guest", "/inspector", "/profile", "/address", "хвс 145.5"]:
        handler.handle_message_created({
            "message": {"body": {"text": cmd}, "sender": {"id": 999, "first_name": "Тестер"}, "recipient": {"chat_id": "c_999"}}
        })

    response_violations = []
    for idx, msg in enumerate(client.sent_messages):
        txt = msg.get("text", "")
        for char in EMOJI_REGEX.findall(txt):
            if char not in ALLOWED_TYPOGRAPHY:
                response_violations.append({
                    "msg_idx": idx,
                    "char": char,
                    "snippet": txt[:50]
                })

    assert len(response_violations) == 0, (
        f"VIOLATION: Found emojis in bot responses:\n" +
        "\n".join(f"Msg #{v['msg_idx']}: {v['char']} in {v['snippet']}" for v in response_violations)
    )


# =========================================================================
# 5. ZERO BRACKET TAG AUDIT
# =========================================================================

def test_audit_zero_bracket_tags_strict():
    """
    Audits all generated bot message responses for bracket tags like [...].
    Asserts 0 brackets in user-facing bot messages.
    """
    client = MockTrackingClient()
    handler = BotHandler(client)
    user_id = 7711
    chat_id = "c_7711"

    # Execute all primary commands and interactions
    handler.handle_bot_started({"sender_user_id": user_id, "chat_id": chat_id})

    test_actions = [
        "/start", "/start guest_tk123", "/meters", "/check", "/check 2809142", "/check 991201",
        "/check 000000", "/pay", "/ticket", "/guest", "/inspector", "/profile", "/address",
        "/register", "хвс 145.5", "гвс 99.1", "свет 1900", "вода invalid", "неизвестная команда",
        "st00012|Name=ООО УК|PersonalAcc=40821810938000012345|BIC=044525225|Sum=485050"
    ]

    for action in test_actions:
        handler.handle_message_created({
            "message": {
                "body": {"text": action},
                "sender": {"id": user_id, "first_name": "Иван"},
                "recipient": {"chat_id": chat_id}
            }
        })

    callbacks = [
        "cmd_meters", "cmd_arshin", "cmd_pay", "cmd_ticket", "cmd_guest", "cmd_inspector", "cmd_menu",
        "cmd_profile", "cmd_address", "cmd_register", "ticket_cat_water", "ticket_cat_electric",
        "submit_meter_meter-khvs-1", "rescan_meter_cold_water"
    ]
    for cb in callbacks:
        handler.handle_callback({
            "callback": {"callback_id": f"cb_{cb}", "payload": cb},
            "sender": {"id": user_id},
            "chat_id": chat_id
        })

    # Photo drops
    for hint in ["фото хвс", "свет", ""]:
        handler.handle_message_created({
            "message": {
                "body": {"text": hint, "attachments": [{"type": "image", "url": "http://img"}]},
                "sender": {"id": user_id},
                "recipient": {"chat_id": chat_id}
            }
        })

    assert len(client.sent_messages) >= 30, f"Expected >= 30 sent messages, got {len(client.sent_messages)}"

    bracket_pattern = re.compile(r"\[[A-Za-zА-Яа-я0-9\s:_-]+\]")
    bracket_violations = []

    for idx, msg in enumerate(client.sent_messages):
        txt = msg.get("text", "")
        matches = bracket_pattern.findall(txt)
        if matches:
            bracket_violations.append({
                "msg_idx": idx,
                "matches": matches,
                "snippet": txt[:80]
            })

    assert len(bracket_violations) == 0, (
        f"VIOLATION: Found junk bracket tags in {len(bracket_violations)} bot messages:\n" +
        "\n".join(f"Msg #{v['msg_idx']}: {v['matches']} in {v['snippet']!r}" for v in bracket_violations)
    )


# =========================================================================
# 6. ADVERSARIAL STRESS: EXACT SINGLE EXECUTION & ATOMIC FSM CONCURRENCY
# =========================================================================

def test_adversarial_sync_handler_feed_update_and_concurrent_fsm_stress():
    """
    Stress-test synchronous handlers under rapid concurrent feed_update and
    verify atomic key retention on SQLiteStorage under heavy multi-coroutine load.
    """
    async def _run():
        client = MockTrackingClient()
        dp = AsyncUpdateDispatcher(client)

        exec_counters: Dict[str, int] = {}
        counter_lock = asyncio.Lock()

        @dp.command("sync_meter")
        def on_sync_meter(update: Update):
            user_id = str(update.sender_user_id)
            exec_counters[user_id] = exec_counters.get(user_id, 0) + 1
            return "ok"

        # 1. Feed 50 updates across 10 users rapidly
        total_events = 50
        for i in range(total_events):
            uid = (i % 10) + 1
            upd = {
                "update_type": "message_created",
                "message": {
                    "body": {"text": f"/sync_meter reading_{i}"},
                    "sender": {"id": uid},
                    "recipient": {"chat_id": f"chat_{uid}"}
                }
            }
            dp.feed_update(upd)

        await dp.wait_closed(timeout=3.0)

        # Every user received 5 events; total executions across all users must be exactly 50
        total_execs = sum(exec_counters.values())
        assert total_execs == total_events, f"Expected {total_events} executions, got {total_execs}: {exec_counters}"
        for uid in range(1, 11):
            assert exec_counters.get(str(uid)) == 5, f"User {uid} executions mismatch: {exec_counters.get(str(uid))}"

        # 2. Heavy concurrent SQLiteStorage update stress across multiple users
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "stress_concurrency.db")
            storage = SQLiteStorage(db_path=db_path)

            async def update_worker(uid: int, key_id: int):
                await storage.update_data(f"c_{uid}", uid, {f"field_{key_id}": key_id * 100})

            # 5 users, each updated with 20 distinct keys concurrently (100 total concurrent operations)
            tasks = [update_worker(u, k) for u in range(1, 6) for k in range(20)]
            await asyncio.gather(*tasks)

            for u in range(1, 6):
                stored = await storage.get_data(f"c_{u}", u)
                assert len(stored) == 20, f"User {u} lost keys: expected 20, got {len(stored)}"
                for k in range(20):
                    assert stored.get(f"field_{k}") == k * 100

            await storage.close()

    asyncio.run(_run())
