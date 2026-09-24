"""Регистрация (SPEC §5.5): приветствие → ФИО → телефон → адрес → подтверждение.

Шаги адреса (ввод → вариант → квартира) общие с профилем: класс AddressFlow.
Данные сессии: {reg: {name, phone, phone_verified, address, raw_address, editing}, addr: {...}, pending_photo_id}.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.bot import keyboards as K
from app.bot import photos
from app.bot.ctx import Ctx
from app.bot.router import call_hook, on_hook, on_repeat, on_state, repeat_step, show_menu
from app.bot.states import S
from app.bot.texts import common as C
from app.bot.texts import registration as T
from app.bot.texts.fmt import esc, flats, format_phone
from app.db import ts
from app.domain.addresses import AddressCandidate, button_text, house_short, norm_key
from app.domain.people import first_name, normalize_name, normalize_phone, validate_name
from app.integrations.address_service import AddressService

_LOCAL_ADDRESSES = AddressService()  # если сервис не подключён — локальный разбор
_FLAT = re.compile(r"^(?:кв|квартира)?\.?\s*№?\s*(\d{1,5}[а-яa-z]?)$", re.IGNORECASE)


# === Общие помощники (их использует и профиль) ===

def phone_line(phone: str | None, verified: bool | int | None) -> str:
    """'+7 912 345-67-89 (номер из MAX)'."""
    s = format_phone(phone)
    return f"{s} {T.FROM_MAX}" if verified else s


def address_service(ctx: Ctx) -> AddressService:
    return ctx.deps.addresses or _LOCAL_ADDRESSES


def parse_phone(ctx: Ctx) -> tuple[str | None, bool, str | None]:
    """Телефон из контакта или текста → (номер, из MAX ли он, текст ошибки).
    Контакт без max_info — как ручной ввод (verified=0); чужой контакт — ошибка."""
    ev = ctx.event
    if ev.kind == "contact":
        if ev.contact_owner_id is not None and ev.contact_owner_id != ev.user_id:
            return None, False, T.NOT_YOUR_CONTACT
        if not ev.contact_phone:
            return None, False, T.CONTACT_NO_PHONE
        return ev.contact_phone, ev.contact_owner_id is not None, None
    phone = normalize_phone(ctx.text)
    if phone:
        return phone, False, None
    foreign = ctx.text.startswith("+") and not ctx.text.startswith("+7")
    return None, False, T.PHONE_FOREIGN if foreign else T.PHONE_ERROR


def said(ctx: Ctx) -> str | None:
    """Действие кнопки или вручную набранное «да/нет/назад»."""
    return ctx.action if ctx.is_callback else K.text_alias(ctx.text)


def address_notes(ctx: Ctx, c: AddressCandidate, shown: list[str] | tuple = ()) -> list[str]:
    """Пометки к адресу, кроме уже показанных (`shown`): не сверен с ФИАС (демо без справочника;
    «сохранить как есть» говорит об этом сам); квартира больше, чем в доме по ФИАС (C12)."""
    notes = []
    if c.source == "local" and not address_service(ctx).verified:
        notes.append(T.LOCAL_NOTE)
    digits = re.match(r"\d+", c.flat or "")
    if c.house_flat_count and digits and int(digits.group()) > c.house_flat_count:
        notes.append(T.FLAT_WARNING.format(flats=flats(c.house_flat_count)))
    return [n for n in notes if n not in shown]


def _with_notes(text: str, notes: list[str]) -> str:
    return "\n\n".join([text, *notes])


def notes_block(notes: list[str]) -> str:
    """Пометки перед вопросом «Верно?»: 'пометка\n\n' или ''."""
    return "".join(f"{n}\n\n" for n in notes)


def _house(c: AddressCandidate) -> str:
    return re.sub(r"\W", "", house_short(c).lower())


def best_candidate(cands: list[AddressCandidate], parsed: list[AddressCandidate]) -> AddressCandidate | None:
    """Один кандидат — он; иначе тот единственный, чей дом совпал с разобранным из текста."""
    if len(cands) == 1:
        return cands[0]
    if not parsed or not parsed[0].house:
        return None
    same = [c for c in cands if _house(c) == _house(parsed[0])]
    return same[0] if len(same) == 1 else None


def parse_flat(text: str) -> str | None:
    m = _FLAT.match(text.strip())
    return m.group(1).upper() if m and int(re.match(r"\d+", m.group(1)).group()) > 0 else None


async def photo_alive(ctx: Ctx, photo_id: str) -> bool:
    """Фото ещё хранится (не истекло, файл на месте); иначе удаляем остатки."""
    row = await ctx.repo.get_photo(photo_id)
    if row and row["expires_at"] >= ts(ctx.now) and Path(row["path"]).exists():
        return True
    await photos.delete_photo(ctx.repo, photo_id)
    return False


# === Шаги адреса ===

Chosen = Callable[[Ctx, AddressCandidate], Awaitable[None]]
Step = Callable[[Ctx], Awaitable[None]]


class AddressFlow:
    """Ввод адреса → вариант («Мы поняли так» / список) → квартира. Черновик — в data['addr']."""

    def __init__(self, input: S, pick: S, flat: S, *, ask: str, back_text: str,
                 on_chosen: Chosen, on_back: Step):
        self.input, self.pick, self.flat = input, pick, flat
        self.ask_text, self.back_text = ask, back_text
        self.on_chosen, self.on_back = on_chosen, on_back
        on_repeat(input)(self.ask)
        on_state(input)(self.got_input)
        on_repeat(pick)(self.ask_pick)
        on_state(pick)(self.got_pick)
        on_repeat(flat)(self.ask_flat)
        on_state(flat)(self.got_flat)

    @staticmethod
    def draft(ctx: Ctx) -> dict:
        return ctx.data.setdefault("addr", {})

    def _back(self, ctx: Ctx) -> K.Button:
        return ctx.btn(self.back_text, "back")

    async def start(self, ctx: Ctx) -> None:
        ctx.data.pop("addr", None)
        await self.ask(ctx)

    async def ask(self, ctx: Ctx, text: str | None = None) -> None:
        ctx.session.go(self.input)
        await ctx.reply(text or self.ask_text, K.kb([self._back(ctx)]))

    async def got_input(self, ctx: Ctx) -> None:
        act, d = said(ctx), self.draft(ctx)
        if act == "back":
            await self.on_back(ctx)
        elif act == "fix":
            await self.ask(ctx, T.ADDRESS_RETRY)
        elif act == "asis" and d.get("asis"):
            await self.choose(ctx, AddressCandidate.from_dict(d["asis"]))
        elif ctx.is_callback or not ctx.text:
            await self.ask(ctx)
        else:
            await self.search(ctx, ctx.text)

    async def search(self, ctx: Ctx, text: str) -> None:
        if ctx.data.get("addr"):
            ctx.session.new_flow()  # новый поиск: «Да» под прошлыми вариантами устарело (QA-5)
        svc = address_service(ctx)
        cands = await svc.suggest(text)
        d = ctx.data["addr"] = {"raw": text}
        if not cands:
            asis = [c for c in svc.parse_local(text) if c.house] if svc.verified else []
            ctx.session.go(self.input)
            if not asis:
                await ctx.reply(T.ADDRESS_NOT_FOUND, K.kb([self._back(ctx)]))
                return
            d["asis"] = asis[0].to_dict()
            await ctx.reply(T.ADDRESS_NOT_FOUND_ASIS.format(address=esc(asis[0].full_text)), K.kb(
                [ctx.btn(T.BTN_ASIS, "asis"), ctx.btn(T.BTN_FIX, "fix")], [self._back(ctx)]))
            return
        best = best_candidate(cands, svc.parse_local(text) if len(cands) > 1 else [])
        d["cands"] = [c.to_dict() for c in ([best] if best else cands)]
        await self.ask_pick(ctx)

    async def ask_pick(self, ctx: Ctx) -> None:
        cands = [AddressCandidate.from_dict(c) for c in self.draft(ctx).get("cands", [])]
        if not cands:
            await self.ask(ctx)
            return
        ctx.session.go(self.pick)
        if len(cands) == 1:
            c = cands[0]
            notes = self.draft(ctx)["shown"] = address_notes(ctx, c)
            await ctx.reply(
                T.ADDRESS_ONE.format(address=esc(c.full_text), notes=notes_block(notes)),
                K.kb([ctx.btn(T.BTN_YES, "yes")], [ctx.btn(T.BTN_NO_OTHER, "no")], [self._back(ctx)]),
            )
            return
        rows = [[ctx.btn(button_text(c), "pick", i)] for i, c in enumerate(cands)]
        await ctx.reply(T.ADDRESS_MANY, K.kb(*rows, [ctx.btn(T.BTN_NOT_MINE, "no")], [self._back(ctx)]))

    async def got_pick(self, ctx: Ctx) -> None:
        act = said(ctx)
        cands = self.draft(ctx).get("cands", [])
        if not ctx.is_callback and act is None and ctx.text:
            await self.search(ctx, ctx.text)  # текст на шаге выбора — новый поиск (B9)
        elif act == "back":
            await self.on_back(ctx)
        elif act == "no":
            await self.ask(ctx, T.ADDRESS_RETRY)
        elif act == "yes" and len(cands) == 1:
            await self.choose(ctx, AddressCandidate.from_dict(cands[0]))
        elif act == "pick" and ctx.arg.isdigit() and int(ctx.arg) < len(cands):
            await self.choose(ctx, AddressCandidate.from_dict(cands[int(ctx.arg)]))
        else:
            await self.ask_pick(ctx)

    async def choose(self, ctx: Ctx, c: AddressCandidate) -> None:
        if c.flat:
            await self._done(ctx, c)
            return
        self.draft(ctx)["chosen"] = c.to_dict()
        await self.ask_flat(ctx)

    async def ask_flat(self, ctx: Ctx) -> None:
        chosen = self.draft(ctx).get("chosen")
        if not chosen:
            await self.ask(ctx)
            return
        ctx.session.go(self.flat)
        await ctx.reply(
            T.ASK_FLAT.format(address=esc(chosen["full_text"])),
            K.kb([ctx.btn(T.BTN_PRIVATE_HOUSE, "private")], [ctx.btn(C.BTN_BACK, "back")]),
        )

    async def got_flat(self, ctx: Ctx) -> None:
        act, d = said(ctx), self.draft(ctx)
        chosen = d.get("chosen")
        if not chosen:
            await self.ask(ctx)
        elif act == "back":
            await self.ask_pick(ctx)
        elif act == "private":
            await self._done(ctx, AddressCandidate.from_dict(chosen))
        elif ctx.is_callback:
            await self.ask_flat(ctx)
        elif flat := parse_flat(ctx.text):
            await self._done(ctx, AddressCandidate.from_dict(chosen).with_flat(flat))
        else:
            await ctx.reply(T.FLAT_ERROR, K.kb(
                [ctx.btn(T.BTN_PRIVATE_HOUSE, "private")], [ctx.btn(C.BTN_BACK, "back")]))

    async def _done(self, ctx: Ctx, c: AddressCandidate) -> None:
        d = ctx.data.pop("addr", {})
        ctx.data["addr_raw"] = d.get("raw")
        ctx.data["addr_shown"] = d.get("shown", []) if len(d.get("cands", [])) == 1 else []
        await self.on_chosen(ctx, c)


# === Вход и имя ===

@on_hook("registration.begin")
async def start_registration(ctx: Ctx) -> None:
    """Приветствие (§8 эталон 1) и вопрос ФИО отдельным сообщением; отложенное фото сохраняем."""
    pending = ctx.data.get("pending_photo_id")
    ctx.session.reset()
    ctx.session.go(S.REG_NAME)
    if pending:
        ctx.data["pending_photo_id"] = pending
    await ctx.reply(C.WELCOME)
    await ask_name(ctx)


def _reg(ctx: Ctx) -> dict:
    return ctx.data.setdefault("reg", {})


def _max_name(ctx: Ctx) -> tuple[str, str] | None:
    """Имя из профиля MAX, если оно проходит проверку ФИО → (для записи, для кнопки)."""
    first = (ctx.event.first_name or "").strip()
    last = (ctx.event.last_name or "").strip()
    check = validate_name(f"{last} {first}")
    if check.error:
        return None
    first_n, full_shown = normalize_name(first), normalize_name(f"{first} {last}")
    for shown in (full_shown, first_n):
        text = T.BTN_ITS_ME.format(name=shown)
        if len(text) <= 24:
            return check.value, text
    return None


def _name_kb(ctx: Ctx) -> dict | None:
    reg = _reg(ctx)
    rows = []
    me = _max_name(ctx)
    if me and me[0] != reg.get("name"):
        rows.append(ctx.btn(me[1], "me"))
    if reg.get("name"):
        rows.append(ctx.btn(T.BTN_KEEP, "keep"))
    if reg.get("editing"):
        rows.append(ctx.btn(C.BTN_BACK, "back"))
    return K.kb(*rows) if rows else None


@on_repeat(S.REG_NAME)
async def ask_name(ctx: Ctx) -> None:
    name = _reg(ctx).get("name")
    await ctx.reply(T.ASK_NAME_AGAIN.format(name=esc(name)) if name else T.ASK_NAME, _name_kb(ctx))


@on_state(S.REG_NAME)
async def got_name(ctx: Ctx) -> None:
    reg, act = _reg(ctx), said(ctx)
    if act == "back":
        await (to_confirm(ctx) if reg.get("editing") else ask_name(ctx))
        return
    if act == "me" and (me := _max_name(ctx)):
        reg["name"] = me[0]
    elif act == "keep" and reg.get("name"):
        pass
    elif ctx.is_callback:
        await ask_name(ctx)
        return
    else:
        check = validate_name(ctx.text)
        if check.error:
            await ctx.reply(T.NAME_ERRORS.get(check.error, T.NAME_ERROR_DEFAULT), _name_kb(ctx))
            return
        reg["name"] = check.value
    if reg.get("editing"):
        await to_confirm(ctx)
    else:
        ctx.session.go(S.REG_PHONE)
        await ask_phone(ctx)


# === Телефон ===

def _phone_kb(ctx: Ctx) -> dict:
    keep = ctx.btn(T.BTN_KEEP, "keep") if _reg(ctx).get("phone") else None
    return K.kb([K.request_contact(T.BTN_SHARE_PHONE)], [keep], [ctx.btn(C.BTN_BACK, "back")])


@on_repeat(S.REG_PHONE)
async def ask_phone(ctx: Ctx) -> None:
    reg = _reg(ctx)
    if reg.get("phone"):
        text = T.PHONE_AGAIN.format(phone=phone_line(reg["phone"], reg.get("phone_verified")))
    else:
        text = T.ASK_PHONE.format(name=esc(first_name(reg.get("name"))))
    await ctx.reply(text, _phone_kb(ctx))


@on_state(S.REG_PHONE, accepts=("contact",))
async def got_phone(ctx: Ctx) -> None:
    reg, act = _reg(ctx), said(ctx)
    if act == "back":
        if reg.get("editing"):
            await to_confirm(ctx)
        else:
            ctx.session.go(S.REG_NAME)
            await ask_name(ctx)
        return
    if act == "keep" and reg.get("phone"):
        pass
    elif ctx.is_callback:
        await ask_phone(ctx)
        return
    else:
        phone, verified, error = parse_phone(ctx)
        if error:
            await ctx.reply(error, _phone_kb(ctx))
            return
        reg.update(phone=phone, phone_verified=verified)
    if reg.get("editing"):
        await to_confirm(ctx)
    else:
        await REG_ADDRESS.start(ctx)


# === Адрес ===

async def _address_back(ctx: Ctx) -> None:
    ctx.data.pop("addr", None)
    if _reg(ctx).get("editing"):
        await to_confirm(ctx)
    else:
        ctx.session.go(S.REG_PHONE)
        await ask_phone(ctx)


async def _address_chosen(ctx: Ctx, c: AddressCandidate) -> None:
    reg = _reg(ctx)
    reg["address"] = c.to_dict()
    reg["raw_address"] = ctx.data.pop("addr_raw", None)
    reg["notes_shown"] = ctx.data.pop("addr_shown", [])
    await to_confirm(ctx)


REG_ADDRESS = AddressFlow(
    S.REG_ADDRESS, S.REG_ADDRESS_PICK, S.REG_FLAT, ask=T.ASK_ADDRESS, back_text=C.BTN_BACK,
    on_chosen=_address_chosen, on_back=_address_back,
)


# === Подтверждение ===

def _missing_step(reg: dict) -> S | None:
    for key, state in (("name", S.REG_NAME), ("phone", S.REG_PHONE), ("address", S.REG_ADDRESS)):
        if not reg.get(key):
            return state
    return None


def _summary(ctx: Ctx, template: str, notes: bool = True) -> str:
    """Сводка регистрации; пометки к адресу — только те, что ещё не показывали на «Мы поняли так»."""
    reg = _reg(ctx)
    c = AddressCandidate.from_dict(reg["address"])
    text = template.format(name=esc(reg["name"]), phone=phone_line(reg["phone"], reg.get("phone_verified")),
                           address=esc(c.full_text))
    return _with_notes(text, address_notes(ctx, c, reg.get("notes_shown", ())) if notes else [])


async def to_confirm(ctx: Ctx) -> None:
    _reg(ctx).pop("editing", None)
    ctx.session.go(S.REG_CONFIRM)
    await ask_confirm(ctx)


@on_repeat(S.REG_CONFIRM)
async def ask_confirm(ctx: Ctx) -> None:
    missing = _missing_step(_reg(ctx))
    if missing:  # черновик неполный (битая сессия) — идём к недостающему шагу
        ctx.session.go(missing)
        await repeat_step(ctx)
        return
    await ctx.reply(_summary(ctx, T.CONFIRM), K.kb(
        [ctx.btn(T.BTN_ALL_OK, "yes")],
        [ctx.btn(T.BTN_EDIT_NAME, "edit_name"), ctx.btn(T.BTN_EDIT_PHONE, "edit_phone")],
        [ctx.btn(T.BTN_EDIT_ADDRESS, "edit_address")],
    ))


@on_state(S.REG_CONFIRM, buttons=True)
async def got_confirm(ctx: Ctx) -> None:
    reg = _reg(ctx)
    if ctx.action == "yes":
        await finish(ctx)
        return
    edit = {"edit_name": S.REG_NAME, "edit_phone": S.REG_PHONE, "edit_address": S.REG_ADDRESS}.get(ctx.action or "")
    if edit is None:
        await ask_confirm(ctx)
        return
    reg["editing"] = True
    if edit == S.REG_ADDRESS:
        await REG_ADDRESS.start(ctx)
    else:
        ctx.session.go(edit)
        await repeat_step(ctx)


async def finish(ctx: Ctx) -> None:
    """«Всё верно»: одна транзакция в БД, затем «нет прав» / подача отложенного фото / меню с шапкой."""
    reg = _reg(ctx)
    missing = _missing_step(reg)
    if missing:
        ctx.session.go(missing)
        await repeat_step(ctx)
        return
    c = AddressCandidate.from_dict(reg["address"])
    res = await ctx.repo.complete_registration(
        ctx.user["id"], full_name=reg["name"], phone=reg["phone"], phone_verified=bool(reg.get("phone_verified")),
        address=c.to_dict(), norm_key=norm_key(c), raw_input=reg.get("raw_address"), now=ctx.now,
    )
    ctx.user = await ctx.repo.get_user_by_id(ctx.user["id"])
    if ctx.is_callback:  # сводка остаётся в чате без кнопок
        await ctx.reply(_summary(ctx, T.SAVED, notes=False))
    pending = ctx.data.get("pending_photo_id")
    ctx.session.reset()
    if res["access"] != "granted":
        await photos.delete_photo(ctx.repo, pending)
        ctx.note(T.DONE_SHORT)
        await call_hook("access.no_access", ctx, address_id=res["address_id"])
        await show_menu(ctx)
        return
    if pending and await photo_alive(ctx, pending):
        ctx.note(T.DONE_PHOTO)
        await call_hook("submission.with_photo", ctx, photo_id=pending)
        return
    ctx.note(T.DONE_PHOTO_EXPIRED if pending else T.DONE)
    await show_menu(ctx)
