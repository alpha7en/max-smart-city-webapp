"""
Comprehensive test suite for the standalone max_bot_sdk package.
Tests:
1. Typed Data Models (Update, Message, Callback, User, Recipient)
2. Keyboard Builder (inline buttons, 28 char truncation, URL sanitization)
3. Dispatcher Routing (commands, callbacks, photos, fallback)
4. Client Request Construction & Error Handling
"""

import pytest
from max_bot_sdk.models import Update, Message, Callback, User, Recipient, MessageBody
from max_bot_sdk.keyboards import Button, KeyboardBuilder
from max_bot_sdk.dispatcher import Dispatcher
from max_bot_sdk.client import MaxBotClient

def test_models_update_parsing():
    # 1. message_created update
    raw_msg = {
        "update_type": "message_created",
        "message": {
            "body": {
                "text": "Тестовое сообщение",
                "attachments": [{"type": "image", "url": "https://max.ru/img.jpg"}]
            },
            "sender": {"id": 1001, "first_name": "Алексей"},
            "recipient": {"chat_id": "chat_42"}
        }
    }
    upd = Update.from_dict(raw_msg)
    assert upd.update_type == "message_created"
    assert upd.sender_user_id == 1001
    assert upd.effective_chat_id == "chat_42"
    assert upd.text == "Тестовое сообщение"
    assert upd.has_image is True

    # 2. message_callback update
    raw_cb = {
        "update_type": "message_callback",
        "callback": {
            "callback_id": "cb_999",
            "payload": "cmd_meters",
            "user": {"id": 2002, "first_name": "Елена"}
        },
        "message": {
            "recipient": {"chat_id": "chat_88"}
        }
    }
    upd_cb = Update.from_dict(raw_cb)
    assert upd_cb.update_type == "message_callback"
    assert upd_cb.callback.callback_id == "cb_999"
    assert upd_cb.callback.payload == "cmd_meters"
    assert upd_cb.sender_user_id == 2002
    assert upd_cb.effective_chat_id == "chat_88"

    # 3. bot_started update
    raw_start = {
        "update_type": "bot_started",
        "user": {"id": 3003, "first_name": "Иван"},
        "chat_id": "chat_direct"
    }
    upd_start = Update.from_dict(raw_start)
    assert upd_start.update_type == "bot_started"
    assert upd_start.sender_user_id == 3003
    assert upd_start.effective_chat_id == "chat_direct"

def test_keyboard_builder_truncation_and_sanitization():
    # Button text exceeding 28 characters must be automatically truncated
    long_text = "Очень длинный текст для кнопки на мобильном экране смартфона"
    btn = Button.callback(long_text, "payload_1")
    assert len(btn["text"]) <= 28
    assert btn["text"].endswith("…")

    # URL sanitization: localhost / 127.0.0.1 must be replaced with https://max.ru
    btn_local = Button.link("Локальная ссылка", "http://localhost:8000/app")
    assert btn_local["url"] == "https://max.ru"

    btn_127 = Button.link("127.0.0.1 ссылка", "http://127.0.0.1:3000/app")
    assert btn_127["url"] == "https://max.ru"

    btn_valid = Button.link("Правильная ссылка", "https://smart.max.ru/app")
    assert btn_valid["url"] == "https://smart.max.ru/app"

    # Build full inline keyboard
    kb = KeyboardBuilder.inline([
        [Button.open_app("Mini App", "https://max.ru/app")],
        [Button.callback("Кнопка 1", "cb1"), Button.callback("Кнопка 2", "cb2")]
    ])
    assert kb["type"] == "inline_keyboard"
    assert len(kb["payload"]["buttons"]) == 2
    assert len(kb["payload"]["buttons"][1]) == 2

def test_dispatcher_routing():
    class DummyClient:
        def __init__(self):
            self.sent = []
            self.answered = []
        def send_message(self, **kwargs):
            self.sent.append(kwargs)
            return {"status": "ok"}
        def answer_callback(self, **kwargs):
            self.answered.append(kwargs)
            return {"status": "ok"}

    client = DummyClient()
    dp = Dispatcher(client)

    received_commands = []
    received_callbacks = []
    received_photos = []

    @dp.command("/start")
    def on_start(client, update):
        received_commands.append(update.text)

    @dp.callback("test_cb")
    def on_cb(client, update):
        received_callbacks.append(update.callback.payload)

    @dp.on_photo
    def on_photo(client, update):
        received_photos.append(update.sender_user_id)

    # 1. Process command
    dp.process_update({
        "update_type": "message_created",
        "message": {
            "body": {"text": "/start"},
            "sender": {"id": 10},
            "recipient": {"chat_id": "c10"}
        }
    })
    assert received_commands == ["/start"]

    # 2. Process callback
    dp.process_update({
        "update_type": "message_callback",
        "callback": {
            "callback_id": "cb_1",
            "payload": "test_cb"
        },
        "sender": {"id": 20},
        "chat_id": "c20"
    })
    assert received_callbacks == ["test_cb"]

    # 3. Process photo
    dp.process_update({
        "update_type": "message_created",
        "message": {
            "body": {"text": "", "attachments": [{"type": "photo", "url": "https://img"}]},
            "sender": {"id": 30},
            "recipient": {"chat_id": "c30"}
        }
    })
    assert received_photos == [30]

def test_client_initialization_and_validation():
    # Must reject empty token
    with pytest.raises(ValueError):
        MaxBotClient("")

    client = MaxBotClient("mock_token_123")
    assert client.token == "mock_token_123"
    assert client.base_url == "https://platform-api2.max.ru"
