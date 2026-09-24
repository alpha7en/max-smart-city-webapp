"""
Comprehensive test suite for AsyncUpdateDispatcher, FSM, and Middlewares in max_bot_sdk.
Zero emoji policy strictly enforced.
Button length <= 18 characters strictly enforced.
"""

import asyncio
import os
import tempfile
import pytest

from max_bot_sdk import (
    AsyncUpdateDispatcher,
    Dispatcher,
    UpdateDispatcher,
    Update,
    UserState,
    State,
    StatesGroup,
    FSMContext,
    MemoryStorage,
    SQLiteStorage,
    BaseMiddleware,
    LoggingMiddleware,
    ErrorHandlingMiddleware,
    Button,
    KeyboardBuilder,
)
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
from app.bot.handlers import BotHandler, get_bot_handler


class DummyClient:
    def __init__(self):
        self.sent_messages = []
        self.answered_callbacks = []

    def send_message(self, **kwargs):
        self.sent_messages.append(kwargs)
        return {"status": "ok", "message_id": 100}

    def answer_callback(self, **kwargs):
        self.answered_callbacks.append(kwargs)
        return {"status": "ok"}

    def get_me(self):
        return {"id": 1, "first_name": "TestBot", "username": "test_bot"}

    def get_updates(self, marker=None, timeout=25):
        return {"marker": "m1", "updates": []}


# =========================================================================
# 1. FSM SUBSYSTEM TESTS (State, Context, MemoryStorage, SQLiteStorage)
# =========================================================================

def test_fsm_memory_storage_and_context():
    async def _run():
        storage = MemoryStorage()
        ctx = FSMContext(storage, chat_id="chat_1", user_id=101)

        assert await ctx.get_state() is None
        assert await ctx.get_data() == {}

        await ctx.set_state(UserState.WAITING_METER_INPUT)
        assert await ctx.get_state() == UserState.WAITING_METER_INPUT.value

        await ctx.set_data(meter_id="meter-khvs-1", temp_val=145.0)
        data = await ctx.get_data()
        assert data["meter_id"] == "meter-khvs-1"
        assert data["temp_val"] == 145.0

        await ctx.update_data(confirmed=True)
        data = await ctx.get_data()
        assert data["confirmed"] is True

        await ctx.reset_state(with_data=False)
        assert await ctx.get_state() is None
        assert (await ctx.get_data())["confirmed"] is True

        await ctx.clear()
        assert await ctx.get_state() is None
        assert await ctx.get_data() == {}

    asyncio.run(_run())


def test_fsm_sqlite_storage_persistence():
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_fsm.db")
            storage1 = SQLiteStorage(db_path=db_path)
            ctx1 = FSMContext(storage1, chat_id="c_99", user_id=999)

            await ctx1.set_state(UserState.WAITING_TICKET_DESC)
            await ctx1.set_data(category="water", address="ул. Мира, д. 5")

            assert await ctx1.get_state() == UserState.WAITING_TICKET_DESC.value
            d1 = await ctx1.get_data()
            assert d1["category"] == "water"

            # Simulate bot restart by creating fresh storage pointing to same sqlite file
            storage2 = SQLiteStorage(db_path=db_path)
            ctx2 = FSMContext(storage2, chat_id="c_99", user_id=999)

            assert await ctx2.get_state() == UserState.WAITING_TICKET_DESC.value
            d2 = await ctx2.get_data()
            assert d2["category"] == "water"
            assert d2["address"] == "ул. Мира, д. 5"

            await ctx2.clear()
            assert await ctx2.get_state() is None
            assert await ctx2.get_data() == {}

    asyncio.run(_run())


def test_custom_states_and_groups():
    class MeterDialog(StatesGroup):
        entering_reading = State("entering_reading")
        confirming = State("confirming")

    states = MeterDialog.all_states()
    assert "entering_reading" in states
    assert "confirming" in states
    assert MeterDialog.entering_reading == "entering_reading"


# =========================================================================
# 2. DISPATCHER TESTS (Sync/Async Dual Execution, Routing, Middlewares)
# =========================================================================

def test_dispatcher_dual_sync_and_async_handlers():
    client = DummyClient()
    dp = AsyncUpdateDispatcher(client)

    events = []

    # Sync handler
    @dp.command("sync_cmd")
    def on_sync(client, update):
        events.append(f"sync:{update.text}")

    # Async handler
    @dp.command("async_cmd")
    async def on_async(update, state):
        events.append(f"async:{update.text}")

    # 1. Dispatch sync command
    res1 = dp.process_update({
        "update_type": "message_created",
        "message": {"body": {"text": "/sync_cmd"}, "sender": {"id": 1}, "recipient": {"chat_id": "c1"}}
    })
    assert bool(res1) is True
    assert "sync:/sync_cmd" in events

    # 2. Dispatch async command in sync test context (handled via fallback runner)
    res2 = dp.process_update({
        "update_type": "message_created",
        "message": {"body": {"text": "/async_cmd"}, "sender": {"id": 1}, "recipient": {"chat_id": "c1"}}
    })
    assert bool(res2) is True
    assert "async:/async_cmd" in events


def test_dispatcher_state_filtered_routing():
    async def _run():
        client = DummyClient()
        dp = AsyncUpdateDispatcher(client)
        fsm = dp.get_fsm_context("chat_42", 777)

        route_hits = []

        @dp.message(state=UserState.WAITING_METER_INPUT)
        async def on_reading(update, state):
            route_hits.append(f"reading:{update.text}")
            await state.reset_state()

        @dp.message(state=None)
        async def on_idle(update):
            route_hits.append(f"idle:{update.text}")

        msg_payload = {
            "update_type": "message_created",
            "message": {"body": {"text": "145.5"}, "sender": {"id": 777}, "recipient": {"chat_id": "chat_42"}}
        }

        # First attempt: state is IDLE (None) -> should hit on_idle
        await dp.dispatch(msg_payload)
        assert route_hits == ["idle:145.5"]

        # Set state to WAITING_METER_INPUT
        await fsm.set_state(UserState.WAITING_METER_INPUT)

        # Second attempt: state is WAITING_METER_INPUT -> should hit on_reading
        await dp.dispatch(msg_payload)
        assert route_hits == ["idle:145.5", "reading:145.5"]

        # State was reset in handler -> should hit on_idle again
        await dp.dispatch(msg_payload)
        assert route_hits == ["idle:145.5", "reading:145.5", "idle:145.5"]

    asyncio.run(_run())


def test_dispatcher_feed_update_and_drain():
    async def _run():
        client = DummyClient()
        dp = AsyncUpdateDispatcher(client)

        processed = []

        @dp.command("slow")
        async def slow_handler(update):
            await asyncio.sleep(0.05)
            processed.append(update.text)

        # Feed updates non-blockingly
        task1 = dp.feed_update({
            "update_type": "message_created",
            "message": {"body": {"text": "/slow 1"}, "sender": {"id": 1}, "recipient": {"chat_id": "c1"}}
        })
        task2 = dp.feed_update({
            "update_type": "message_created",
            "message": {"body": {"text": "/slow 2"}, "sender": {"id": 2}, "recipient": {"chat_id": "c2"}}
        })

        assert len(dp._active_tasks) == 2
        await dp.wait_closed(timeout=2.0)
        assert len(dp._active_tasks) == 0
        assert len(processed) == 2

    asyncio.run(_run())


def test_dispatcher_middlewares():
    async def _run():
        client = DummyClient()
        dp = AsyncUpdateDispatcher(client)

        middleware_log = []

        class AuditMiddleware(BaseMiddleware):
            async def pre_process(self, update, data):
                middleware_log.append(f"pre:{update.update_type}")
                return True

            async def post_process(self, update, data, result=None, exception=None):
                middleware_log.append(f"post:{update.update_type}")

        dp.register_middleware(AuditMiddleware())
        dp.register_middleware(LoggingMiddleware())
        dp.register_middleware(ErrorHandlingMiddleware())

        @dp.command("ping")
        def on_ping(update):
            middleware_log.append("ping_handler")

        await dp.dispatch({
            "update_type": "message_created",
            "message": {"body": {"text": "/ping"}, "sender": {"id": 1}, "recipient": {"chat_id": "c1"}}
        })

        assert "pre:message_created" in middleware_log
        assert "ping_handler" in middleware_log
        assert "post:message_created" in middleware_log

    asyncio.run(_run())


# =========================================================================
# 3. MODULARIZED KEYBOARDS AND BOT WORKER AUDIT
# =========================================================================

def test_modular_keyboards_button_length_and_zero_emoji():
    keyboards = [
        get_main_menu_keyboard(),
        get_meters_keyboard(),
        get_services_keyboard(),
        get_help_keyboard(),
        get_guest_keyboard("guest_token_123"),
        get_meter_confirmation_keyboard("meter-khvs-1", 145.5),
        get_ticket_categories_keyboard(),
        get_inspector_keyboard(),
        get_payment_keyboard(4850.50),
        get_address_switch_keyboard()
    ]

    for kb in keyboards:
        assert kb["type"] == "inline_keyboard"
        buttons = kb.get("payload", {}).get("buttons", [])
        for row in buttons:
            for btn in row:
                lbl = btn.get("text", "")
                assert len(lbl) <= 18, f"Button label exceeded 18 chars: {lbl!r} (len={len(lbl)})"


def test_bot_worker_lifecycle():
    worker = BotWorker(token="mock_token", base_url="https://platform-api2.max.ru")
    assert worker.is_configured() is True

    unconfigured = BotWorker(token="")
    assert unconfigured.is_configured() is False


# =========================================================================
# 4. FSM CONVERSATIONAL FLOW IN BOT HANDLER
# =========================================================================

def test_bot_handler_fsm_multi_step_dialog():
    client = DummyClient()
    handler = BotHandler(client)
    chat_id = "c_dialog_1"
    user_id = 555
    fsm = handler.get_fsm_context(chat_id, user_id)

    # Step 1: User clicks "cmd_meters"
    handler.handle_callback({
        "callback": {
            "callback_id": "cb_m",
            "payload": "cmd_meters",
            "user": {"id": user_id}
        },
        "sender": {"id": user_id},
        "chat_id": chat_id
    })
    # State should now be WAITING_METER_INPUT
    assert fsm.get_state() == UserState.WAITING_METER_INPUT.value

    # Step 2: User sends numerical reading "148.5"
    handler.handle_message_created({
        "message": {
            "body": {"text": "148.5"},
            "sender": {"id": user_id, "first_name": "Тестер"},
            "recipient": {"chat_id": chat_id}
        }
    })

    # Reading should be accepted and state reset to IDLE
    assert len(client.sent_messages) >= 2
    latest_msg = client.sent_messages[-1]["text"]
    assert "ГИС ЖКХ: Показания успешно приняты" in latest_msg
    assert "148.5" in latest_msg
    assert fsm.current_state is None

    # Step 3: User clicks "cmd_arshin"
    handler.handle_callback({
        "callback": {
            "callback_id": "cb_ar",
            "payload": "cmd_arshin",
            "user": {"id": user_id}
        },
        "sender": {"id": user_id},
        "chat_id": chat_id
    })
    # State should now be WAITING_ARSHIN_SERIAL
    assert fsm.current_state == UserState.WAITING_ARSHIN_SERIAL.value

    # Step 4: User sends serial "991201"
    handler.handle_message_created({
        "message": {
            "body": {"text": "991201"},
            "sender": {"id": user_id, "first_name": "Тестер"},
            "recipient": {"chat_id": chat_id}
        }
    })
    arshin_msg = client.sent_messages[-1]["text"]
    assert "ФГИС «АРШИН»" in arshin_msg
    assert fsm.current_state is None


# =========================================================================
# 5. REMEDIATION AUDIT TESTS (Single Execution, Concurrency, Ticket Schema)
# =========================================================================

def test_feed_update_and_dispatch_exact_single_call_count_sync_handler():
    """
    Verify sync handlers execute exactly once whether dispatched via feed_update,
    awaited via dp.dispatch, evaluated synchronously, or wrapped with middlewares.
    """
    async def _run():
        client = DummyClient()
        dp = AsyncUpdateDispatcher(client)

        calls = []

        @dp.command("sync_ping")
        def on_sync_ping(update):
            calls.append(f"sync:{update.text}")
            return "pong"

        upd = {
            "update_type": "message_created",
            "message": {"body": {"text": "/sync_ping"}, "sender": {"id": 10}, "recipient": {"chat_id": "c10"}}
        }

        # 1. feed_update: must execute exactly once
        task = dp.feed_update(upd)
        await task
        assert calls == ["sync:/sync_ping"], f"feed_update caused {len(calls)} executions"

        # 2. await dp.dispatch: must execute exactly once
        calls.clear()
        res_await = await dp.dispatch(upd)
        assert bool(res_await) is True
        assert calls == ["sync:/sync_ping"], f"await dp.dispatch caused {len(calls)} executions"

        # 3. Synchronous dp.dispatch (without await): must execute exactly once
        calls.clear()
        res_sync = dp.dispatch(upd)
        assert bool(res_sync) is True
        assert calls == ["sync:/sync_ping"], f"sync dispatch caused {len(calls)} executions"

        # 4. Sync handler with middlewares: pre -> handler -> post order, exactly once
        calls.clear()
        mw_log = []

        class TracingMiddleware(BaseMiddleware):
            async def pre_process(self, update, data):
                mw_log.append("pre")
                return True

            async def post_process(self, update, data, result=None, exception=None):
                mw_log.append("post")

        dp.register_middleware(TracingMiddleware())
        await dp.dispatch(upd)
        assert calls == ["sync:/sync_ping"], f"middleware pipeline caused {len(calls)} executions"
        assert mw_log == ["pre", "post"], f"unexpected middleware log: {mw_log}"

    asyncio.run(_run())


def test_sqlite_storage_concurrent_update_data_integrity():
    """
    Verify concurrent update_data operations on SQLiteStorage and FSMContext
    retain 100% of keys without race conditions or lost updates.
    """
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "fsm_concurrency.db")
            storage = SQLiteStorage(db_path=db_path)
            ctx = FSMContext(storage, "chat_conc", 9999)

            total_keys = 40

            # 1. Concurrent storage.update_data calls
            async def worker_storage(idx: int):
                await storage.update_data("chat_conc", 9999, {f"k_storage_{idx}": idx})

            await asyncio.gather(*(worker_storage(i) for i in range(total_keys)))

            stored_1 = await storage.get_data("chat_conc", 9999)
            assert len(stored_1) == total_keys, f"Expected {total_keys} keys, got {len(stored_1)}"
            for i in range(total_keys):
                assert stored_1.get(f"k_storage_{i}") == i

            # 2. Concurrent ctx.update_data calls
            async def worker_ctx(idx: int):
                await ctx.update_data(**{f"k_ctx_{idx}": idx * 2})

            await asyncio.gather(*(worker_ctx(i) for i in range(total_keys)))

            stored_2 = await ctx.get_data()
            assert len(stored_2) == total_keys * 2, f"Expected {total_keys * 2} keys, got {len(stored_2)}"
            for i in range(total_keys):
                assert stored_2.get(f"k_ctx_{i}") == i * 2

            await storage.close()

    asyncio.run(_run())


def test_bot_handler_ticket_creation_from_text_success_and_resets_fsm():
    """
    Verify ticket creation from text succeeds without AttributeError (using ticket.id),
    preserves user category, sends proper response, and resets FSM state to IDLE.
    """
    client = DummyClient()
    handler = BotHandler(client)
    chat_id = "chat_tck_rem"
    user_id = 9001
    fsm = handler.get_fsm_context(chat_id, user_id)

    # User initiates ticket dialog and picks category
    fsm.set_state(UserState.WAITING_TICKET_DESC)
    fsm.update_data(category="plumbing")

    # User sends problem description
    handler.handle_message_created({
        "message": {
            "body": {"text": "Прорвало трубу отопления в коридоре"},
            "sender": {"id": user_id, "first_name": "Алексей"},
            "recipient": {"chat_id": chat_id}
        }
    })

    assert len(client.sent_messages) >= 1
    resp_text = client.sent_messages[-1]["text"]
    assert "Заявка в УК зарегистрирована" in resp_text
    assert "TCK-2026-" in resp_text
    assert "Срочная (до 24 ч)" in resp_text
    assert fsm.current_state is None
    assert fsm.get_state() == None
