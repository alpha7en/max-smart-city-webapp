"""Подача показаний (SPEC §5.6, SPEC_REVIEW B8–B11, C1–C9): фото → счётчик → распознавание → проверка → отправка.

Данные сессии (ctx.data):
  from: 'photo' | 'manual' | 'add'      — откуда начали (кнопки «Назад», подсказки)
  photo_id, caption                      — временное фото и подпись к нему (демо-ошибка «ошибка»)
  meter_id | draft{type,tariffs,address_id,serial?} — выбранный или новый счётчик (в БД — при отправке)
  addr{raw, cands, not_found, chosen}    — ввод нового адреса
  recognized{t1,t2,t3,serial,confidence,stub}, serial_status, serial_ok
  values{t1,t2,t3} (тысячные), source    — что отправим
  manual{idx, vals, back}                — ручной ввод по полям; back: 'review' | 'pick' | 'await'
  confirm, replace, check{kind,prev,delta,months} — повторная отправка после вопросов
  await_mode: 'instruction' | 'retake' | 'add' — что просим в SUB_AWAIT_PHOTO
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from app.bot import keyboards as K
from app.bot import photos
from app.bot.ctx import Ctx
from app.bot.router import (
    call_hook,
    cancel_scenario,
    drop_scenario,
    on_hook,
    on_repeat,
    on_state,
    repeat_step,
    show_menu,
)
from app.bot.states import S
from app.bot.texts import common as C
from app.bot.texts import fmt
from app.bot.texts import submission as T
from app.bot.texts.fmt import esc
from app.domain.access import can_submit
from app.domain.addresses import AddressCandidate, button_text, clean_flat, norm_key
from app.domain.meters import (
    TYPE_LABELS,
    DateParseError,
    MeterType,
    ValueParseError,
    current_period,
    field_labels,
    fields_for,
    format_value,
    growth_limit,
    looks_like_value,
    meter_labels,
    normalize_serial,
    parse_due_date,
    parse_value,
    spec,
    tariffs_of,
)
from app.integrations.recognizer import Recognition
from app.readings import SubmitResult, format_delta, format_months, format_values, submit_reading
from app.repo import values_of

log = logging.getLogger(__name__)
RECOGNIZE_TIMEOUT = 25.0          # с; сам HTTP-клиент ограничен 20 с
LOW_CONFIDENCE, CHECK_CONFIDENCE = 0.5, 0.8
DRAFT_STATES = {S.SUB_NEW_TYPE, S.SUB_NEW_TARIFF, S.SUB_NEW_ADDRESS, S.SUB_ADDR_INPUT, S.SUB_ADDR_PICK,
                S.SUB_ADDR_FLAT}
RESET_ON_NEW_PHOTO = ("recognized", "values", "source", "serial_status", "serial_ok", "manual", "confirm",
                      "replace", "check")
TYPE_ROWS = ((MeterType.COLD_WATER, MeterType.HOT_WATER), (MeterType.ELECTRICITY, MeterType.GAS, MeterType.HEAT))
_FLAT_OK = re.compile(r"^\d{1,5}[а-яёa-z]?$", re.I)


@dataclass
class Meter:
    """Счётчик сценария: существующий (id) или черновик (id=None)."""
    id: int | None
    type: str
    tariffs: int
    address_id: int
    label: str
    serial: str | None

    @property
    def fields(self) -> tuple[str, ...]:
        return fields_for(self.tariffs)


# === Общие помощники ===

def _uid(ctx: Ctx) -> int:
    return ctx.user["id"]


def _period(ctx: Ctx) -> str:
    return current_period(ctx.now.date())


def _has_meter(ctx: Ctx) -> bool:
    return bool(ctx.data.get("meter_id") or (ctx.data.get("draft") or {}).get("address_id"))


async def _begin(ctx: Ctx, origin: str) -> None:
    """Новый сценарий подачи с чистыми данными и новым flow (старые кнопки — устаревшие)."""
    await drop_scenario(ctx)
    ctx.data["from"] = origin


async def _blocked_address(ctx: Ctx) -> int | None:
    """C2: адреса есть, но ни по одному нет доступа → id первого (для «нет прав»)."""
    rows = await ctx.repo.user_addresses(_uid(ctx))
    if rows and not any(can_submit(r["access"]) for r in rows):
        return rows[0]["id"]
    return None


async def _no_access(ctx: Ctx, address_id: int | None) -> None:
    """Фото удаляем, сценарий в IDLE, сообщение «нет прав» (поток S1)."""
    await drop_scenario(ctx)
    if address_id is None:
        ctx.note(T.NO_METER)
        await show_menu(ctx)
        return
    await call_hook("access.no_access", ctx, address_id=address_id)


async def _handled_exit(ctx: Ctx) -> bool:
    """[Отмена] → «Подачу отменили» + меню; [В меню] → меню. True — событие обработано."""
    if ctx.action == "cancel":
        await cancel_scenario(ctx, by_user=True)
        await show_menu(ctx)
        return True
    if ctx.action == "menu":
        await drop_scenario(ctx)
        await show_menu(ctx)
        return True
    return False


def _row(ctx: Ctx, *buttons: tuple[str, str] | tuple[str, str, str | int]) -> list[K.Button]:
    return [ctx.btn(*b) for b in buttons]


async def _meter(ctx: Ctx) -> Meter | None:
    """Текущий счётчик с проверкой прав; нет прав — «нет прав» уже отправлено, → None."""
    d = ctx.data
    if d.get("meter_id"):
        meters = await ctx.repo.user_meters(_uid(ctx))
        for m, label in zip(meters, meter_labels(meters), strict=True):
            if m["id"] == d["meter_id"]:
                return Meter(m["id"], m["type"], tariffs_of(m["type"], m["tariffs"]), m["address_id"], label,
                             m["serial"])
        row = await ctx.repo.get_meter(d["meter_id"])
        await _no_access(ctx, row["address_id"] if row else None)
        return None
    draft = d.get("draft") or {}
    link = await ctx.repo.user_address(_uid(ctx), draft.get("address_id") or 0)
    if not link or not can_submit(link["access"]):
        await _no_access(ctx, draft.get("address_id"))
        return None
    return Meter(None, draft["type"], tariffs_of(draft["type"], draft.get("tariffs")), link["id"],
                 f"{TYPE_LABELS[draft['type']]} · {link['label']}", draft.get("serial"))


async def _prev_values(ctx: Ctx, m: Meter) -> dict | None:
    """Последнее показание до текущего периода (для подсказок и «В прошлый раз»)."""
    if m.id is None:
        return None
    row = await ctx.repo.last_reading(m.id, before_period=_period(ctx))
    return values_of(row) if row else None


# === Точки входа ===

@on_hook("submission.start")
async def start_submission(ctx: Ctx, **_) -> None:
    """«Подать показания»: инструкция к фото + [Ввести вручную] [Мини-приложение] [В меню]."""
    if (aid := await _blocked_address(ctx)) is not None:
        await _no_access(ctx, aid)
        return
    await _begin(ctx, "photo")
    ctx.session.go(S.SUB_AWAIT_PHOTO, await_mode="instruction")
    await ask_photo(ctx)


@on_hook("submission.manual")
async def start_manual(ctx: Ctx, **_) -> None:
    """Ручной ввод: выбор счётчика без фото."""
    if (aid := await _blocked_address(ctx)) is not None:
        await _no_access(ctx, aid)
        return
    await _begin(ctx, "manual")
    await _go_pick(ctx)


@on_hook("submission.add_meter")
async def start_add_meter(ctx: Ctx, **_) -> None:
    """C8: «Добавить счётчик» — тип → тариф → адрес → фото или ручной ввод; счётчик создаётся с показанием."""
    if (aid := await _blocked_address(ctx)) is not None:
        await _no_access(ctx, aid)
        return
    await _begin(ctx, "add")
    ctx.session.go(S.SUB_NEW_TYPE)
    await ask_type(ctx)


@on_hook("submission.with_photo")
async def start_with_photo(ctx: Ctx, photo_id: str | None = None, **_) -> None:
    """Фото уже скачано (прислали во время регистрации) — начинаем подачу с ним."""
    if (aid := await _blocked_address(ctx)) is not None:
        await photos.delete_photo(ctx.repo, photo_id)
        await _no_access(ctx, aid)
        return
    await _begin(ctx, "photo")
    if not await photos.photo_path(ctx.repo, photo_id):
        await photos.delete_photo(ctx.repo, photo_id)
        ctx.session.go(S.SUB_AWAIT_PHOTO, await_mode="retake")
        await ctx.reply(T.PHOTO_GONE, _retake_kb(ctx))
        return
    ctx.data["photo_id"] = photo_id
    ctx.note(T.PENDING_PHOTO)
    await _go_pick(ctx)


@on_hook("submission.photo")
async def on_photo(ctx: Ctx) -> None:
    """Фото в IDLE — новая подача; в SUB_* — замена фото (роутер, правило 6)."""
    s = ctx.session
    fresh = s.state == S.IDLE
    if fresh and (aid := await _blocked_address(ctx)) is not None:
        await _no_access(ctx, aid)
        return
    try:
        pid = await photos.download_to_tmp(ctx.api, ctx.repo, ctx.settings.photos_dir, _uid(ctx),
                                           ctx.event.photo_url or "", ctx.now)
    except photos.PhotoError as e:
        log.warning("photo download failed: %s", e)
        if fresh:
            await _begin(ctx, "photo")
            s.go(S.SUB_AWAIT_PHOTO, await_mode="retake")
            await ctx.reply(T.PHOTO_FAILED, _retake_kb(ctx))
        else:
            ctx.note(T.PHOTO_FAILED)
            await repeat_step(ctx)
        return
    if fresh:
        await _begin(ctx, "photo")
        ctx.note(T.PHOTO_RECEIVED)
    else:
        old = ctx.data.get("photo_id")
        await photos.delete_photo(ctx.repo, old)
        for key in RESET_ON_NEW_PHOTO:
            ctx.data.pop(key, None)
        if ctx.data.get("from") != "add":
            ctx.data["from"] = "photo"
        ctx.note(T.PHOTO_REPLACED if old or s.state != S.SUB_AWAIT_PHOTO else T.PHOTO_RECEIVED)
    ctx.data.update(photo_id=pid, caption=ctx.event.text)
    if _has_meter(ctx):
        await _recognize(ctx)
    elif s.state in DRAFT_STATES:
        await repeat_step(ctx)
    else:
        await _go_pick(ctx)


# === Ожидание фото (инструкция, «Переснять», добавление счётчика) ===

def _retake_kb(ctx: Ctx) -> dict:
    return K.kb(_row(ctx, (T.BTN_MANUAL, "manual")), _row(ctx, (C.BTN_CANCEL, "cancel")))


@on_repeat(S.SUB_AWAIT_PHOTO)
async def ask_photo(ctx: Ctx) -> None:
    mode = ctx.data.get("await_mode", "instruction")
    if mode == "instruction":
        await ctx.reply(T.INSTRUCTION, K.kb(
            [ctx.btn(T.BTN_MANUAL, "manual"), ctx.app_btn(C.BTN_MINIAPP)],
            [ctx.btn(C.BTN_MENU, "menu")],
        ))
    else:
        await ctx.reply(T.ADD_PROMPT if mode == "add" else T.RETAKE, _retake_kb(ctx))


@on_state(S.SUB_AWAIT_PHOTO)
async def await_photo(ctx: Ctx) -> None:
    if await _handled_exit(ctx):
        return
    alias = K.text_alias(ctx.text) if not ctx.is_callback else None
    if ctx.action == "manual":
        await _manual_from_await(ctx)
    elif ctx.action == "retake":
        ctx.data["await_mode"] = "retake"
        await ask_photo(ctx)
    elif not ctx.is_callback and looks_like_value(ctx.text):  # B9: число вместо фото — ручной ввод
        await _manual_from_await(ctx, typed=ctx.text)
    elif alias in ("no", "back"):
        await cancel_scenario(ctx, by_user=True)
        await show_menu(ctx)
    else:
        await ask_photo(ctx)


async def _manual_from_await(ctx: Ctx, typed: str | None = None) -> None:
    if _has_meter(ctx):
        await _start_manual_input(ctx, back="await", typed=typed)
        return
    ctx.data["from"] = "manual"
    if typed:
        ctx.data["typed"] = typed
    await _go_pick(ctx)


# === Выбор счётчика ===

async def _go_pick(ctx: Ctx) -> None:
    """К выбору счётчика; счётчиков нет — сразу к выбору типа нового."""
    ctx.data.pop("meter_id", None)
    ctx.data.pop("draft", None)
    if await ctx.repo.user_meters(_uid(ctx)):
        ctx.session.go(S.SUB_PICK_METER)
        await ask_pick(ctx)
        return
    ctx.note(T.NO_METERS_PHOTO if ctx.data.get("photo_id") else T.NO_METERS_MANUAL)
    ctx.session.go(S.SUB_NEW_TYPE)
    await ask_type(ctx)


@on_repeat(S.SUB_PICK_METER)
async def ask_pick(ctx: Ctx) -> None:
    meters = await ctx.repo.user_meters(_uid(ctx))
    rows = [[ctx.btn(label, "m", m["id"])] for m, label in zip(meters, meter_labels(meters), strict=True)]
    rows.append(_row(ctx, (T.BTN_NEW_METER, "new"), (C.BTN_CANCEL, "cancel")))
    await ctx.reply(T.PICK_PHOTO if ctx.data.get("photo_id") else T.PICK_MANUAL, K.kb(*rows))


@on_state(S.SUB_PICK_METER, buttons=True)
async def pick_meter(ctx: Ctx) -> None:
    if await _handled_exit(ctx):
        return
    if ctx.action == "m" and ctx.arg.isdigit():
        ctx.data.pop("draft", None)
        ctx.data["meter_id"] = int(ctx.arg)
        await _after_meter(ctx)
    elif ctx.action == "new":
        ctx.session.go(S.SUB_NEW_TYPE)
        await ask_type(ctx)
    else:
        await ask_pick(ctx)


async def _after_meter(ctx: Ctx) -> None:
    """Счётчик известен: есть фото — распознаём; добавление — просим фото; иначе — ручной ввод."""
    if await _meter(ctx) is None:
        return
    if ctx.data.get("photo_id"):
        await _recognize(ctx)
    elif ctx.data.get("from") == "add":
        ctx.session.go(S.SUB_AWAIT_PHOTO, await_mode="add")
        await ask_photo(ctx)
    else:
        await _start_manual_input(ctx, back="pick", typed=ctx.data.pop("typed", None))


# === Новый счётчик: тип → тарифы → адрес ===

async def _can_go_back_to_pick(ctx: Ctx) -> bool:
    return ctx.data.get("from") != "add" and bool(await ctx.repo.user_meters(_uid(ctx)))


@on_repeat(S.SUB_NEW_TYPE)
async def ask_type(ctx: Ctx) -> None:
    rows = [[ctx.btn(TYPE_LABELS[t], "t", t.value) for t in row] for row in TYPE_ROWS]
    last = [(C.BTN_BACK, "back")] if await _can_go_back_to_pick(ctx) else []
    rows.append(_row(ctx, *last, (C.BTN_CANCEL, "cancel")))
    await ctx.reply(T.ASK_TYPE, K.kb(*rows))


@on_state(S.SUB_NEW_TYPE, buttons=True)
async def got_type(ctx: Ctx) -> None:
    if await _handled_exit(ctx):
        return
    if ctx.action == "t" and ctx.arg in TYPE_LABELS:
        ctx.data["draft"] = {"type": ctx.arg, "tariffs": 1}
        if ctx.arg == MeterType.ELECTRICITY:
            ctx.session.go(S.SUB_NEW_TARIFF)
            await ask_tariff(ctx)
        else:
            ctx.session.go(S.SUB_NEW_ADDRESS)
            await ask_address(ctx)
    elif ctx.action == "back" and await _can_go_back_to_pick(ctx):
        ctx.session.go(S.SUB_PICK_METER)
        await ask_pick(ctx)
    else:
        await ask_type(ctx)


@on_repeat(S.SUB_NEW_TARIFF)
async def ask_tariff(ctx: Ctx) -> None:
    await ctx.reply(T.ASK_TARIFF, K.kb(
        [ctx.btn(text, "tar", n) for n, text in T.TARIFF_BUTTONS.items()],
        _row(ctx, (C.BTN_BACK, "back"), (C.BTN_CANCEL, "cancel")),
    ))


@on_state(S.SUB_NEW_TARIFF, buttons=True)
async def got_tariff(ctx: Ctx) -> None:
    if await _handled_exit(ctx):
        return
    if ctx.action == "tar" and ctx.arg in ("1", "2", "3"):
        ctx.data.setdefault("draft", {"type": MeterType.ELECTRICITY.value})["tariffs"] = int(ctx.arg)
        ctx.session.go(S.SUB_NEW_ADDRESS)
        await ask_address(ctx)
    elif ctx.action == "back":
        ctx.session.go(S.SUB_NEW_TYPE)
        await ask_type(ctx)
    else:
        await ask_tariff(ctx)


@on_repeat(S.SUB_NEW_ADDRESS)
async def ask_address(ctx: Ctx) -> None:
    """C1: всегда список адресов с доступом + [Другой адрес]."""
    rows = [[ctx.btn(a["label"], "a", a["id"])]
            for a in await ctx.repo.user_addresses(_uid(ctx)) if can_submit(a["access"])]
    rows.append(_row(ctx, (T.BTN_OTHER_ADDRESS, "other")))
    rows.append(_row(ctx, (C.BTN_BACK, "back"), (C.BTN_CANCEL, "cancel")))
    await ctx.reply(T.ASK_ADDRESS, K.kb(*rows))


@on_state(S.SUB_NEW_ADDRESS, buttons=True)
async def got_address(ctx: Ctx) -> None:
    if await _handled_exit(ctx):
        return
    if ctx.action == "a" and ctx.arg.isdigit():
        ctx.data.setdefault("draft", {})["address_id"] = int(ctx.arg)
        await _after_meter(ctx)
    elif ctx.action == "other":
        ctx.session.go(S.SUB_ADDR_INPUT)
        await ask_new_address(ctx)
    elif ctx.action == "back":
        electricity = (ctx.data.get("draft") or {}).get("type") == MeterType.ELECTRICITY
        ctx.session.go(S.SUB_NEW_TARIFF if electricity else S.SUB_NEW_TYPE)
        await repeat_step(ctx)
    else:
        await ask_address(ctx)


# === Новый адрес (как в регистрации; права — по модели domain/access) ===

def _address_service(ctx: Ctx):
    if ctx.deps.addresses is None:  # сервис не подключён (тесты, веб без бота) — локальный разбор
        from app.integrations.address_service import AddressService

        ctx.deps.addresses = AddressService(None)
    return ctx.deps.addresses


@on_repeat(S.SUB_ADDR_INPUT)
async def ask_new_address(ctx: Ctx) -> None:
    await ctx.reply(T.ASK_NEW_ADDRESS, K.kb(_row(ctx, (C.BTN_BACK, "back"), (C.BTN_CANCEL, "cancel"))))


@on_state(S.SUB_ADDR_INPUT)
async def got_new_address(ctx: Ctx) -> None:
    if await _handled_exit(ctx):
        return
    if ctx.action == "back" or K.text_alias(ctx.text) == "back":
        ctx.session.go(S.SUB_NEW_ADDRESS)
        await ask_address(ctx)
    elif ctx.is_callback or not ctx.text:
        await ask_new_address(ctx)
    else:
        await _search_address(ctx, ctx.text)


async def _search_address(ctx: Ctx, text: str) -> None:
    svc = _address_service(ctx)
    cands = await svc.suggest(text)
    not_found = False
    if not cands:
        cands = [c for c in svc.parse_local(text) if c.house][:1]
        not_found = True
        if not cands:
            ctx.session.go(S.SUB_ADDR_INPUT)
            await ctx.reply(T.ADDRESS_NOT_FOUND, K.kb(_row(ctx, (C.BTN_BACK, "back"), (C.BTN_CANCEL, "cancel"))))
            return
    ctx.session.go(S.SUB_ADDR_PICK, addr={"raw": text, "cands": [c.to_dict() for c in cands],
                                          "not_found": not_found})
    await ask_address_pick(ctx)


@on_repeat(S.SUB_ADDR_PICK)
async def ask_address_pick(ctx: Ctx) -> None:
    addr = ctx.data.get("addr") or {}
    cands = [AddressCandidate.from_dict(c) for c in addr.get("cands", [])]
    if not cands:
        ctx.session.go(S.SUB_ADDR_INPUT)
        await ask_new_address(ctx)
        return
    back = (C.BTN_BACK, "back")
    if addr.get("not_found"):
        await ctx.reply(T.ADDRESS_NOT_IN_REGISTRY.format(address=esc(cands[0].full_text)), K.kb(
            _row(ctx, (T.BTN_SAVE_AS_IS, "c", 0), (T.BTN_FIX, "again")), _row(ctx, back)))
    elif len(cands) == 1:
        note = T.ADDRESS_LOCAL_NOTE if cands[0].source == "local" else ""
        await ctx.reply(T.ADDRESS_ONE.format(address=esc(cands[0].full_text), note=note), K.kb(
            _row(ctx, (T.BTN_YES, "c", 0), (T.BTN_REENTER, "again")), _row(ctx, back)))
    else:
        rows = [[ctx.btn(button_text(c), "c", i)] for i, c in enumerate(cands)]
        rows += [_row(ctx, (T.BTN_NOT_MINE, "again")), _row(ctx, back)]
        await ctx.reply(T.ADDRESS_MANY, K.kb(*rows))


@on_state(S.SUB_ADDR_PICK)
async def got_address_pick(ctx: Ctx) -> None:
    """Кнопки вариантов; текст (кроме «да/нет/назад») — новый поиск (B9)."""
    if await _handled_exit(ctx):
        return
    addr = ctx.data.get("addr") or {}
    action = ctx.action if ctx.is_callback else K.text_alias(ctx.text)
    if action == "yes" and len(addr.get("cands", [])) == 1:
        action, ctx.arg = "c", "0"
    if action == "c" and ctx.arg.isdigit() and int(ctx.arg) < len(addr.get("cands", [])):
        cand = AddressCandidate.from_dict(addr["cands"][int(ctx.arg)])
        if cand.flat:
            await _save_address(ctx, cand)
        else:
            addr["chosen"] = cand.to_dict()
            ctx.session.go(S.SUB_ADDR_FLAT)
            await ask_flat(ctx)
    elif action in ("again", "no"):
        ctx.session.go(S.SUB_ADDR_INPUT)
        await ask_new_address(ctx)
    elif action == "back":
        ctx.session.go(S.SUB_NEW_ADDRESS)
        await ask_address(ctx)
    elif not ctx.is_callback and ctx.text:
        await _search_address(ctx, ctx.text)
    else:
        await ask_address_pick(ctx)


@on_repeat(S.SUB_ADDR_FLAT)
async def ask_flat(ctx: Ctx) -> None:
    await ctx.reply(T.ASK_FLAT, K.kb(_row(ctx, (T.BTN_PRIVATE_HOUSE, "house")),
                                     _row(ctx, (C.BTN_BACK, "back"), (C.BTN_CANCEL, "cancel"))))


@on_state(S.SUB_ADDR_FLAT)
async def got_flat(ctx: Ctx) -> None:
    if await _handled_exit(ctx):
        return
    chosen = (ctx.data.get("addr") or {}).get("chosen")
    if not chosen:
        ctx.session.go(S.SUB_ADDR_INPUT)
        await ask_new_address(ctx)
        return
    chosen = AddressCandidate.from_dict(chosen)
    if ctx.action == "back" or K.text_alias(ctx.text) == "back":
        ctx.session.go(S.SUB_ADDR_PICK)
        await ask_address_pick(ctx)
    elif ctx.action == "house":
        await _save_address(ctx, chosen)
    elif not ctx.is_callback and (flat := clean_flat(ctx.text)) and _FLAT_OK.match(flat):
        await _save_address(ctx, chosen.with_flat(flat))
    elif not ctx.is_callback and ctx.text:
        await ctx.reply(T.FLAT_ERROR, K.kb(_row(ctx, (T.BTN_PRIVATE_HOUSE, "house")),
                                           _row(ctx, (C.BTN_BACK, "back"), (C.BTN_CANCEL, "cancel"))))
    else:
        await ask_flat(ctx)


async def _save_address(ctx: Ctx, cand: AddressCandidate) -> None:
    """Привязать адрес по модели прав. Чужой адрес (pending) — C3: адрес остаётся, счётчик не создаём."""
    repo, uid = ctx.repo, _uid(ctx)
    key = norm_key(cand)
    existing = await repo.find_address(key)
    link = await repo.user_address(uid, existing["id"]) if existing else None
    if link:
        if not can_submit(link["access"]):
            await _no_access(ctx, link["id"])
            return
        ctx.note(T.ADDRESS_ALREADY.format(label=esc(link["label"])))
        address_id = link["id"]
    else:
        raw = (ctx.data.get("addr") or {}).get("raw")
        res = await repo.add_user_address(uid, cand.to_dict(), key, raw, ctx.now.date())
        if not can_submit(res["access"]):
            await _no_access(ctx, res["address_id"])
            return
        address_id = res["address_id"]
    ctx.data.pop("addr", None)
    ctx.data.setdefault("draft", {})["address_id"] = address_id
    await _after_meter(ctx)


# === Распознавание ===

async def _run_recognizer(ctx: Ctx, path: str, m: Meter, prev: dict | None) -> Recognition:
    hint = {"prev": prev, "serial": m.serial, "caption": ctx.data.get("caption")}
    try:
        return await asyncio.wait_for(
            ctx.deps.recognizer.recognize(path, m.type, m.tariffs, hint=hint), RECOGNIZE_TIMEOUT)
    except Exception as e:  # noqa: BLE001 — таймаут/сбой клиента = не распознали
        log.warning("recognizer failed: %r", e)
        return Recognition(confidence=0.0, error=type(e).__name__)


async def _recognize(ctx: Ctx) -> None:
    m = await _meter(ctx)
    if m is None:
        return
    d = ctx.data
    path = await photos.photo_path(ctx.repo, d.get("photo_id"))
    if not path:
        await photos.delete_photo(ctx.repo, d.pop("photo_id", None))
        ctx.session.go(S.SUB_AWAIT_PHOTO, await_mode="retake")
        await ctx.reply(T.PHOTO_GONE, _retake_kb(ctx))
        return
    await ctx.typing()
    await ctx.reply(T.LOOKING)
    rec = await _run_recognizer(ctx, path, m, await _prev_values(ctx, m))
    if rec.error or rec.confidence < LOW_CONFIDENCE or rec.values.get("t1") is None:
        await photos.delete_photo(ctx.repo, d.pop("photo_id", None))
        for key in RESET_ON_NEW_PHOTO:
            d.pop(key, None)
        ctx.session.go(S.SUB_AWAIT_PHOTO, await_mode="retake")
        await ctx.reply(T.RECOGNIZE_FAILED, K.kb(
            _row(ctx, (T.BTN_RETAKE, "retake"), (T.BTN_MANUAL, "manual")), _row(ctx, (C.BTN_CANCEL, "cancel"))))
        return
    if m.id is None and rec.serial:  # F12: новый счётчик, а по адресу уже есть счётчик с этим номером
        same = await ctx.repo.find_meter_by_serial(m.address_id, rec.serial)
        if same:
            d.pop("draft", None)
            d["meter_id"] = same["id"]
            old_kind = (m.type, m.tariffs)
            if (m := await _meter(ctx)) is None:
                return
            ctx.note(T.SWITCHED_METER.format(label=esc(m.label)))
            if (m.type, m.tariffs) != old_kind:
                rec = await _run_recognizer(ctx, path, m, await _prev_values(ctx, m))
        else:
            d["draft"]["serial"] = rec.serial
    d["recognized"] = {**{f: rec.values.get(f) for f in m.fields}, "serial": rec.serial,
                       "confidence": rec.confidence, "stub": rec.stub}
    d["values"] = {f: rec.values.get(f) for f in m.fields}
    d["source"] = "photo"
    d["serial_status"] = await _serial_status(ctx, m, rec.serial)
    if d["serial_status"] == "mismatch" and not d.get("serial_ok"):
        ctx.session.go(S.SUB_SERIAL_MISMATCH)
        await ask_serial(ctx)
        return
    ctx.session.go(S.SUB_REVIEW)
    await ask_review(ctx)


async def _serial_status(ctx: Ctx, m: Meter, serial: str | None) -> str | None:
    """'match' | 'mismatch' | 'new' (сохраним) | None (нечего показать)."""
    if not serial:
        return None
    if m.serial:
        return "match" if normalize_serial(serial) == normalize_serial(m.serial) else "mismatch"
    if m.id is None:
        return "new"
    other = await ctx.repo.find_meter_by_serial(m.address_id, serial)
    return None if other and other["id"] != m.id else "new"


@on_repeat(S.SUB_SERIAL_MISMATCH)
async def ask_serial(ctx: Ctx) -> None:
    m = await _meter(ctx)
    if m is None:
        return
    photo_serial = (ctx.data.get("recognized") or {}).get("serial") or "—"
    await ctx.reply(
        T.SERIAL_MISMATCH.format(photo=esc(photo_serial), label=esc(m.label), saved=esc(m.serial)),
        K.kb(_row(ctx, (T.BTN_OTHER_METER, "other")), _row(ctx, (T.BTN_SAME_METER, "same")),
             _row(ctx, (T.BTN_RETAKE, "retake"), (C.BTN_CANCEL, "cancel"))),
    )


@on_state(S.SUB_SERIAL_MISMATCH, buttons=True)
async def got_serial(ctx: Ctx) -> None:
    if await _handled_exit(ctx):
        return
    if ctx.action == "other":
        for key in RESET_ON_NEW_PHOTO:
            ctx.data.pop(key, None)
        await _go_pick(ctx)
    elif ctx.action in ("same", "yes"):
        ctx.data.update(serial_ok=True, serial_status="ignored")
        ctx.session.go(S.SUB_REVIEW)
        await ask_review(ctx)
    elif ctx.action == "retake":
        await _retake(ctx)
    else:
        await ask_serial(ctx)


async def _retake(ctx: Ctx) -> None:
    """Удалить фото и ждать новое; счётчик запомнен — новое фото сразу распознаем."""
    await photos.delete_photo(ctx.repo, ctx.data.pop("photo_id", None))
    for key in RESET_ON_NEW_PHOTO:
        ctx.data.pop(key, None)
    ctx.session.go(S.SUB_AWAIT_PHOTO, await_mode="retake")
    await ask_photo(ctx)


# === Проверка (эталон §8 п.6) ===

def _is_photo_path(ctx: Ctx) -> bool:
    return bool(ctx.data.get("photo_id"))


@on_repeat(S.SUB_REVIEW)
async def ask_review(ctx: Ctx) -> None:
    m = await _meter(ctx)
    if m is None:
        return
    d = ctx.data
    values = d.get("values") or {}
    lines = [esc(m.label), ""]
    if len(m.fields) == 1:
        lines.append(T.VALUE_LINE.format(value=fmt.value(values.get("t1"), m.type)))
    else:
        labels = field_labels(m.type, m.tariffs)
        lines += [T.TARIFF_LINE.format(label=labels[f], value=fmt.value(values.get(f), m.type)) for f in m.fields]
    serial = (d.get("recognized") or {}).get("serial")
    status_line = {"match": T.SERIAL_MATCH, "new": T.SERIAL_NEW, "ignored": T.SERIAL_IGNORED}.get(
        d.get("serial_status") or "")
    if status_line and serial:
        lines.append(status_line.format(serial=esc(serial)))
    prev = await _prev_values(ctx, m)
    if prev:
        if len(m.fields) == 1 and values.get("t1") is not None:
            lines.append(T.PREV_LINE.format(value=fmt.value(prev.get("t1"), m.type),
                                            delta=fmt.delta(values["t1"] - prev["t1"], m.type)))
        else:
            lines.append(T.PREV_LINE_MULTI.format(value=format_values(prev, m.type, m.tariffs)))
    rec = d.get("recognized") or {}
    if d.get("source") == "photo":
        if LOW_CONFIDENCE <= rec.get("confidence", 1) < CHECK_CONFIDENCE:
            lines.append(T.CHECK_DIGITS)
        if rec.get("stub"):
            lines.append(T.STUB_NOTE)
    lines += ["", T.REVIEW_QUESTION]
    second = [(T.BTN_EDIT, "edit")] + ([(T.BTN_RETAKE, "retake")] if _is_photo_path(ctx) else [])
    await ctx.reply("\n".join(lines), K.kb(
        _row(ctx, (T.BTN_SEND, "send")), _row(ctx, *second), _row(ctx, (C.BTN_CANCEL, "cancel"))))


@on_state(S.SUB_REVIEW)
async def got_review(ctx: Ctx) -> None:
    if await _handled_exit(ctx):
        return
    action = ctx.action if ctx.is_callback else K.text_alias(ctx.text)
    if action in ("send", "yes"):
        await _submit(ctx)
    elif action in ("edit", "no"):
        await _start_manual_input(ctx, back="review")
    elif action == "retake" and _is_photo_path(ctx):
        await _retake(ctx)
    elif not ctx.is_callback and looks_like_value(ctx.text):  # B9: число — исправление
        await _start_manual_input(ctx, back="review", typed=ctx.text)
    else:
        await ask_review(ctx)


# === Ручной ввод по полям ===

async def _start_manual_input(ctx: Ctx, *, back: str, typed: str | None = None) -> None:
    ctx.data["manual"] = {"idx": 0, "vals": {}, "back": back}
    ctx.session.go(S.SUB_MANUAL)
    if typed:
        await _manual_value(ctx, typed)
    else:
        await ask_manual(ctx)


def _manual_kb(ctx: Ctx) -> dict:
    return K.kb(_row(ctx, (C.BTN_BACK, "back"), (C.BTN_CANCEL, "cancel")))


@on_repeat(S.SUB_MANUAL)
async def ask_manual(ctx: Ctx) -> None:
    m = await _meter(ctx)
    if m is None:
        return
    man = ctx.data.setdefault("manual", {"idx": 0, "vals": {}, "back": "pick"})
    idx = min(man["idx"], len(m.fields) - 1)
    f = m.fields[idx]
    example = T.EXAMPLES[m.type]
    if len(m.fields) == 1:
        ask = T.ASK_VALUE.format(example=example)
    else:
        ask = T.ASK_TARIFF_VALUE.format(label=field_labels(m.type, m.tariffs)[f], n=idx + 1,
                                        total=len(m.fields), example=example)
    lines = [esc(m.label), "", ask]
    rec = (ctx.data.get("recognized") or {}).get(f)
    if rec is not None:
        lines.append(T.RECOGNIZED_HINT.format(value=fmt.value(rec, m.type)))
    prev = await _prev_values(ctx, m)
    if prev and prev.get(f) is not None:
        lines.append(T.PREV_HINT.format(value=fmt.value(prev[f], m.type)))
    await ctx.reply("\n".join(lines), _manual_kb(ctx))


@on_state(S.SUB_MANUAL)
async def got_manual(ctx: Ctx) -> None:
    if await _handled_exit(ctx):
        return
    if ctx.action == "back" or (not ctx.is_callback and K.text_alias(ctx.text) == "back"):
        await _manual_back(ctx)
    elif ctx.is_callback:
        await ask_manual(ctx)
    else:
        await _manual_value(ctx, ctx.text)


async def _manual_back(ctx: Ctx) -> None:
    """C4: на первом поле — туда, откуда пришли; иначе — предыдущее поле."""
    man = ctx.data.get("manual") or {"idx": 0, "back": "pick"}
    if man["idx"] > 0:
        man["idx"] -= 1
        await ask_manual(ctx)
        return
    ctx.data.pop("manual", None)
    if man["back"] == "review" and ctx.data.get("values"):
        ctx.session.go(S.SUB_REVIEW)
        await ask_review(ctx)
    elif man["back"] == "await":
        ctx.session.go(S.SUB_AWAIT_PHOTO)
        await ask_photo(ctx)
    else:
        await _go_pick(ctx)


async def _manual_value(ctx: Ctx, text: str) -> None:
    m = await _meter(ctx)
    if m is None:
        return
    man = ctx.data.setdefault("manual", {"idx": 0, "vals": {}, "back": "pick"})
    f = m.fields[min(man["idx"], len(m.fields) - 1)]
    try:
        man["vals"][f] = parse_value(text, m.type)
    except ValueParseError as e:
        sp = spec(m.type)
        await ctx.reply(T.PARSE_ERRORS[e.code].format(example=T.EXAMPLES[m.type], digits=sp.int_digits,
                                                      decimals=sp.frac_digits), _manual_kb(ctx))
        return
    man["idx"] += 1
    if man["idx"] < len(m.fields):
        await ask_manual(ctx)
        return
    ctx.data["values"] = {f: man["vals"].get(f) for f in m.fields}
    ctx.data["source"] = "photo_edited" if ctx.data.get("recognized") else "manual"
    for key in ("manual", "confirm", "replace", "check"):
        ctx.data.pop(key, None)
    ctx.session.go(S.SUB_REVIEW)
    await ask_review(ctx)


# === Отправка и её вопросы ===

async def _submit(ctx: Ctx) -> None:
    d = ctx.data
    m = await _meter(ctx)
    if m is None:
        return
    res = await submit_reading(
        ctx.repo, user_id=_uid(ctx), meter_id=m.id, draft=None if m.id else d.get("draft"),
        values=d.get("values") or {}, source=d.get("source") or "manual", recognized=d.get("recognized"),
        confirm=bool(d.get("confirm")), replace=bool(d.get("replace")), today=ctx.now.date(),
    )
    if res.ok:
        await _done(ctx, m, res)
    elif res.status == "already_submitted":
        d["check"] = {"kind": "already", "old": res.previous}
        ctx.session.go(S.SUB_REPLACE_CONFIRM)
        await ask_replace(ctx)
    elif res.status in ("less_than_previous", "needs_confirm"):
        d["check"] = {"kind": "less" if res.status == "less_than_previous" else "big",
                      "prev": res.previous, "delta": res.delta, "months": res.months}
        ctx.session.go(S.SUB_PLAUSIBILITY)
        await ask_plausibility(ctx)
    elif res.status == "no_access":
        await _no_access(ctx, m.address_id)
    else:  # bad_format — значения не прошли проверку: вводим заново
        ctx.note(res.message)
        await _start_manual_input(ctx, back="review")


@on_repeat(S.SUB_PLAUSIBILITY)
async def ask_plausibility(ctx: Ctx) -> None:
    m = await _meter(ctx)
    if m is None:
        return
    check = ctx.data.get("check") or {}
    values = ctx.data.get("values") or {}
    cancel = (C.BTN_CANCEL, "cancel")
    if check.get("kind") == "big":
        months = check.get("months") or 1
        text = T.TOO_BIG.format(delta=format_delta(m.type, check.get("delta") or {}), months=format_months(months),
                                limit=f"{format_value(growth_limit(m.type, months), m.type)} {spec(m.type).unit}")
        kb = K.kb(_row(ctx, (T.BTN_CONFIRM_BIG, "confirm")), _row(ctx, (T.BTN_EDIT, "edit"), cancel))
    else:
        text = T.LESS_THAN_PREV.format(prev=format_values(check.get("prev") or {}, m.type, m.tariffs),
                                       new=format_values(values, m.type, m.tariffs))
        retake = [(T.BTN_RETAKE, "retake")] if _is_photo_path(ctx) else []
        kb = K.kb(_row(ctx, (T.BTN_EDIT, "edit"), *retake), _row(ctx, cancel))
    await ctx.reply(text, kb)


@on_state(S.SUB_PLAUSIBILITY, buttons=True)
async def got_plausibility(ctx: Ctx) -> None:
    if await _handled_exit(ctx):
        return
    kind = (ctx.data.get("check") or {}).get("kind")
    if ctx.action in ("confirm", "yes") and kind == "big":
        ctx.data["confirm"] = True
        await _submit(ctx)
    elif ctx.action in ("edit", "no"):
        await _start_manual_input(ctx, back="review")
    elif ctx.action == "retake" and _is_photo_path(ctx):
        await _retake(ctx)
    else:
        await ask_plausibility(ctx)


@on_repeat(S.SUB_REPLACE_CONFIRM)
async def ask_replace(ctx: Ctx) -> None:
    m = await _meter(ctx)
    if m is None:
        return
    old = (ctx.data.get("check") or {}).get("old") or {}
    await ctx.reply(
        T.ALREADY_SUBMITTED.format(month=fmt.month_name(_period(ctx)), old=format_values(old, m.type, m.tariffs),
                                   new=format_values(ctx.data.get("values") or {}, m.type, m.tariffs)),
        K.kb(_row(ctx, (T.BTN_REPLACE, "replace"), (T.BTN_KEEP_OLD, "keep"))),
    )


@on_state(S.SUB_REPLACE_CONFIRM, buttons=True)
async def got_replace(ctx: Ctx) -> None:
    if await _handled_exit(ctx):
        return
    if ctx.action in ("replace", "yes"):
        ctx.data["replace"] = True
        await _submit(ctx)
    elif ctx.action in ("keep", "no"):
        await drop_scenario(ctx)
        ctx.note(T.KEPT_OLD)
        await show_menu(ctx)
    else:
        await ask_replace(ctx)


# === Готово и дата поверки ===

def _after_kb() -> dict:
    return K.kb([K.gbtn(C.BTN_MENU, "menu"), K.gbtn(T.BTN_MORE, "submit")])


async def _done(ctx: Ctx, m: Meter, res: SubmitResult) -> None:
    d = ctx.data
    stub = d.get("source") == "photo" and (d.get("recognized") or {}).get("stub")
    created = m.id is None and res.meter_id is not None
    if created:  # подпись нового счётчика — с учётом остальных
        meters = await ctx.repo.user_meters(_uid(ctx))
        label = dict(zip([x["id"] for x in meters], meter_labels(meters), strict=True)).get(res.meter_id, m.label)
    else:
        label = m.label
    lines = [T.DONE.format(label=esc(label), value=format_values(res.values, m.type, m.tariffs),
                           month=fmt.month_name(res.period)), T.UK_MOCK]
    if stub:
        lines.append(T.STUB_DONE)
    if res.status == "flagged":
        lines.append(T.FLAGGED_DONE)
    await drop_scenario(ctx)  # фото удалено, новый flow — кнопки проверки устарели
    meter = await ctx.repo.get_meter(res.meter_id)
    if created and meter and not meter["verification_due"]:
        ctx.session.go(S.SUB_VERIF_DATE, meter_id=res.meter_id)
        await ctx.reply("\n".join(lines) + "\n\n" + T.ASK_VERIF, _verif_kb(ctx))
        return
    await ctx.reply("\n".join(lines), _after_kb())


def _verif_kb(ctx: Ctx) -> dict:
    return K.kb(_row(ctx, (T.BTN_LATER, "later")))


@on_repeat(S.SUB_VERIF_DATE)
async def ask_verif(ctx: Ctx) -> None:
    await ctx.reply(T.ASK_VERIF, _verif_kb(ctx))


@on_state(S.SUB_VERIF_DATE)
async def got_verif(ctx: Ctx) -> None:
    if ctx.action == "later" or (not ctx.is_callback and ctx.text.lower() in T.LATER_WORDS):
        await drop_scenario(ctx)
        await ctx.reply(T.VERIF_LATER, _after_kb())
        return
    if ctx.is_callback or not ctx.text:
        await ask_verif(ctx)
        return
    try:
        due = parse_due_date(ctx.text, ctx.now.date())
    except DateParseError as e:
        await ctx.reply(T.VERIF_ERRORS[e.code], _verif_kb(ctx))
        return
    await ctx.repo.set_verification(ctx.data["meter_id"], due.isoformat(), "user")
    await drop_scenario(ctx)
    await ctx.reply(T.VERIF_SAVED.format(date=f"{fmt.day_month(due)} {due.year}"), _after_kb())
