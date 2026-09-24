"""Роутер: глобальные правила (SPEC §5.4) → обработчик состояния.

Потоки регистрируют обработчики декораторами в своих модулях app/bot/flows/*, router.py не правят:

    @on_state(S.REG_NAME)                          # событие в состоянии (текст/кнопка сценария)
    @on_state(S.REG_CONFIRM, buttons=True)         # шаг ждёт кнопку: текст «да/нет/назад» → ctx.action
    @on_state(S.REG_PHONE, accepts=("contact",))   # шаг принимает contact / location
    @on_repeat(S.REG_NAME)                         # заново задать вопрос шага (с клавиатурой)
    @on_global("menu")                             # кнопка g|menu|arg из любого состояния
    @on_command("/demo")                           # команда в чате
    @on_hook("menu")                               # точки входа, которые вызывает роутер (см. HOOK_NAMES)

В обработчике: ctx.action / ctx.arg — действие нажатой кнопки сценария; ctx.text — текст;
ctx.session.go(S.X, key=value) — переход; ctx.reply(...) — ответ. Сессия сохраняется роутером
после успешной обработки; при исключении — не сохраняется (состояние не трогаем).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from app import clock
from app.bot import keyboards as K
from app.bot import photos
from app.bot.ctx import Ctx, Deps
from app.bot.events import Event
from app.bot.keyboards import Payload
from app.bot.session import load_session, save_session
from app.bot.states import S
from app.bot.texts import common as T

log = logging.getLogger(__name__)
Handler = Callable[[Ctx], Awaitable[None]]

STATE_HANDLERS: dict[S, Handler] = {}
REPEAT_STEP: dict[S, Handler] = {}
GLOBAL_ACTIONS: dict[str, Handler] = {}
# Диплинк max.ru/<бот>?start=<payload> из мини-приложения → глобальное действие (кнопка g|action).
# Незарегистрированному — обычная регистрация (правило 2), в регистрации — «продолжим» (правило 3).
START_PAYLOADS = {
    "profile": "profile", "phone": "prof_phone", "add_address": "prof_addr", "delete_data": "prof_del",
    "add_meter": "add_meter", "submit": "submit", "meters": "meters",
}
COMMANDS: dict[str, Handler] = {}
HOOKS: dict[str, Handler] = {}
BUTTON_STATES: set[S] = set()
ACCEPTS: dict[S, frozenset[str]] = {}
# Точки входа, которые вызывает роутер:
#   "menu"               — показать меню-дашборд; перевести сессию в IDLE.
#   "registration.begin" — приветствие + первый вопрос (REG_NAME); сохранить data["pending_photo_id"].
#   "submission.photo"   — фото в IDLE / SUB_*: начать подачу или заменить фото в текущей.
# Точки входа между потоками (вызывать через call_hook, регистрировать через @on_hook):
#   "submission.start"     — инструкция «пришлите фото» (S2), вызывает меню/уведомления (S4).
#   "submission.manual"    — ручной ввод: выбор счётчика без фото (S2).
#   "submission.add_meter" — «Добавить счётчик» из «Мои счётчики» (S2).
#   "submission.with_photo"— начать подачу с уже сохранённым фото, kw photo_id (S2; вызывает S1 после регистрации).
#   "access.no_access"     — сообщение «нет прав» по адресу, kw address_id (S1; вызывает S2).
HOOK_NAMES = (
    "menu", "registration.begin", "submission.photo",
    "submission.start", "submission.manual", "submission.add_meter", "submission.with_photo",
    "access.no_access",
)
DEDUP_SIZE = 2000


def on_state(*states: S, buttons: bool = False, accepts: tuple[str, ...] = ()):
    def deco(fn: Handler) -> Handler:
        for st in states:
            STATE_HANDLERS[st] = fn
            if buttons:
                BUTTON_STATES.add(st)
            else:
                BUTTON_STATES.discard(st)
            ACCEPTS[st] = frozenset(accepts)
        return fn
    return deco


def on_repeat(*states: S):
    def deco(fn: Handler) -> Handler:
        for st in states:
            REPEAT_STEP[st] = fn
        return fn
    return deco


def on_global(action: str):
    def deco(fn: Handler) -> Handler:
        GLOBAL_ACTIONS[action] = fn
        return fn
    return deco


def on_command(name: str):
    def deco(fn: Handler) -> Handler:
        COMMANDS[name.lower()] = fn
        return fn
    return deco


def on_hook(name: str):
    if name not in HOOK_NAMES:
        raise ValueError(f"unknown hook {name!r}")

    def deco(fn: Handler) -> Handler:
        HOOKS[name] = fn
        return fn
    return deco


# --- Общие действия, доступные потокам ---

async def call_hook(name: str, ctx: Ctx, **kw) -> None:
    """Вызвать точку входа другого потока. Нет регистрации — показать меню (и залогировать)."""
    handler = HOOKS.get(name)
    if handler is None:
        log.error("hook %s is not registered", name)
        await show_menu(ctx)
        return
    await handler(ctx, **kw)


async def show_menu(ctx: Ctx) -> None:
    await HOOKS["menu"](ctx)


async def repeat_step(ctx: Ctx) -> None:
    """Заново задать вопрос текущего шага. Нет обработчика — меню (или начало регистрации)."""
    handler = REPEAT_STEP.get(ctx.session.state)
    if handler:
        await handler(ctx)
    elif not ctx.registered:
        await HOOKS["registration.begin"](ctx)
    else:
        ctx.session.reset()
        await show_menu(ctx)


async def drop_scenario(ctx: Ctx) -> None:
    """Сбросить сценарий в IDLE и удалить его временное фото."""
    await photos.delete_photo(ctx.repo, ctx.session.data.get("photo_id"))
    ctx.session.reset()


def _nothing_to_cancel(state: S) -> bool:
    """SUB_VERIF_DATE: показание уже сохранено, ждём только необязательную дату поверки."""
    return state == S.SUB_VERIF_DATE


SUB_PROGRESS_KEYS = ("photo_id", "meter_id", "draft", "values", "addr", "manual")


async def cancel_scenario(ctx: Ctx, *, by_user: bool = False) -> None:
    """Отмена подачи/профиля + строка об этом в следующем сообщении.
    Подачу без прогресса (только инструкция к фото) прерываем молча: терять было нечего (QA-6)."""
    st = ctx.session.state
    if not st.is_scenario:
        return
    progress = any(ctx.session.data.get(k) for k in SUB_PROGRESS_KEYS)
    await drop_scenario(ctx)
    if _nothing_to_cancel(st) or (st.is_sub and not by_user and not progress):
        return
    if st.is_sub:
        ctx.note(T.SUB_CANCELLED_BY_USER if by_user else T.SUB_CANCELLED)
    else:
        ctx.note(T.PROFILE_CANCELLED_BY_USER if by_user else T.PROFILE_CANCELLED)


async def restart_registration(ctx: Ctx) -> None:
    """Очистить черновик регистрации (фото сохраняем) и начать с ФИО."""
    pending = ctx.session.data.get("pending_photo_id")
    ctx.session.reset()
    ctx.session.go(S.REG_NAME)
    if pending:
        ctx.session.data["pending_photo_id"] = pending
    await repeat_step(ctx)


async def keep_pending_photo(ctx: Ctx) -> None:
    """Фото до/во время регистрации: сохранить на 24 ч (новое заменяет старое)."""
    s = ctx.session
    try:
        pid = await photos.download_to_tmp(
            ctx.api, ctx.repo, ctx.settings.photos_dir, ctx.user["id"], ctx.event.photo_url or "",
            ctx.now, photos.PENDING_PHOTO_TTL,
        )
    except photos.PhotoError as e:
        log.warning("pending photo download failed: %s", e)
        ctx.note(T.PHOTO_FAILED)
        return
    old = s.data.get("pending_photo_id")
    if old:
        await photos.delete_photo(ctx.repo, old)
    s.data["pending_photo_id"] = pid
    ctx.note(T.PHOTO_REPLACED_PENDING if old else T.PHOTO_SAVED)


class Router:
    def __init__(self, deps: Deps):
        import app.bot.flows  # noqa: F401 — модули флоу регистрируют обработчики при импорте

        missing = [h for h in HOOK_NAMES if h not in HOOKS]
        if missing:
            raise RuntimeError(f"flows did not register hooks: {missing}")
        self.deps = deps
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._locks: dict[int, asyncio.Lock] = {}
        self._waiting: dict[int, int] = {}

    def _duplicate(self, update_id: str) -> bool:
        if not update_id:
            return False
        if update_id in self._seen:
            return True
        self._seen[update_id] = None
        if len(self._seen) > DEDUP_SIZE:
            self._seen.popitem(last=False)
        return False

    async def handle(self, ev: Event) -> None:
        """Обработать событие: дедуп, лок на пользователя, правила, обработчик."""
        if self._duplicate(ev.update_id):
            return
        uid = ev.user_id
        lock = self._locks.setdefault(uid, asyncio.Lock())
        self._waiting[uid] = self._waiting.get(uid, 0) + 1
        pre_answered = False
        try:
            if ev.kind == "callback" and lock.locked():
                # Пользователь ждёт окончания прошлой обработки — снимаем «часики» сразу.
                pre_answered = True
                await self._quiet_ack(ev)
            async with lock:
                await self._process(ev, pre_answered)
        finally:
            self._waiting[uid] -= 1
            if not self._waiting[uid]:
                del self._waiting[uid]
                self._locks.pop(uid, None)

    async def _quiet_ack(self, ev: Event) -> None:
        try:
            await self.deps.api.answer(ev.callback_id)
        except Exception as e:  # noqa: BLE001
            log.warning("callback ack failed: %s", e)

    async def _process(self, ev: Event, pre_answered: bool) -> None:
        repo = self.deps.repo
        now = clock.now()
        user = await repo.ensure_user(ev.user_id, ev.chat_id)
        session = await load_session(repo, user["id"])
        ctx = Ctx(self.deps, ev, session, user, now, answered=pre_answered)
        payload = K.decode(ev.payload) if ev.kind == "callback" else None
        if payload:
            ctx.action, ctx.arg = payload.action, payload.arg
        try:
            await self._dispatch(ctx, payload)
            await ctx.flush_notes()
        except Exception:
            await self._on_error(ctx)
            return
        finally:
            if ctx.is_callback and not ctx.answered:
                await ctx.ack()
        if not ctx.drop_session:
            await save_session(repo, ctx.session, now)

    async def _on_error(self, ctx: Ctx) -> None:
        """Правило 9: лог с trace_id, человеческое сообщение, состояние не трогаем."""
        trace = uuid.uuid4().hex[:8]
        log.exception("handler failed trace=%s user=%s state=%s kind=%s",
                      trace, ctx.event.user_id, ctx.session.state, ctx.event.kind)
        try:
            ctx._notes.clear()
            stored = await load_session(ctx.repo, ctx.session.user_id)
            rows = [K.callback(T.BTN_RETRY, stored.flow_id, "retry")]
            if ctx.registered and not stored.state.is_reg:
                rows.append(K.gbtn(T.BTN_MENU, "menu"))
            await ctx.reply(T.ERROR, K.kb(rows))
        except Exception:  # noqa: BLE001
            log.exception("error reply failed trace=%s", trace)

    async def _dispatch(self, ctx: Ctx, p: Payload | None) -> None:
        ev, s = ctx.event, ctx.session
        text = ctx.text
        low = text.lower()
        is_menu_text = ev.kind == "text" and low in T.MENU_WORDS
        is_global_cb = ev.kind == "callback" and p is not None and p.is_global

        # 10. Истёкшая подача/профиль → IDLE (фото удаляем).
        if s.is_expired(ctx.now):
            was = s.state
            await drop_scenario(ctx)
            if not (ev.kind == "start" or is_menu_text or is_global_cb):
                if not _nothing_to_cancel(was):
                    ctx.note(T.PROFILE_EXPIRED if was.is_profile else T.SUB_EXPIRED)
                if ev.kind != "photo":
                    await ctx.ack()
                    await show_menu(ctx)
                    return

        # 2. Незарегистрированный вне регистрации → приветствие и регистрация.
        if not ctx.registered and not s.state.is_reg:
            await ctx.ack()
            await drop_scenario(ctx)
            await HOOKS["registration.begin"](ctx)
            if ev.kind == "photo":
                await keep_pending_photo(ctx)
            return

        # 5. Кнопки.
        if ev.kind == "callback":
            await self._callback(ctx, p)
            return

        # 3. /start и «меню».
        if ev.kind == "start" or is_menu_text:
            if s.state.is_reg:
                await ctx.reply(T.CONTINUE_REG, K.kb([ctx.btn(T.BTN_RESTART, "restart")]))
                await repeat_step(ctx)
            else:
                await cancel_scenario(ctx)
                action = START_PAYLOADS.get((ev.start_payload or "").strip().lower())
                await (GLOBAL_ACTIONS[action] if action in GLOBAL_ACTIONS else show_menu)(ctx)
            return

        # 4. Отмена.
        if ev.kind == "text" and low in T.CANCEL_WORDS:
            if s.state.is_reg:
                await ctx.reply(T.REG_CANCEL_HINT, K.kb(
                    [ctx.btn(T.BTN_RESTART, "restart"), ctx.btn(T.BTN_CONTINUE, "retry")]))
            else:
                await cancel_scenario(ctx, by_user=True)
                await show_menu(ctx)
            return

        # Команды «/…».
        if ev.kind == "text" and text.startswith("/"):
            handler = COMMANDS.get(low.split()[0])
            if handler is None:
                ctx.note(T.UNKNOWN_COMMAND)
                await repeat_step(ctx)
            elif s.state.is_reg:
                ctx.note(T.FINISH_REG_FIRST + ".")
                await repeat_step(ctx)
            else:
                await cancel_scenario(ctx)
                await handler(ctx)
            return

        # 6. Фото.
        if ev.kind == "photo":
            if s.state.is_reg:
                await keep_pending_photo(ctx)
                await repeat_step(ctx)
                return
            if s.state.is_profile or s.state == S.SUB_VERIF_DATE:
                await drop_scenario(ctx)
            if ev.photo_count > 1:
                ctx.note(T.FIRST_PHOTO_ONLY)
            await HOOKS["submission.photo"](ctx)
            return

        # 7. Контакт/геолокация не к месту, прочие вложения.
        if ev.kind == "other" or (
            ev.kind in ("contact", "location") and ev.kind not in ACCEPTS.get(s.state, ())
        ):
            ctx.note(T.UNSUPPORTED)
            await repeat_step(ctx)
            return

        # 8. Текст там, где ждём кнопку.
        if ev.kind == "text" and s.state in BUTTON_STATES:
            alias = K.text_alias(text)
            if alias is None:
                await repeat_step(ctx)
                return
            ctx.action, ctx.arg = alias, ""

        await (STATE_HANDLERS.get(s.state) or repeat_step)(ctx)

    async def _callback(self, ctx: Ctx, p: Payload | None) -> None:
        s = ctx.session
        if p is not None and p.is_global and p.action in GLOBAL_ACTIONS:
            if s.state.is_reg:
                await ctx.toast(T.FINISH_REG_FIRST)
                await repeat_step(ctx)
                return
            await ctx.ack()  # глобальные действия — всегда новым сообщением
            await cancel_scenario(ctx)
            await GLOBAL_ACTIONS[p.action](ctx)
            return
        if p is None or p.is_global or p.flow != s.flow_id:
            await self._stale(ctx)
            return
        if p.action == "retry":
            await repeat_step(ctx)
            return
        if p.action == "restart" and s.state.is_reg:
            await restart_registration(ctx)
            return
        await (STATE_HANDLERS.get(s.state) or repeat_step)(ctx)

    async def _stale(self, ctx: Ctx) -> None:
        await ctx.toast(T.STALE_BUTTON)
        await ctx.clear_keyboard()
        await repeat_step(ctx)
