"""
Asynchronous Update Dispatcher for MAX Bot SDK.
Supports dual sync and async handler execution, FSM state filtering,
middleware pipelines, non-blocking feed_update task scheduling, and graceful drain.
Zero emoji policy strictly enforced.
"""

import asyncio
import inspect
import logging
import re
from typing import Callable, List, Optional, Any, Dict, Union, Set, Pattern, Type

from max_bot_sdk.models import Update, Message, Callback
from max_bot_sdk.fsm.storage import BaseStorage, MemoryStorage
from max_bot_sdk.fsm.context import FSMContext
from max_bot_sdk.fsm.state import UserState, State
from max_bot_sdk.middlewares.base import BaseMiddleware

logger = logging.getLogger("max_bot_sdk.dispatcher")


class AsyncResult:
    """
    Dual-execution wrapper that can be both evaluated synchronously
    and awaited asynchronously in coroutines.
    """
    def __init__(self, value: Any, coro: Optional[Any] = None, coro_factory: Optional[Callable] = None):
        self._value = value
        self._coro = coro
        self._coro_factory = coro_factory

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return f"<AsyncResult value={self._value!r}>"

    def __await__(self):
        if self._coro_factory is not None:
            return self._coro_factory().__await__()
        if self._coro is not None:
            return self._coro.__await__()
        async def _ret():
            return self._value
        return _ret().__await__()


class HandlerRoute:
    """Encapsulates a registered route with filters and metadata."""
    def __init__(
        self,
        handler: Callable,
        state: Optional[Any] = None,
        update_types: Optional[List[str]] = None,
        commands: Optional[List[str]] = None,
        callback_pattern: Optional[Union[str, Pattern]] = None,
        text_pattern: Optional[Union[str, Pattern]] = None,
        has_image: Optional[bool] = None,
        is_fallback: bool = False
    ):
        self.handler = handler
        self.state = state
        self.update_types = update_types or []
        self.commands = [c.lstrip("/").lower() for c in commands] if commands else []
        self.callback_pattern = callback_pattern
        self.text_pattern = text_pattern
        self.has_image = has_image
        self.is_fallback = is_fallback

    def matches_state(self, current_state: Optional[str]) -> bool:
        if self.state == "*":
            return True
        if self.state is None:
            return current_state is None or current_state == "idle" or current_state == UserState.IDLE.value
        if isinstance(self.state, (list, tuple, set)):
            allowed = set()
            for s in self.state:
                val = s.value if hasattr(s, "value") else (s.name if hasattr(s, "name") else str(s))
                allowed.add(val)
            return current_state in allowed
        expected = self.state.value if hasattr(self.state, "value") else (self.state.name if hasattr(self.state, "name") else str(self.state))
        return current_state == expected


class AsyncUpdateDispatcher:
    """
    Asynchronous event dispatcher and router for MAX Bot API.
    Features:
    - Dual synchronous/asynchronous handler execution
    - Finite State Machine (FSM) routing filters
    - Interceptor middleware pipeline
    - Non-blocking update dispatching via feed_update()
    - Graceful shutdown task drain via wait_closed()
    """

    def __init__(
        self,
        client: Optional[Any] = None,
        storage: Optional[BaseStorage] = None
    ):
        self.client = client
        self.storage: BaseStorage = storage or MemoryStorage()
        self._routes: List[HandlerRoute] = []
        self._fallback_route: Optional[HandlerRoute] = None
        self._middlewares: List[BaseMiddleware] = []
        self._active_tasks: Set[asyncio.Task] = set()
        self._error_handlers: Dict[Type[Exception], Callable] = {}

    # -------------------------------------------------------------------------
    # Middleware registration
    # -------------------------------------------------------------------------

    def register_middleware(self, middleware: BaseMiddleware) -> None:
        """Register an interceptor middleware."""
        self._middlewares.append(middleware)

    # -------------------------------------------------------------------------
    # Decorators & Route Registration
    # -------------------------------------------------------------------------

    def message(
        self,
        state: Optional[Any] = None,
        commands: Optional[List[str]] = None,
        has_image: Optional[bool] = None,
        text_pattern: Optional[Union[str, Pattern]] = None
    ):
        """Decorator for general message handling."""
        def decorator(func: Callable):
            route = HandlerRoute(
                handler=func,
                state=state,
                update_types=["message_created"],
                commands=commands,
                has_image=has_image,
                text_pattern=text_pattern
            )
            self._routes.append(route)
            return func
        return decorator

    def command(self, cmd_name: Union[str, List[str]], state: Optional[Any] = None):
        """Decorator for command messages (/start, /meters, etc.)."""
        cmd_list = [cmd_name] if isinstance(cmd_name, str) else list(cmd_name)
        def decorator(func: Callable):
            route = HandlerRoute(
                handler=func,
                state=state,
                update_types=["message_created", "bot_started"],
                commands=cmd_list
            )
            self._routes.append(route)
            return func
        return decorator

    def callback(self, pattern: Union[str, Pattern], state: Optional[Any] = None):
        """Decorator for inline keyboard callbacks."""
        def decorator(func: Callable):
            route = HandlerRoute(
                handler=func,
                state=state,
                update_types=["message_callback"],
                callback_pattern=pattern
            )
            self._routes.append(route)
            return func
        return decorator

    def on_photo(self, func_or_state: Any = None, state: Optional[Any] = None):
        """Decorator for photo attachments."""
        if callable(func_or_state) and state is None:
            route = HandlerRoute(
                handler=func_or_state,
                state=None,
                update_types=["message_created"],
                has_image=True
            )
            self._routes.append(route)
            return func_or_state

        target_state = func_or_state
        def decorator(func: Callable):
            route = HandlerRoute(
                handler=func,
                state=target_state,
                update_types=["message_created"],
                has_image=True
            )
            self._routes.append(route)
            return func
        return decorator

    def on_text(self, pattern: Optional[Union[str, Pattern]] = None, state: Optional[Any] = None):
        """Decorator for plain text messages."""
        if callable(pattern) and state is None:
            route = HandlerRoute(
                handler=pattern,
                state=None,
                update_types=["message_created"],
                text_pattern=None
            )
            self._routes.append(route)
            return pattern

        def decorator(func: Callable):
            route = HandlerRoute(
                handler=func,
                state=state,
                update_types=["message_created"],
                text_pattern=pattern
            )
            self._routes.append(route)
            return func
        return decorator

    def fallback(self, func_or_state: Any = None, state: Optional[Any] = "*"):
        """Decorator for fallback handlers."""
        if callable(func_or_state):
            route = HandlerRoute(
                handler=func_or_state,
                state="*",
                is_fallback=True
            )
            self._fallback_route = route
            return func_or_state

        target_state = func_or_state or state
        def decorator(func: Callable):
            route = HandlerRoute(
                handler=func,
                state=target_state,
                is_fallback=True
            )
            self._fallback_route = route
            return func
        return decorator

    def error(self, exc_type: Type[Exception] = Exception):
        """Decorator for handling exceptions of a specific type."""
        def decorator(func: Callable):
            self._error_handlers[exc_type] = func
            return func
        return decorator

    # Legacy programmatic aliases
    def register_message_handler(self, handler: Callable, **kwargs):
        return self.message(**kwargs)(handler)

    def register_command_handler(self, cmd_name: Union[str, List[str]], handler: Callable, state: Optional[Any] = None):
        return self.command(cmd_name=cmd_name, state=state)(handler)

    def register_callback_handler(self, pattern: Union[str, Pattern], handler: Callable, state: Optional[Any] = None):
        return self.callback(pattern=pattern, state=state)(handler)

    def register_photo_handler(self, handler: Callable, state: Optional[Any] = None):
        return self.on_photo(state=state)(handler)

    def register_text_handler(self, pattern: Optional[Union[str, Pattern]], handler: Callable, state: Optional[Any] = None):
        return self.on_text(pattern=pattern, state=state)(handler)

    def register_fallback_handler(self, handler: Callable, state: Optional[Any] = "*"):
        return self.fallback(state=state)(handler)

    # -------------------------------------------------------------------------
    # FSM Helpers
    # -------------------------------------------------------------------------

    def get_fsm_context(
        self,
        chat_id: Optional[Union[str, int]],
        user_id: Optional[Union[str, int]]
    ) -> FSMContext:
        """Create an FSMContext instance for the specified chat and user."""
        return FSMContext(self.storage, chat_id, user_id)

    def _sync_get_state(self, chat_id: Optional[Union[str, int]], user_id: Optional[Union[str, int]]) -> Optional[str]:
        """Synchronously check state if storage supports it or run quick event loop."""
        if hasattr(self.storage, "sync_get_state"):
            try:
                return self.storage.sync_get_state(chat_id, user_id)
            except Exception as e:
                logger.debug("Failed sync_get_state: %s", e)
        if isinstance(self.storage, MemoryStorage):
            k = self.storage._make_key(chat_id, user_id)
            return self.storage._states.get(k)
        try:
            loop = asyncio.get_running_loop()
            return None
        except RuntimeError:
            return asyncio.run(self.storage.get_state(chat_id, user_id))

    async def _async_get_state(self, chat_id: Optional[Union[str, int]], user_id: Optional[Union[str, int]]) -> Optional[str]:
        return await self.storage.get_state(chat_id, user_id)

    # -------------------------------------------------------------------------
    # Invocation & Execution Engine
    # -------------------------------------------------------------------------

    def _prepare_args(
        self,
        handler: Callable,
        client: Any,
        update: Update,
        fsm_context: FSMContext,
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Inspect handler parameters and prepare keyword or positional arguments."""
        try:
            sig = inspect.signature(handler)
        except Exception:
            return {"update": update}

        kwargs: Dict[str, Any] = {}
        matched_any = False

        for name, param in sig.parameters.items():
            if name == "client":
                kwargs[name] = client
                matched_any = True
            elif name in ("update", "upd", "event"):
                kwargs[name] = update
                matched_any = True
            elif name in ("state", "fsm_context", "context"):
                kwargs[name] = fsm_context
                matched_any = True
            elif name == "data":
                kwargs[name] = data
                matched_any = True
            elif name in data:
                kwargs[name] = data[name]
                matched_any = True

        if matched_any and len(kwargs) == len(sig.parameters):
            return kwargs

        # Fallback to positional arguments if names did not align
        pos_count = len(sig.parameters)
        if pos_count >= 3:
            return {"client": client, "update": update, "state": fsm_context}
        elif pos_count == 2:
            # Traditional (client, update) or (update, state)
            first_name = list(sig.parameters.keys())[0]
            if first_name in ("update", "upd", "event"):
                return {"update": update, "state": fsm_context}
            return {"client": client, "update": update}
        elif pos_count == 1:
            return {"update": update}
        return {}

    async def _execute_handler_async(
        self,
        handler: Callable,
        client: Any,
        update: Update,
        fsm_context: FSMContext,
        data: Dict[str, Any]
    ) -> Any:
        args_dict = self._prepare_args(handler, client, update, fsm_context, data)
        try:
            sig = inspect.signature(handler)
            has_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
            if has_kw or set(args_dict.keys()) == set(sig.parameters.keys()):
                call_args = (args_dict,)
                is_kwargs = True
            else:
                call_args = tuple(args_dict.values())
                is_kwargs = False
        except Exception:
            call_args = tuple(args_dict.values())
            is_kwargs = False

        if inspect.iscoroutinefunction(handler):
            if is_kwargs:
                return await handler(**call_args[0])
            return await handler(*call_args)
        else:
            if is_kwargs:
                res = handler(**call_args[0])
            else:
                res = handler(*call_args)
            if inspect.isawaitable(res):
                return await res
            return res

    def _execute_handler_sync(
        self,
        handler: Callable,
        client: Any,
        update: Update,
        fsm_context: FSMContext,
        data: Dict[str, Any]
    ) -> Any:
        args_dict = self._prepare_args(handler, client, update, fsm_context, data)
        try:
            sig = inspect.signature(handler)
            has_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
            if has_kw or set(args_dict.keys()) == set(sig.parameters.keys()):
                call_args = (args_dict,)
                is_kwargs = True
            else:
                call_args = tuple(args_dict.values())
                is_kwargs = False
        except Exception:
            call_args = tuple(args_dict.values())
            is_kwargs = False

        if inspect.iscoroutinefunction(handler):
            try:
                loop = asyncio.get_running_loop()
                # Loop is running, return coroutine for caller to await
                if is_kwargs:
                    return handler(**call_args[0])
                return handler(*call_args)
            except RuntimeError:
                # No loop running, run via asyncio.run
                if is_kwargs:
                    return asyncio.run(handler(**call_args[0]))
                return asyncio.run(handler(*call_args))
        else:
            if is_kwargs:
                res = handler(**call_args[0])
            else:
                res = handler(*call_args)
            if inspect.isawaitable(res):
                try:
                    loop = asyncio.get_running_loop()
                    return res
                except RuntimeError:
                    return asyncio.run(res)
            return res

    # -------------------------------------------------------------------------
    # Route Resolution Matching
    # -------------------------------------------------------------------------

    def _match_route(self, route: HandlerRoute, update: Update, current_state: Optional[str]) -> bool:
        """Check whether a single route matches the incoming update and state."""
        if not route.matches_state(current_state):
            return False

        upd_type = update.update_type

        # 1. bot_started
        if upd_type == "bot_started":
            if "bot_started" in route.update_types or "start" in route.commands:
                return True
            return False

        # 2. message_callback
        if upd_type == "message_callback":
            if "message_callback" not in route.update_types:
                return False
            if route.callback_pattern is not None:
                payload = update.callback.payload if update.callback else ""
                pat = route.callback_pattern
                if isinstance(pat, str):
                    if pat == payload or pat.startswith(payload) or payload.startswith(pat) or re.search(pat, payload):
                        return True
                    return False
                elif hasattr(pat, "search"):
                    return bool(pat.search(payload))
            return True

        # 3. message_created
        if upd_type == "message_created":
            if "message_created" not in route.update_types:
                return False

            text = (update.text or "").strip()

            # Photo filter
            if route.has_image is True:
                return update.has_image is True

            # Commands filter
            if route.commands:
                if text.startswith("/"):
                    cmd = text.split()[0].lstrip("/").lower()
                    return cmd in route.commands
                return False

            # Text pattern filter
            if route.text_pattern is not None:
                pat = route.text_pattern
                if isinstance(pat, str):
                    return pat.lower() in text.lower() or bool(re.search(pat, text, re.IGNORECASE))
                elif hasattr(pat, "search"):
                    return bool(pat.search(text))
                return False

            # Generic message handler without specific command/text constraints
            if not route.commands and route.text_pattern is None and route.has_image is None:
                return True

        return False

    # -------------------------------------------------------------------------
    # Dispatch Entry Points
    # -------------------------------------------------------------------------

    def dispatch(self, update: Any) -> AsyncResult:
        """
        Main entry point for routing an update.
        Returns an AsyncResult that acts as True/False synchronously
        and can be awaited in asynchronous contexts.
        """
        if isinstance(update, dict):
            update = Update.from_dict(update)

        chat_id = update.effective_chat_id
        user_id = update.sender_user_id
        raw = getattr(update, "raw", None)
        if isinstance(raw, dict):
            if not chat_id:
                chat_id = raw.get("chat_id") or (raw.get("recipient", {}).get("chat_id") if isinstance(raw.get("recipient"), dict) else None)
            if not user_id:
                sender_obj = raw.get("sender")
                if isinstance(sender_obj, dict):
                    user_id = sender_obj.get("id") or sender_obj.get("user_id")
                elif isinstance(raw.get("user"), dict):
                    user_id = raw.get("user", {}).get("id")
                elif raw.get("user_id"):
                    user_id = raw.get("user_id")
                elif raw.get("sender_user_id"):
                    user_id = raw.get("sender_user_id")

        fsm_context = self.get_fsm_context(chat_id, user_id)
        data: Dict[str, Any] = {
            "client": self.client,
            "fsm_context": fsm_context,
            "dispatcher": self
        }

        # 1. Attempt synchronous matching first
        current_state = self._sync_get_state(chat_id, user_id)

        matched_route = None
        for route in self._routes:
            if self._match_route(route, update, current_state):
                matched_route = route
                break

        if not matched_route and self._fallback_route:
            if self._fallback_route.matches_state(current_state):
                matched_route = self._fallback_route

        # Create async pipeline coroutine for when awaited
        async def _async_pipeline(already_executed: bool = False, sync_res: Any = None) -> bool:
            try:
                state_async = await self._async_get_state(chat_id, user_id)
                route_to_run = matched_route
                if route_to_run is None:
                    for r in self._routes:
                        if self._match_route(r, update, state_async):
                            route_to_run = r
                            break
                    if not route_to_run and self._fallback_route:
                        if self._fallback_route.matches_state(state_async):
                            route_to_run = self._fallback_route

                # Middlewares pre_process
                for mw in self._middlewares:
                    cont = await mw.pre_process(update, data)
                    if not cont:
                        return False

                if not route_to_run:
                    for mw in self._middlewares:
                        await mw.post_process(update, data, result=None)
                    return False

                handler_res = sync_res
                handler_exc = None
                if not already_executed:
                    try:
                        handler_res = await self._execute_handler_async(
                            route_to_run.handler,
                            self.client,
                            update,
                            fsm_context,
                            data
                        )
                    except Exception as exc:
                        handler_exc = exc
                        handled = await self._handle_exception_async(exc, update, data)
                        if not handled:
                            raise exc

                for mw in reversed(self._middlewares):
                    await mw.post_process(update, data, result=handler_res, exception=handler_exc)

                return True
            except Exception as e:
                logger.error("Unhandled error in async pipeline for %s: %s", update.update_type, e, exc_info=True)
                return False

        # If a route was matched synchronously and handler is regular def
        if matched_route and not inspect.iscoroutinefunction(matched_route.handler):
            loop = None
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            # When no middlewares are registered, execute synchronously immediately
            if not self._middlewares:
                try:
                    res = self._execute_handler_sync(
                        matched_route.handler,
                        self.client,
                        update,
                        fsm_context,
                        data
                    )
                    # If sync handler returned an awaitable/coroutine, ensure it is scheduled
                    if inspect.isawaitable(res):
                        if loop is not None:
                            task = loop.create_task(res)
                            self._active_tasks.add(task)
                            task.add_done_callback(self._active_tasks.discard)
                            return AsyncResult(True, coro=task)
                        else:
                            asyncio.run(res)
                            return AsyncResult(True)

                    return AsyncResult(True)
                except Exception as e:
                    logger.error("Error in sync handler execution for %s: %s", update.update_type, e, exc_info=True)
                    self._handle_exception_sync(e, update, data)
                    return AsyncResult(False)

            elif loop is None:
                # Middlewares present, but no loop running: execute synchronously with asyncio.run
                try:
                    for mw in self._middlewares:
                        cont = asyncio.run(mw.pre_process(update, data))
                        if not cont:
                            return AsyncResult(False)

                    res = self._execute_handler_sync(
                        matched_route.handler,
                        self.client,
                        update,
                        fsm_context,
                        data
                    )
                    if inspect.isawaitable(res):
                        res = asyncio.run(res)

                    for mw in reversed(self._middlewares):
                        asyncio.run(mw.post_process(update, data, result=res))

                    return AsyncResult(True)
                except Exception as e:
                    logger.error("Error in sync handler with middlewares for %s: %s", update.update_type, e, exc_info=True)
                    self._handle_exception_sync(e, update, data)
                    return AsyncResult(False)

        # If matched handler is async, or middlewares present in running loop, or no route matched synchronously
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(_async_pipeline(already_executed=False))
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)
            return AsyncResult(True if matched_route else False, coro=task)
        except RuntimeError:
            # No loop running, run async pipeline to completion
            sync_res = asyncio.run(_async_pipeline(already_executed=False))
            return AsyncResult(sync_res, coro=None)

    process_update = dispatch

    async def _handle_exception_async(self, exc: Exception, update: Update, data: Dict[str, Any]) -> bool:
        for exc_cls, handler in self._error_handlers.items():
            if isinstance(exc, exc_cls):
                try:
                    if inspect.iscoroutinefunction(handler):
                        await handler(exc, update)
                    else:
                        handler(exc, update)
                    return True
                except Exception as inner:
                    logger.error("Error in error handler: %s", inner)
        return False

    def _handle_exception_sync(self, exc: Exception, update: Update, data: Dict[str, Any]) -> bool:
        for exc_cls, handler in self._error_handlers.items():
            if isinstance(exc, exc_cls):
                try:
                    handler(exc, update)
                    return True
                except Exception as inner:
                    logger.error("Error in sync error handler: %s", inner)
        return False

    # -------------------------------------------------------------------------
    # Non-blocking Feed & Task Management
    # -------------------------------------------------------------------------

    def feed_update(self, update: Any) -> asyncio.Task:
        """
        Schedules non-blocking update processing in the background event loop.
        Tracks active task in self._active_tasks.
        """
        task = asyncio.create_task(self._safe_dispatch(update))
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)
        return task

    async def _safe_dispatch(self, update: Any) -> bool:
        try:
            res = self.dispatch(update)
            if inspect.isawaitable(res):
                return await res
            return bool(res)
        except Exception as exc:
            logger.error("Unhandled exception in feed_update task: %s", exc, exc_info=True)
            return False

    async def wait_closed(self, timeout: float = 5.0) -> None:
        """
        Gracefully drain all active background update processing tasks before shutdown.
        """
        if not self._active_tasks:
            return
        logger.info("Waiting for %d active bot tasks to finish...", len(self._active_tasks))
        try:
            loop = asyncio.get_running_loop()
            end_time = loop.time() + timeout
            while self._active_tasks:
                rem = end_time - loop.time()
                if rem <= 0:
                    break
                tasks = list(self._active_tasks)
                done, pending = await asyncio.wait(tasks, timeout=min(rem, 0.5))
        except Exception as e:
            logger.debug("Error during wait_closed drain: %s", e)
        for p in list(self._active_tasks):
            p.cancel()

    drain = wait_closed


# Backward compatibility aliases
Dispatcher = AsyncUpdateDispatcher
UpdateDispatcher = AsyncUpdateDispatcher
