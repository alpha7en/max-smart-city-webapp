"""Подача показаний (SPEC §5.6, SPEC_REVIEW B8–B11, C1–C9): фото → счётчик → распознавание → проверка → отправка.

Данные сессии (ctx.data):
  from: 'photo' | 'manual' | 'add'      — откуда начали (кнопки «Назад», подсказки)
  photo_id, caption                      — временное фото и подпись к нему (демо-ошибка «ошибка»)
  meter_id | draft{type,tariffs,address_id,serial?} — выбранный или новый счётчик (в БД — при отправке)
  recognized{t1,t2,t3,texts,serial,confidence,stub,issues,brand,model}, serial_status, serial_ok
    serial_status: 'match' | 'new' | 'mismatch' | 'other' (номер другого счётчика) | 'ignored' (номер на фото неверный)
                   | 'entered' (номер ввели вручную — serial_entered)
    Серийник черновика берём только при отправке (из recognized) — до неё у нового счётчика номера нет.
    Номер обязателен: нет ни у счётчика, ни на фото → «Не разобрали серийный номер» / ввод номера.
    (brand/model — только в БД, в тексты не попадают)
  values{t1,t2,t3} (тысячные), source    — что отправим
  texts{t1,t2,t3}                        — показание как прочитано/введено ('2168', без выдуманных ',00')
  manual{idx, vals, back}                — ручной ввод по полям; back: 'review' | 'pick' | 'await'
  confirm, replace, check{kind,prev,delta,months} — повторная отправка после вопросов
  await_mode: 'instruction' | 'retake' | 'add' — что просим в SUB_AWAIT_PHOTO
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from app import arshin_service as AS
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
from app.bot.session import save_session
from app.bot.states import S
from app.bot.texts import arshin as TA
from app.bot.texts import common as C
from app.bot.texts import fmt
from app.bot.texts import meters as TM
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
    text_matches,
    typed_text,
    looks_like_value,
    meter_labels,
    normalize_serial,
    parse_due_date,
    parse_value,
    spec,
    tariffs_of,
)
from app.domain.serials import clean_serial, format_serial, usable_serial, validate_serial
from app.domain.verification import Record, card_url, short_title
from app.integrations.recognizer import CONF_MIN, SERVICE, Recognition
from app.readings import SubmitResult, format_delta, format_months, format_values, submit_reading
from app.repo import values_of

log = logging.getLogger(__name__)
RECOGNIZE_TIMEOUT = 25.0          # с; сам HTTP-клиент ограничен 20 с (сервис отвечает за 2–5 с)
LOW_CONFIDENCE, CHECK_CONFIDENCE = CONF_MIN, 0.8
PAGE_SIZE = 20                    # кнопок-вариантов на экране выбора счётчика/адреса
DRAFT_STATES = {S.SUB_NEW_TYPE, S.SUB_NEW_TARIFF, S.SUB_NEW_ADDRESS, S.SUB_ADDR_INPUT, S.SUB_ADDR_PICK,
                S.SUB_ADDR_FLAT}
RESET_ON_NEW_PHOTO = ("recognized", "values", "texts", "source", "serial_status", "serial_ok", "manual",
                      "confirm", "replace", "check", "serial_other")
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


def _page(ctx: Ctx, rows: list[list[K.Button]], key: str) -> tuple[list[list[K.Button]], str]:
    """Длинный список кнопок — страницами по PAGE_SIZE (лимит MAX — 30 рядов) + [Показать ещё] (QA-3).
    → (ряды страницы с кнопкой листания, строка «Показали 1–20 из 30» или "")."""
    if len(rows) <= PAGE_SIZE:
        return rows, ""
    pages = (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE
    page = ctx.data.get(key, 0) % pages
    start = page * PAGE_SIZE
    shown = rows[start:start + PAGE_SIZE]
    last = page == pages - 1
    shown.append([ctx.btn(T.BTN_PAGE_FIRST if last else T.BTN_PAGE_NEXT, "page")])
    return shown, "\n\n" + T.PAGE_NOTE.format(start=start + 1, end=start + len(shown) - 1, total=len(rows))


def _next_page(ctx: Ctx, key: str) -> None:
    ctx.data[key] = ctx.data.get(key, 0) + 1


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
        if row and not row["active"]:  # счётчик удалили (в боте или мини-приложении), пока шла подача
            await drop_scenario(ctx)
            ctx.note(TM.SUBMIT_GONE)
            await show_menu(ctx)
            return None
        await _no_access(ctx, row["address_id"] if row else None)
        return None
    draft = d.get("draft") or {}
    link = await ctx.repo.user_address(_uid(ctx), draft.get("address_id") or 0)
    if not link or not can_submit(link["access"]):
        await _no_access(ctx, draft.get("address_id"))
        return None
    return Meter(None, draft["type"], tariffs_of(draft["type"], draft.get("tariffs")), link["id"],
                 f"{TYPE_LABELS[draft['type']]} · {link['label']}", draft.get("serial"))


def _shown(ctx: Ctx, m: Meter, values: dict | None, f: str, unit: bool = True) -> str:
    """Значение поля для текста: как прочитали/ввели ('2168 кВт·ч'), иначе по формату табло."""
    v = (values or {}).get(f)
    text = (ctx.data.get("texts") or {}).get(f)
    if not text_matches(text, v, m.type):
        return fmt.value(v, m.type, unit=unit)
    return f"{text} {spec(m.type).unit}" if unit else text


def _shown_all(ctx: Ctx, m: Meter, values: dict | None) -> str:
    """'2168 кВт·ч' или для нескольких тарифов '1010 / 505,5 кВт·ч' (как format_values, но без выдуманных знаков)."""
    return f"{' / '.join(_shown(ctx, m, values, f, unit=False) for f in m.fields)} {spec(m.type).unit}"


def _needs_serial(ctx: Ctx, m: Meter) -> bool:
    """Номера нет ни у счётчика, ни на фото (или номер с фото отвергли), и вручную его не ввели."""
    d = ctx.data
    if m.serial or d.get("serial_entered"):
        return False
    return not ((d.get("recognized") or {}).get("serial") and d.get("serial_status") == "new")


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


@on_hook("submission.for_meter")
async def start_for_meter(ctx: Ctx, meter_id: int | None = None, label: str = "", **_) -> None:
    """«Подать показания» из карточки счётчика: инструкция к фото, счётчик уже выбран."""
    await _begin(ctx, "photo")
    ctx.data["meter_id"] = meter_id
    if await _meter(ctx) is None:
        return
    ctx.note(TM.SUBMIT_FOR.format(meter=esc(label)))
    ctx.session.go(S.SUB_AWAIT_PHOTO, await_mode="instruction")
    await ask_photo(ctx)


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
        s.new_flow()  # кнопки экрана со старым фото («Отправить» под старым значением) — устаревшие (QA-5)
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
    if ctx.action == "pick_other":  # «Похоже, это не тот счётчик» — то же фото, другой счётчик
        await _go_pick(ctx)
        return
    if ctx.action == "manual":
        await _manual_from_await(ctx)
    elif ctx.action == "retake":
        await photos.delete_photo(ctx.repo, ctx.data.pop("photo_id", None))
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
    # Фото, оставленное для «Выбрать другой счётчик», при ручном вводе не нужно.
    await photos.delete_photo(ctx.repo, ctx.data.pop("photo_id", None))
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
    for key in ("meter_id", "draft", "serial_entered"):
        ctx.data.pop(key, None)
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
    rows, note = _page(ctx, [[ctx.btn(label, "m", m["id"])]
                             for m, label in zip(meters, meter_labels(meters), strict=True)], "pick_page")
    rows.append(_row(ctx, (T.BTN_NEW_METER, "new"), (C.BTN_CANCEL, "cancel")))
    await ctx.reply((T.PICK_PHOTO if ctx.data.get("photo_id") else T.PICK_MANUAL) + note, K.kb(*rows))


@on_state(S.SUB_PICK_METER, buttons=True)
async def pick_meter(ctx: Ctx) -> None:
    if await _handled_exit(ctx):
        return
    if ctx.action == "m" and (mid := K.parse_id(ctx.arg)):
        ctx.data.pop("draft", None)
        ctx.data["meter_id"] = mid
        await _after_meter(ctx)
    elif ctx.action == "new":
        ctx.session.go(S.SUB_NEW_TYPE)
        await ask_type(ctx)
    else:
        if ctx.action == "page":
            _next_page(ctx, "pick_page")
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
    rows, note = _page(ctx, [[ctx.btn(a["label"], "a", a["id"])]
                             for a in await ctx.repo.user_addresses(_uid(ctx)) if can_submit(a["access"])],
                       "addr_page")
    rows.append(_row(ctx, (T.BTN_OTHER_ADDRESS, "other")))
    rows.append(_row(ctx, (C.BTN_BACK, "back"), (C.BTN_CANCEL, "cancel")))
    await ctx.reply(T.ASK_ADDRESS + note, K.kb(*rows))


@on_state(S.SUB_NEW_ADDRESS, buttons=True)
async def got_address(ctx: Ctx) -> None:
    if await _handled_exit(ctx):
        return
    if ctx.action == "a" and (aid := K.parse_id(ctx.arg)):
        ctx.data.setdefault("draft", {})["address_id"] = aid
        await _after_meter(ctx)
    elif ctx.action == "other":
        ctx.session.go(S.SUB_ADDR_INPUT)
        await ask_new_address(ctx)
    elif ctx.action == "back":
        electricity = (ctx.data.get("draft") or {}).get("type") == MeterType.ELECTRICITY
        ctx.session.go(S.SUB_NEW_TARIFF if electricity else S.SUB_NEW_TYPE)
        await repeat_step(ctx)
    else:
        if ctx.action == "page":
            _next_page(ctx, "addr_page")
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
    if ctx.data.get("addr"):
        ctx.session.new_flow()  # новый поиск: «Да» под прошлыми вариантами устарело (QA-5)
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
        note = f"{T.ADDRESS_LOCAL_NOTE}\n\n" if cands[0].source == "local" else ""
        await ctx.reply(T.ADDRESS_ONE.format(address=esc(cands[0].full_text), notes=note), K.kb(
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
    chosen = (ctx.data.get("addr") or {}).get("chosen") or {}
    await ctx.reply(T.ASK_FLAT.format(address=esc(chosen.get("full_text"))), K.kb(_row(ctx, (T.BTN_PRIVATE_HOUSE, "house")),
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
        return Recognition(confidence=0.0, error=type(e).__name__, issues=[SERVICE])


async def _recognition_failed(ctx: Ctx, m: Meter, rec: Recognition) -> None:
    """«Не получилось распознать»: что не так на фото и что делать. Фото оставляем, только если
    предлагаем выбрать другой счётчик (на фото, похоже, счётчик другого типа)."""
    d = ctx.data
    other_type = "wrong_type" in rec.issues
    if not other_type:
        await photos.delete_photo(ctx.repo, d.pop("photo_id", None))
    for key in RESET_ON_NEW_PHOTO:
        d.pop(key, None)
    ctx.session.go(S.SUB_AWAIT_PHOTO, await_mode="retake")
    rows = [_row(ctx, (T.BTN_RETAKE, "retake"), (T.BTN_MANUAL, "manual"))]
    if other_type:
        rows.append(_row(ctx, (T.BTN_PICK_OTHER, "pick_other")))
    rows.append(_row(ctx, (C.BTN_CANCEL, "cancel")))
    await ctx.reply(T.recognize_failed(rec.issues, rec.note, m.type, need_serial=_needs_serial(ctx, m)),
                    K.kb(*rows))


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
    looking_mid = await ctx.reply(T.LOOKING)
    try:
        rec = await _run_recognizer(ctx, path, m, await _prev_values(ctx, m))
    finally:
        if looking_mid:
            await ctx.delete(looking_mid)
    log.info("recognition %s/%s: readable=%s confidence=%.2f issues=%s serial=%s stub=%s", m.type, m.tariffs,
             rec.readable(m.fields), rec.confidence, rec.issues, bool(rec.serial), rec.stub)
    if not rec.readable(m.fields):
        await _recognition_failed(ctx, m, rec)
        return
    # Год, ГОСТ, Qn и прочее «не номер» с шильдика отбрасываем: не показываем и не сохраняем.
    rec.serial = usable_serial(rec.serial, m.type)
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
                if not rec.readable(m.fields):
                    await _recognition_failed(ctx, m, rec)
                    return
                rec.serial = usable_serial(rec.serial, m.type)
    # Распознанный номер в черновик не пишем: номер нового счётчика сохранится при отправке
    # (readings.submit_reading), а до неё сравнивать новый счётчик не с чем.
    texts = {f: t for f, t in rec.texts.items() if f in m.fields}
    d["recognized"] = {**{f: rec.values.get(f) for f in m.fields}, "texts": texts, "serial": rec.serial,
                       "confidence": rec.confidence, "stub": rec.stub, "issues": rec.issues,
                       "brand": rec.brand, "model": rec.model}
    d["values"] = {f: rec.values.get(f) for f in m.fields}
    d["texts"] = dict(texts)
    d["source"] = "photo"
    d["serial_status"] = await _serial_status(ctx, m, rec.serial)
    if d["serial_status"] in ("mismatch", "other") and not d.get("serial_ok"):
        ctx.session.go(S.SUB_SERIAL_MISMATCH)
        await ask_serial(ctx)
        return
    await _review_recognized(ctx)


def _missing(m: Meter, values: dict | None) -> list[str]:
    """Поля счётчика без значения (пустое показание не показываем и не отправляем)."""
    return [f for f in m.fields if (values or {}).get(f) is None]


async def _review_recognized(ctx: Ctx) -> None:
    """На проверку; многотарифный, где на фото виден не каждый тариф, — спросить только недостающие;
    номера нет ни у счётчика, ни на фото — сначала номер."""
    m = await _meter(ctx)
    if m is None:
        return
    if missing := _missing(m, ctx.data.get("values")):
        await _start_manual_input(ctx, back="photo" if _is_photo_path(ctx) else "pick", only=missing)
        return
    await _to_review(ctx, m)


async def _to_review(ctx: Ctx, m: Meter) -> None:
    """Все значения есть: к проверке, а если номера нет — сначала к номеру."""
    if _needs_serial(ctx, m):
        await _serial_step(ctx)
        return
    ctx.session.go(S.SUB_REVIEW)
    await ask_review(ctx)


async def _serial_status(ctx: Ctx, m: Meter, serial: str | None) -> str | None:
    """'match' | 'mismatch' | 'other' (номер другого счётчика) | 'new' (сохраним) | None (нечего показать).
    Сравнение — только нормализованных номеров: «18-123 456» и «18123456» совпадают."""
    if not normalize_serial(serial):
        return None
    if m.serial:
        return "match" if normalize_serial(serial) == normalize_serial(m.serial) else "mismatch"
    other = await ctx.repo.find_meter_by_serial(m.address_id, serial or "")
    if other and other["id"] != m.id:
        ctx.data["serial_other"] = other["id"]
        return "other"
    return "new"


@on_repeat(S.SUB_SERIAL_MISMATCH)
async def ask_serial(ctx: Ctx) -> None:
    m = await _meter(ctx)
    if m is None:
        return
    photo_serial = format_serial((ctx.data.get("recognized") or {}).get("serial"), m.type) or "—"
    other_id = ctx.data.get("serial_other") if ctx.data.get("serial_status") == "other" else None
    if other_id:
        meters = await ctx.repo.user_meters(_uid(ctx))
        other = next((lb for x, lb in zip(meters, meter_labels(meters), strict=True) if x["id"] == other_id), "")
        text = T.SERIAL_OF_OTHER.format(photo=esc(photo_serial), other=esc(other), label=esc(m.label))
    else:
        text = T.SERIAL_MISMATCH.format(photo=esc(photo_serial), label=esc(m.label),
                                        saved=esc(format_serial(m.serial, m.type) or "—"))
    await ctx.reply(
        text,
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
        await _review_recognized(ctx)  # у счётчика нет номера — спросит его
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


# === Серийный номер: обязателен (на фото не виден, у счётчика не сохранён) ===

def _serial_from_photo(ctx: Ctx) -> bool:
    """Номер ждали с фото (а не отвергли его) — показываем «Не разобрали номер» с [Переснять]."""
    d = ctx.data
    return _is_photo_path(ctx) and d.get("source") == "photo" and d.get("serial_status") != "ignored"


async def _serial_step(ctx: Ctx) -> None:
    """После фото — «Не разобрали номер» [Переснять] [Ввести номер]; без фото (или номер с фото
    отвергли) — сразу ввод номера."""
    if _serial_from_photo(ctx):
        ctx.session.go(S.SUB_SERIAL_MISSING)
        await ask_serial_missing(ctx)
    else:
        ctx.session.go(S.SUB_SERIAL_INPUT)
        await ask_serial_input(ctx)


@on_repeat(S.SUB_SERIAL_MISSING)
async def ask_serial_missing(ctx: Ctx) -> None:
    m = await _meter(ctx)
    if m is None:
        return
    await ctx.reply(f"{esc(m.label)}\n\n{T.SERIAL_MISSING}", K.kb(
        _row(ctx, (T.BTN_RETAKE, "retake"), (T.BTN_SERIAL, "serial")), _row(ctx, (C.BTN_CANCEL, "cancel"))))


@on_state(S.SUB_SERIAL_MISSING)
async def got_serial_missing(ctx: Ctx) -> None:
    if await _handled_exit(ctx):
        return
    if ctx.action == "retake":
        await _retake(ctx)
    elif ctx.action == "serial":
        ctx.session.go(S.SUB_SERIAL_INPUT)
        await ask_serial_input(ctx)
    elif not ctx.is_callback and ctx.text and K.text_alias(ctx.text) is None:  # сразу написали номер
        ctx.session.go(S.SUB_SERIAL_INPUT)
        await _serial_value(ctx, ctx.text)
    else:
        await ask_serial_missing(ctx)


@on_repeat(S.SUB_SERIAL_INPUT)
async def ask_serial_input(ctx: Ctx) -> None:
    m = await _meter(ctx)
    if m is None:
        return
    await ctx.reply(f"{esc(m.label)}\n\n{T.ASK_SERIAL.format(example=T.SERIAL_EXAMPLES[m.type])}",
                    _manual_kb(ctx))


@on_state(S.SUB_SERIAL_INPUT)
async def got_serial_input(ctx: Ctx) -> None:
    if await _handled_exit(ctx):
        return
    if ctx.action == "back" or (not ctx.is_callback and K.text_alias(ctx.text) == "back"):
        if _serial_from_photo(ctx):
            ctx.session.go(S.SUB_SERIAL_MISSING)
            await ask_serial_missing(ctx)
        elif _is_photo_path(ctx):
            await _retake(ctx)
        else:
            await _start_manual_input(ctx, back="pick")
    elif ctx.is_callback or not ctx.text:
        await ask_serial_input(ctx)
    else:
        await _serial_value(ctx, ctx.text)


async def _serial_value(ctx: Ctx, text: str) -> None:
    """Номер, введённый вручную: проверка по типу (domain/serials), номер другого счётчика не берём."""
    m = await _meter(ctx)
    if m is None:
        return
    check = validate_serial(text, m.type)
    if not check.usable:
        tpl = T.SERIAL_ERRORS.get(check.code or "", T.SERIAL_ERRORS["not_serial"])
        await ctx.reply(tpl.format(example=T.SERIAL_EXAMPLES[m.type], typical=T.SERIAL_TYPICAL[m.type]),
                        _manual_kb(ctx))
        return
    serial = clean_serial(text) or ""
    other = await ctx.repo.find_meter_by_serial(m.address_id, serial)
    if other and other["id"] != m.id:
        meters = await ctx.repo.user_meters(_uid(ctx))
        label = next((lb for x, lb in zip(meters, meter_labels(meters), strict=True) if x["id"] == other["id"]), "")
        await ctx.reply(T.SERIAL_TAKEN.format(serial=esc(format_serial(serial, m.type)), other=esc(label)),
                        _manual_kb(ctx))
        return
    ctx.data.update(serial_entered=serial, serial_status="entered")
    await _review_recognized(ctx)


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
    if missing := _missing(m, values):  # пустое «Показание: —» не показываем — спрашиваем недостающее
        await _start_manual_input(ctx, back="photo" if _is_photo_path(ctx) else "pick", only=missing)
        return
    lines = [esc(m.label), ""]
    if len(m.fields) == 1:
        lines.append(T.VALUE_LINE.format(value=_shown(ctx, m, values, "t1")))
    else:
        labels = field_labels(m.type, m.tariffs)
        lines += [T.TARIFF_LINE.format(label=labels[f], value=_shown(ctx, m, values, f)) for f in m.fields]
    status = d.get("serial_status") or ""
    serial = d.get("serial_entered") if status == "entered" else (d.get("recognized") or {}).get("serial")
    ignored = T.SERIAL_IGNORED if m.serial else T.SERIAL_REJECTED
    status_line = {"match": T.SERIAL_MATCH, "new": T.SERIAL_NEW, "entered": T.SERIAL_NEW,
                   "ignored": ignored}.get(status)
    if status_line and serial:
        lines.append(status_line.format(serial=esc(format_serial(serial, m.type))))
        if status in ("new", "entered") and validate_serial(serial, m.type).level == "warning":
            lines.append(T.SERIAL_WARN.format(typical=T.SERIAL_TYPICAL[m.type]))
    elif m.serial and d.get("source") == "photo" and d.get("recognized") and not serial:
        lines.append(T.SERIAL_NOT_ON_PHOTO.format(serial=esc(format_serial(m.serial, m.type))))
    prev = await _prev_values(ctx, m)
    if prev:
        if len(m.fields) == 1 and values.get("t1") is not None:
            lines.append(T.PREV_LINE.format(value=fmt.stored(prev.get("t1"), m.type),
                                            delta=fmt.delta(values["t1"] - prev["t1"], m.type)))
        else:
            lines.append(T.PREV_LINE_MULTI.format(value=format_values(prev, m.type, m.tariffs, stored=True)))
    rec = d.get("recognized") or {}
    if d.get("source") == "photo":
        warnings = T.review_warnings(rec.get("issues") or [], m.type)
        if warnings:  # что именно не так с фото — конкретно, а не общее «проверьте»
            lines += warnings
        elif LOW_CONFIDENCE <= rec.get("confidence", 1) < CHECK_CONFIDENCE:
            lines.append(T.CHECK_DIGITS)
        if rec.get("stub"):
            lines.append(T.STUB_NOTE)
    other_type = d.get("source") == "photo" and "wrong_type" in (rec.get("issues") or []) and _is_photo_path(ctx)
    if other_type:
        lines.append(T.WRONG_TYPE_WARN)
    lines += ["", T.REVIEW_QUESTION]
    second = [(T.BTN_EDIT, "edit")] + ([(T.BTN_RETAKE, "retake")] if _is_photo_path(ctx) else [])
    rows = [_row(ctx, (T.BTN_SEND, "send")), _row(ctx, *second)]
    if other_type:
        rows.append(_row(ctx, (T.BTN_PICK_OTHER, "pick_other")))
    rows.append(_row(ctx, (C.BTN_CANCEL, "cancel")))
    await ctx.reply("\n".join(lines), K.kb(*rows))


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
    elif action == "pick_other" and _is_photo_path(ctx):
        for key in RESET_ON_NEW_PHOTO:
            ctx.data.pop(key, None)
        await _go_pick(ctx)
    elif not ctx.is_callback and looks_like_value(ctx.text):  # B9: число — исправление
        await _start_manual_input(ctx, back="review", typed=ctx.text)
    else:
        await ask_review(ctx)


# === Ручной ввод по полям ===

async def _start_manual_input(ctx: Ctx, *, back: str, typed: str | None = None,
                              only: list[str] | None = None) -> None:
    """Ввод по полям. only — спросить только эти поля (остальные уже распознаны и лежат в values)."""
    vals = {f: v for f, v in (ctx.data.get("values") or {}).items() if v is not None} if only else {}
    texts = {f: t for f, t in (ctx.data.get("texts") or {}).items() if f in vals}
    ctx.data["manual"] = {"idx": 0, "vals": vals, "texts": texts, "back": back, **({"only": only} if only else {})}
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
    asked = _asked(m, man)
    idx = min(man["idx"], len(asked) - 1)
    f = asked[idx]
    example = T.EXAMPLES[m.type]
    labels = field_labels(m.type, m.tariffs)
    if len(m.fields) == 1:
        ask = T.ASK_VALUE.format(example=example)
    elif len(asked) == 1:
        ask = T.ASK_TARIFF_ONLY.format(label=labels[f], example=example)
    else:
        ask = T.ASK_TARIFF_VALUE.format(label=labels[f], n=idx + 1, total=len(asked), example=example)
    lines = [esc(m.label), ""]
    if man.get("only"):
        got = [f"{labels[k]} — {_shown(ctx, m, man['vals'], k)}" for k in man["vals"] if k not in asked]
        if got:
            lines += [T.PARTIAL_GOT.format(got="; ".join(got)), ""]
    lines.append(ask)
    rec = ctx.data.get("recognized") or {}
    if rec.get(f) is not None:
        text = (rec.get("texts") or {}).get(f)
        shown = f"{text} {spec(m.type).unit}" if text_matches(text, rec[f], m.type) else fmt.value(rec[f], m.type)
        lines.append(T.RECOGNIZED_HINT.format(value=shown))
    prev = await _prev_values(ctx, m)
    if prev and prev.get(f) is not None:
        lines.append(T.PREV_HINT.format(value=fmt.stored(prev[f], m.type)))
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


def _asked(m: Meter, man: dict) -> list[str]:
    """Поля, которые спрашиваем вручную: все поля счётчика или только недостающие (only)."""
    return [f for f in m.fields if f in man["only"]] if man.get("only") else list(m.fields)


async def _manual_back(ctx: Ctx) -> None:
    """C4: на первом поле — туда, откуда пришли; иначе — предыдущее поле.
    На проверку возвращаемся, только если все поля заполнены; распознано не всё — к новому фото."""
    man = ctx.data.get("manual") or {"idx": 0, "back": "pick"}
    if man["idx"] > 0:
        man["idx"] -= 1
        await ask_manual(ctx)
        return
    ctx.data.pop("manual", None)
    m = await _meter(ctx)
    if m is None:
        return
    complete = not _missing(m, ctx.data.get("values"))
    if man["back"] == "review" and complete:
        ctx.session.go(S.SUB_REVIEW)
        await ask_review(ctx)
    elif man["back"] in ("photo", "review") and _is_photo_path(ctx):
        await _retake(ctx)
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
    asked = _asked(m, man)
    f = asked[min(man["idx"], len(asked) - 1)]
    try:
        man["vals"][f] = parse_value(text, m.type)
        man.setdefault("texts", {})[f] = typed_text(text)
    except ValueParseError as e:
        sp = spec(m.type)
        await ctx.reply(T.PARSE_ERRORS[e.code].format(example=T.EXAMPLES[m.type], digits=sp.int_digits,
                                                      decimals=sp.frac_digits), _manual_kb(ctx))
        return
    man["idx"] += 1
    if man["idx"] < len(asked):
        await ask_manual(ctx)
        return
    ctx.data["values"] = {f: man["vals"].get(f) for f in m.fields}
    ctx.data["texts"] = {f: t for f, t in (man.get("texts") or {}).items() if f in m.fields}
    ctx.data["source"] = "photo_edited" if ctx.data.get("recognized") else "manual"
    for key in ("manual", "confirm", "replace", "check"):
        ctx.data.pop(key, None)
    await _to_review(ctx, m)


# === Отправка и её вопросы ===

async def _submit(ctx: Ctx) -> None:
    d = ctx.data
    m = await _meter(ctx)
    if m is None:
        return
    if _needs_serial(ctx, m):  # страховка: без номера не отправляем
        await _serial_step(ctx)
        return
    recognized = d.get("recognized")
    if recognized and d.get("serial_status") not in ("match", "new"):
        # Номер на фото пользователь отверг (или он чужой) — счётчику его не присваиваем.
        recognized = {**recognized, "serial": None, "photo_serial": recognized.get("serial")}
    res = await submit_reading(
        ctx.repo, user_id=_uid(ctx), meter_id=m.id, draft=None if m.id else d.get("draft"),
        values=d.get("values") or {}, source=d.get("source") or "manual", recognized=recognized,
        confirm=bool(d.get("confirm")), replace=bool(d.get("replace")), today=ctx.now.date(),
        serial=d.get("serial_entered"),
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
                                       new=_shown_all(ctx, m, values))
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
                                   new=_shown_all(ctx, m, ctx.data.get("values") or {})),
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



async def _done(ctx: Ctx, m: Meter, res: SubmitResult) -> None:
    created = m.id is None and res.meter_id is not None
    if created:  # подпись нового счётчика — с учётом остальных
        meters = await ctx.repo.user_meters(_uid(ctx))
        label = dict(zip([x["id"] for x in meters], meter_labels(meters), strict=True)).get(res.meter_id, m.label)
    else:
        label = m.label
    lines = [T.DONE.format(meter=esc(fmt.meter_of(m.type, label)), month=fmt.month_name(res.period),
                           value=_shown_all(ctx, m, res.values)), T.UK_MOCK]
    if res.status == "flagged":
        lines.append(T.FLAGGED_DONE)
    await drop_scenario(ctx)  # фото удалено, новый flow — кнопки проверки устарели
    meter = await ctx.repo.get_meter(res.meter_id)
    ask_verif_date = created and meter and not meter["verification_due"]
    out = await _arshin_check(ctx, meter)
    url, options = None, []
    if out and out.level == "high":
        lines.append(AS.result_line(out.match.best, out.demo, ctx.now.date()))
        url = card_url(out.match.best.vri_id)
        ask_verif_date = False
    elif out and out.level == "low":
        options = out.match.options
    elif out and out.status in ("found", "none") and ask_verif_date:
        serial = esc(format_serial(meter["serial"], m.type))
        lines += [TA.NOT_FOUND.format(serial=serial)] + ([TA.PICK_DEMO] if out.demo else [])
    if options:
        ctx.session.go(S.SUB_VERIF_DATE, meter_id=res.meter_id, arshin_opts=[r.to_dict() for r in options],
                       arshin_demo=out.demo, arshin_serial=meter["serial"])
    elif ask_verif_date:
        ctx.session.go(S.SUB_VERIF_DATE, meter_id=res.meter_id)
    # Показание уже в БД: сохраняем сессию до ответа. Упадёт отправка — роутер не сохранит сессию,
    # и старое «Отправить» не должно записать показание второй раз (QA-1).
    await save_session(ctx.repo, ctx.session, ctx.now)
    if options:
        await ctx.reply("\n".join(lines) + "\n\n" + _pick_text(ctx, m.type), _pick_kb(ctx))
    elif ask_verif_date:
        await ctx.reply("\n".join(lines) + "\n\n" + T.ASK_VERIF, _verif_kb(ctx))
    else:
        await ctx.reply("\n".join(lines), _after_kb(url))


async def _arshin_check(ctx: Ctx, meter: dict | None) -> AS.Outcome | None:
    """Проверка в ФГИС «Аршин» после первой подачи с номером (не дольше AS.WAIT_SECONDS, потом — в фоне).
    Дату из паспорта (source='user') не трогаем; уже проверенный счётчик обновляет планировщик."""
    arshin = ctx.deps.arshin
    if (arshin is None or not arshin.enabled or not meter or not meter["serial"]
            or meter["verification_source"] == "user" or meter["arshin_checked_at"]):
        return None
    await ctx.typing()
    return await AS.check_within(ctx.deps, meter["id"], ctx.now.date(), ctx.now)


def _after_kb(url: str | None = None) -> dict:
    return K.kb([K.gbtn(C.BTN_MENU, "menu"), K.gbtn(T.BTN_MORE, "submit")], K.link(TA.BTN_CARD, url) if url else None)


def _verif_kb(ctx: Ctx) -> dict:
    return K.kb(_row(ctx, (T.BTN_LATER, "later")))


def _options(ctx: Ctx) -> list[Record]:
    return [Record.from_dict(d) for d in ctx.data.get("arshin_opts") or []]


def _pick_text(ctx: Ctx, meter_type: str) -> str:
    """«Это ваш счётчик?» по записям ФГИС с низкой уверенностью (коллизия номеров, тип не подтверждён)."""
    opts, serial = _options(ctx), esc(format_serial(ctx.data.get("arshin_serial"), meter_type))
    head = (TA.PICK_ONE.format(serial=serial, title=esc(opts[0].mit_title or short_title(opts[0])))
            if len(opts) == 1 else TA.PICK_MANY.format(serial=serial))
    demo = [TA.PICK_DEMO] if ctx.data.get("arshin_demo") else []
    return "\n\n".join([head, *demo, TA.PICK_OR_DATE])


def _pick_kb(ctx: Ctx) -> dict:
    rows = [[ctx.btn(AS.option_text(r), "ar", i)] for i, r in enumerate(_options(ctx))]
    rows.append(_row(ctx, (TA.BTN_NOT_MINE if len(rows) == 1 else TA.BTN_NONE, "ar_none")))
    return K.kb(*rows)


@on_repeat(S.SUB_VERIF_DATE)
async def ask_verif(ctx: Ctx) -> None:
    if ctx.data.get("arshin_opts"):
        meter = await ctx.repo.get_meter(ctx.data.get("meter_id") or 0)
        await ctx.reply(_pick_text(ctx, meter["type"] if meter else ""), _pick_kb(ctx))
        return
    await ctx.reply(T.ASK_VERIF, _verif_kb(ctx))


async def _arshin_pick(ctx: Ctx) -> bool:
    """Кнопки записей ФГИС в SUB_VERIF_DATE. True — событие обработано."""
    opts = _options(ctx)
    if not opts or ctx.action not in ("ar", "ar_none"):
        return False
    if ctx.action == "ar_none" or not ctx.arg.isdigit() or int(ctx.arg) >= len(opts):
        for key in ("arshin_opts", "arshin_demo", "arshin_serial"):
            ctx.data.pop(key, None)
        await ctx.reply(T.ASK_VERIF, _verif_kb(ctx))
        return True
    rec, demo = opts[int(ctx.arg)], bool(ctx.data.get("arshin_demo"))
    await AS.apply_record(ctx.repo, ctx.data["meter_id"], rec, ctx.now)
    await drop_scenario(ctx)
    await ctx.reply(AS.picked_line(rec, demo), _after_kb(card_url(rec.vri_id)))
    return True


@on_state(S.SUB_VERIF_DATE)
async def got_verif(ctx: Ctx) -> None:
    if await _arshin_pick(ctx):
        return
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
        kb = _pick_kb(ctx) if ctx.data.get("arshin_opts") else _verif_kb(ctx)
        await ctx.reply(T.VERIF_ERRORS[e.code], kb)
        return
    await ctx.repo.set_verification(ctx.data["meter_id"], due.isoformat(), "user")
    await drop_scenario(ctx)
    await ctx.reply(T.VERIF_SAVED.format(date=f"{fmt.day_month(due)} {due.year}"), _after_kb())
