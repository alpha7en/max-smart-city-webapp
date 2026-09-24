"""
FSM Context for managing user state and conversational data.
Zero emoji policy strictly enforced.
"""

from typing import Optional, Dict, Any, Union, Callable
from enum import Enum

from max_bot_sdk.fsm.storage import BaseStorage
from max_bot_sdk.fsm.state import State, UserState


class FSMResult:
    """
    Dual sync/async result wrapper for FSM operations.
    Can be evaluated directly in synchronous code or awaited in coroutines.
    """
    def __init__(self, value: Any, coro_factory: Optional[Callable] = None):
        self._value = value
        self._coro_factory = coro_factory

    def __bool__(self) -> bool:
        return bool(self._value)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, FSMResult):
            return self._value == other._value
        if isinstance(other, Enum):
            return self._value == other.value
        if isinstance(other, State):
            return self._value == other.name
        return self._value == other

    def __str__(self) -> str:
        return str(self._value)

    def __repr__(self) -> str:
        return f"<FSMResult value={self._value!r}>"

    def __getitem__(self, item: Any) -> Any:
        if isinstance(self._value, dict):
            return self._value[item]
        raise TypeError(f"FSMResult value is not subscriptable: {type(self._value)}")

    def get(self, key: Any, default: Any = None) -> Any:
        if isinstance(self._value, dict):
            return self._value.get(key, default)
        return default

    def __await__(self):
        if self._coro_factory is not None:
            return self._coro_factory().__await__()
        async def _ret():
            return self._value
        return _ret().__await__()


class FSMContext:
    """
    Context manager for finite state machine bound to a specific chat and user.
    """

    def __init__(
        self,
        storage: BaseStorage,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]]
    ):
        self.storage = storage
        self.chat_id = chat_id
        self.user_id = user_id

    @property
    def current_state(self) -> Optional[str]:
        """Convenience property for synchronous access to current state."""
        return self.storage.sync_get_state(self.chat_id, self.user_id)

    @property
    def current_data(self) -> Dict[str, Any]:
        """Convenience property for synchronous access to current data."""
        return self.storage.sync_get_data(self.chat_id, self.user_id)

    def _to_raw_state(self, state: Optional[Union[UserState, State, str]]) -> Optional[str]:
        if state is None:
            return None
        if isinstance(state, Enum):
            return state.value
        if isinstance(state, State):
            return state.name
        return str(state)

    def get_state(self) -> FSMResult:
        """Retrieve current state string or None if idle."""
        try:
            val = self.storage.sync_get_state(self.chat_id, self.user_id)
            has_sync = True
        except (NotImplementedError, Exception):
            val = None
            has_sync = False

        async def _async_get():
            if has_sync:
                return val
            return await self.storage.get_state(self.chat_id, self.user_id)
        return FSMResult(val, coro_factory=_async_get)

    def set_state(self, state: Optional[Union[UserState, State, str]] = None) -> FSMResult:
        """Set new state. Pass None to reset to idle."""
        raw_state = self._to_raw_state(state)
        try:
            self.storage.sync_set_state(self.chat_id, self.user_id, raw_state)
            has_sync = True
        except (NotImplementedError, Exception):
            has_sync = False

        async def _async_set():
            if not has_sync:
                await self.storage.set_state(self.chat_id, self.user_id, raw_state)
            return None
        return FSMResult(None, coro_factory=_async_set)

    def get_data(self) -> FSMResult:
        """Retrieve stored conversational payload."""
        try:
            val = self.storage.sync_get_data(self.chat_id, self.user_id)
            has_sync = True
        except (NotImplementedError, Exception):
            val = {}
            has_sync = False

        async def _async_get():
            if has_sync:
                return val
            return await self.storage.get_data(self.chat_id, self.user_id)
        return FSMResult(val, coro_factory=_async_get)

    def set_data(self, data: Optional[Dict[str, Any]] = None, **kwargs) -> FSMResult:
        """Overwrite stored payload."""
        payload = dict(data or {})
        payload.update(kwargs)
        try:
            self.storage.sync_set_data(self.chat_id, self.user_id, payload)
            has_sync = True
        except (NotImplementedError, Exception):
            has_sync = False

        async def _async_set():
            if not has_sync:
                await self.storage.set_data(self.chat_id, self.user_id, payload)
            return None
        return FSMResult(None, coro_factory=_async_set)

    def update_data(self, data: Optional[Dict[str, Any]] = None, **kwargs) -> FSMResult:
        """Merge key-value pairs into existing conversational payload."""
        payload = dict(data or {})
        payload.update(kwargs)
        try:
            val = self.storage.sync_update_data(self.chat_id, self.user_id, payload)
            has_sync = True
        except (NotImplementedError, Exception):
            val = {}
            has_sync = False

        async def _async_upd():
            if has_sync:
                return val
            return await self.storage.update_data(self.chat_id, self.user_id, payload)
        return FSMResult(val, coro_factory=_async_upd)

    def clear(self) -> FSMResult:
        """Clear both state and stored data."""
        try:
            self.storage.sync_clear(self.chat_id, self.user_id)
            has_sync = True
        except (NotImplementedError, Exception):
            has_sync = False

        async def _async_clear():
            if not has_sync:
                await self.storage.clear(self.chat_id, self.user_id)
            return None
        return FSMResult(None, coro_factory=_async_clear)

    def reset_state(self, with_data: bool = False) -> FSMResult:
        """Reset state to None, optionally clearing stored data."""
        if with_data:
            return self.clear()
        return self.set_state(None)
