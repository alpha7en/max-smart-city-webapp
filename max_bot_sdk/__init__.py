"""
MAX Bot SDK — Python SDK for building chatbots on the MAX platform (platform-api2.max.ru).
Zero emoji policy strictly enforced.
"""

from max_bot_sdk.client import MaxBotClient
from max_bot_sdk.keyboards import Button, KeyboardBuilder
from max_bot_sdk.dispatcher import AsyncUpdateDispatcher, UpdateDispatcher, Dispatcher
from max_bot_sdk.models import Update, User, Message, Callback
from max_bot_sdk.fsm import (
    UserState,
    State,
    StatesGroup,
    FSMContext,
    BaseStorage,
    MemoryStorage,
    SQLiteStorage,
)
from max_bot_sdk.middlewares import (
    BaseMiddleware,
    LoggingMiddleware,
    ErrorHandlingMiddleware,
)

__version__ = "2.0.0"
__all__ = [
    "MaxBotClient",
    "Button",
    "KeyboardBuilder",
    "AsyncUpdateDispatcher",
    "UpdateDispatcher",
    "Dispatcher",
    "Update",
    "User",
    "Message",
    "Callback",
    "UserState",
    "State",
    "StatesGroup",
    "FSMContext",
    "BaseStorage",
    "MemoryStorage",
    "SQLiteStorage",
    "BaseMiddleware",
    "LoggingMiddleware",
    "ErrorHandlingMiddleware",
]
