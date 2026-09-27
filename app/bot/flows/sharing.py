"""«Поделиться доступом» в боте. Модель и общая с API логика — app/sharing.py.

Собственник: [Поделиться доступом] (g|share|[aid]) → один адрес — ссылка сразу; несколько — выбор кнопками-
переключателями (g|sh_sel|<id.id…> меняет сообщение на месте) → [Готово] (g|sh_go|<id.id…>) → ссылка отдельным
сообщением для пересылки + пояснение. [Общий доступ] (g|sh_list): кем делится (кто подавал показания),
ссылки ждут ответа [Отменить ссылку N] (g|inv_cancel|id), чем поделились с ним. Адрес (g|acc_list|aid):
собственнику — люди, [Закрыть: …] (g|acc_rev → g|acc_rev_ok, access=denied), [Разрешить: …] для запросов;
получателю — «доступ от Анны И.» и [Выйти] (g|sh_rm → g|sh_rm_ok, связь удаляется).
Получатель по ссылке: зарегистрирован — [Принять]/[Отказаться] (g|inv_ok / g|inv_no); нет — регистрация
с токеном в сессии (data["invite"]), адрес необязателен, после «Всё верно» — accept_after_registration.
Диплинки мини-приложения: start=inv_new_<aid> (поделиться), start=inv_acc_<aid> (адрес).
"""
from __future__ import annotations

import logging

from app import sharing as SH
from app.bot import keyboards as K
from app.bot.ctx import Ctx
from app.bot.router import (
    call_hook, cancel_scenario, continue_registration, drop_scenario, on_global, on_hook, show_menu,
)
from app.bot.texts import common as C
from app.bot.texts import hackathon_demo as HT
from app.bot.texts import sharing as T
from app.bot.texts import profile as PT
from app.bot.texts.fmt import b, day_month, esc, with_notes
from app.domain.people import short_name, short_name_gen
from app.integrations.max_api import MaxApiError
from app.repo import Row

log = logging.getLogger(__name__)
SHARE, PICK, GO, SHARED, ADDRESS = "share", "sh_sel", "sh_go", SH.SHARED, "acc_list"
REVOKE, REVOKE_OK, REMOVE, REMOVE_OK, CANCEL = "acc_rev", "acc_rev_ok", "sh_rm", "sh_rm_ok", "inv_cancel"
DECISION_KIND = "access_decision"  # как в profile: запрос доступа уже решён — не дублировать ответ
menu_kb = SH.menu_kb


def _shared_btn() -> K.Button:
    return K.gbtn(T.BTN_SHARED, SHARED)


def _ids(arg: str) -> list[int]:
    """'3.7' → [3, 7] (мусор пропускаем)."""
    return [i for i in (K.parse_id(x) for x in (arg or "").split(".")) if i]


def _arg(ids) -> str:
    return ".".join(str(i) for i in sorted(set(ids)))


async def _bot_username(ctx: Ctx) -> str:
    if not ctx.deps.bot_username and ctx.api is not None:
        try:
            ctx.deps.bot_username = (await ctx.api.get_me()).get("username") or ""
        except MaxApiError as e:
            log.warning("GET /me for share link failed: %s", e)
    return ctx.deps.bot_username


async def _in_place(ctx: Ctx, text: str, keyboard: dict) -> None:
    """Переключатели выбора: меняем то же сообщение (PUT /messages); не вышло — новое сообщение."""
    if ctx.is_callback and ctx.event.message_mid:
        try:
            await ctx.api.edit(ctx.event.message_mid, text, keyboard=keyboard)
            return
        except MaxApiError as e:
            log.info("share picker edit failed: %s", e)
    await ctx.reply(text, keyboard)


# === Собственник: выбор адресов и ссылка ===

def _fit(owned: list[Row]) -> list[Row]:
    """Столько адресов, чтобы payload «все выбраны» уложился в лимит кнопки (на практике — все)."""
    out: list[Row] = []
    for a in owned:
        if len(K.encode(K.GLOBAL, PICK, "")) + len(_arg([x["id"] for x in [*out, a]])) > K.PAYLOAD_MAX_BYTES:
            break
        out.append(a)
    return out


def _picker_kb(owned: list[Row], selected: set[int]) -> dict:
    ids = {a["id"] for a in owned}
    rows = [[K.gbtn(T.BTN_PICKED.format(label=a["label"]) if a["id"] in selected else a["label"],
                    PICK, _arg(selected ^ {a["id"]}) or "-")] for a in owned]
    everything = K.gbtn(T.BTN_ALL, PICK, _arg(ids)) if len(owned) > 1 and selected != ids else None
    return K.kb(*rows, everything, K.gbtn(T.BTN_DONE, GO, _arg(selected) or "-"),
                [K.gbtn(C.BTN_CANCEL, "profile")])


async def _no_owned(ctx: Ctx) -> None:
    received = [a for a in await ctx.repo.user_addresses(ctx.user["id"]) if a["role"] != "owner"]
    await ctx.reply(with_notes(T.NO_OWNED, PT.RIGHTS_MODEL),
                    menu_kb([_shared_btn() if received else None, K.gbtn(PT.BTN_PROFILE, "profile")]))


@on_global("inv_new")  # старая кнопка «Пригласить жильца» в истории чата
@on_global(SHARE)
async def share(ctx: Ctx, aid: int | None = None) -> None:
    """[Поделиться доступом]: один адрес — сразу ссылка; несколько — выбор (aid — отмечен заранее)."""
    owned = _fit(await SH.owned(ctx.repo, ctx.user["id"]))
    aid = aid or K.parse_id(ctx.arg)
    if not owned:
        await _no_owned(ctx)
    elif aid and aid not in {a["id"] for a in owned}:
        await ctx.reply(T.OWNER_ONLY, menu_kb([_shared_btn()]))
    elif len(owned) == 1:
        await _create(ctx, [owned[0]["id"]])
    else:
        await ctx.reply(T.PICK, _picker_kb(owned, {aid} if aid else set()))


@on_global(PICK)
async def toggle(ctx: Ctx) -> None:
    owned = _fit(await SH.owned(ctx.repo, ctx.user["id"]))
    if not owned:
        await _no_owned(ctx)
        return
    selected = set(_ids(ctx.arg)) & {a["id"] for a in owned}
    await _in_place(ctx, T.PICK, _picker_kb(owned, selected))


@on_global(GO)
async def done(ctx: Ctx) -> None:
    owned = _fit(await SH.owned(ctx.repo, ctx.user["id"]))
    selected = set(_ids(ctx.arg)) & {a["id"] for a in owned}
    if not owned:
        await _no_owned(ctx)
    elif not selected:
        await _in_place(ctx, T.PICK_EMPTY + "\n\n" + T.PICK, _picker_kb(owned, selected))
    else:
        await ctx.clear_keyboard()
        await _create(ctx, [a["id"] for a in owned if a["id"] in selected])


async def _create(ctx: Ctx, address_ids: list[int]) -> None:
    uid = ctx.user["id"]
    bot = await _bot_username(ctx)
    if not bot:
        await ctx.reply(T.NO_USERNAME, menu_kb([_shared_btn()]))
        return
    inv, err = await SH.create_invite(ctx.repo, uid, address_ids, ctx.now)
    if err == "limit":
        first = (await ctx.repo.owner_invites(uid, ctx.now))[0]
        await ctx.reply(T.LIMIT.format(count=SH.INVITE_LIMIT, until=day_month(SH.local_date(first["expires_at"]))),
                        menu_kb([_shared_btn()]))
        return
    if err:
        await ctx.reply(T.OWNER_ONLY, menu_kb([_shared_btn()]))
        return
    texts = await SH.invite_texts(ctx.repo, inv, bot)
    # Ссылку — отдельным сообщением без разметки: его удобно переслать, «_» в имени бота не станет курсивом.
    await ctx.api.send(texts["link"], user_id=ctx.event.user_id, fmt=None)
    await ctx.reply(T.CREATED.format(until=texts["until"]), menu_kb([_shared_btn()]), new=True)


# === «Общий доступ» ===

def members_line(members: list[Row]) -> str:
    """Строка профиля собственника: «Доступ: Анна И., Пётр С. (ждёт)» или «Доступ: только вы»."""
    return T.ACCESS_LINE.format(names=_names(members))


def _names(members: list[Row]) -> str:
    names = [esc(short_name(m["full_name"])) + (f" ({T.WAITS})" if m["access"] == "pending" else "")
             for m in members if m["access"] != "denied"]
    return ", ".join(names) or T.ONLY_YOU


def received_role(ua: Row) -> str:
    """«доступ от Анны И.» / «нужно одобрение собственника» / «собственник не открыл доступ»."""
    return SH.role_text(ua) or PT.ROLE.get((ua["role"], ua["access"]), ua["access"])


@on_global(SHARED)
async def show_shared(ctx: Ctx) -> None:
    uid = ctx.user["id"]
    addrs = await ctx.repo.user_addresses(uid)
    owned = [a for a in addrs if a["role"] == "owner"]
    received = [a for a in addrs if a["role"] != "owner"]
    invites = await ctx.repo.owner_invites(uid, ctx.now)
    if not owned and not received:
        await ctx.reply(T.SHARED_EMPTY, menu_kb([K.gbtn(PT.BTN_PROFILE, "profile")]))
        return
    blocks, addr_rows, cancel_rows = [T.SHARED_TITLE], [], []
    if owned:
        lines = [T.SHARED_OWNED]
        for a in owned:
            members = await ctx.repo.address_members(a["id"])
            lines.append(T.SHARED_OWNED_LINE.format(label=esc(a["label"]), names=_names(members)))
            if any(m["access"] != "denied" for m in members):
                addr_rows.append(K.gbtn(a["label"], ADDRESS, a["id"]))
        blocks.append("\n".join(lines))
    if invites:
        labels = {a["id"]: a["label"] for a in owned}
        lines = [T.SHARED_INVITES]
        for n, inv in enumerate(invites, 1):
            names = "; ".join(esc(labels[i]) for i in inv["address_ids"] if i in labels)
            lines.append(T.SHARED_INVITE_LINE.format(n=n, labels=names,
                                                     until=day_month(SH.local_date(inv["expires_at"]))))
            cancel_rows.append(K.gbtn(T.BTN_CANCEL_INVITE.format(n=n), CANCEL, inv["id"]))
        blocks.append("\n".join(lines))
    if received:
        lines = [T.SHARED_RECEIVED]
        for a in received:
            lines.append(T.SHARED_RECEIVED_LINE.format(label=esc(a["label"]), role=received_role(a)))
            addr_rows.append(K.gbtn(a["label"], ADDRESS, a["id"]))
        blocks.append("\n".join(lines))
    if addr_rows:
        managed = len(addr_rows) > len(received)  # есть свои адреса с людьми
        blocks.append(T.SHARED_HINT if managed and received else T.SHARED_HINT_OWNED if managed
                      else T.SHARED_HINT_RECEIVED)
    demo = HT.DEMO_PEOPLE if await ctx.repo.is_hackathon_demo(uid) else None  # ТОЛЬКО ДЛЯ ХАКАТОНА
    await ctx.reply(with_notes("\n\n".join(blocks), demo), menu_kb(
        K.gbtn(T.BTN_SHARE, SHARE) if owned else None, *addr_rows, *cancel_rows, K.gbtn(PT.BTN_PROFILE, "profile")))


@on_global(ADDRESS)
async def show_address(ctx: Ctx, aid: int | None = None) -> None:
    """Адрес: собственнику — кто передаёт показания и кнопки доступа; получателю — от кого доступ."""
    aid = aid or K.parse_id(ctx.arg)
    ua = await ctx.repo.user_address(ctx.user["id"], aid) if aid else None
    if ua is None:
        if aid:
            ctx.note(T.ADDRESS_GONE)
        await show_shared(ctx)
        return
    back = [K.gbtn(C.BTN_BACK, SHARED), K.gbtn(C.BTN_MENU, "menu")]
    if ua["role"] != "owner":
        by = SH.role_text(ua) and SH.sharer(ua)
        text = (T.RECEIVED.format(label=b(ua["full_text"]), by=esc(short_name_gen(by))) if by else
                T.RECEIVED_OTHER.format(label=b(ua["full_text"]), role=received_role(ua)))
        await ctx.reply(text, K.kb(K.gbtn(T.BTN_REMOVE, REMOVE, ua["id"]), back))
        return
    members = [m for m in await ctx.repo.address_members(ua["id"]) if m["access"] != "denied"]
    share_btn = K.gbtn(T.BTN_SHARE, SHARE, ua["id"])
    if not members:
        await ctx.reply(T.MEMBERS_EMPTY.format(label=b(ua["full_text"])), K.kb(share_btn, back))
        return
    lines, rows = [], []
    for n, m in enumerate(members, 1):
        name = short_name(m["full_name"])
        lines.append(T.MEMBER_LINE.format(n=n, name=esc(name), status=SH.member_status(m, ctx.now.date())))
        arg = f"{m['user_id']}.{ua['id']}"
        if m["access"] == "granted":
            rows.append(K.gbtn(_fit_name(T.BTN_REVOKE, T.BTN_REVOKE_N, name, n), REVOKE, arg))
        else:  # запрос доступа — тот же ответ, что на сообщение-запрос (profile.acc_ok)
            rows.append(K.gbtn(_fit_name(T.BTN_ALLOW, T.BTN_ALLOW_N, name, n), "acc_ok", arg))
    await ctx.reply(T.MEMBERS.format(label=b(ua["full_text"]), lines="\n".join(lines)),
                    K.kb(*rows, share_btn, back))


def _fit_name(template: str, fallback: str, name: str, n: int) -> str:
    text = template.format(name=name)
    return text if len(text) <= 24 else fallback.format(n=n)


# --- Собственник закрывает доступ ---

async def _member(ctx: Ctx) -> tuple[Row, Row, Row] | None:
    """(адрес собственника, пользователь, его связь с адресом) по arg «tid.aid»; ошибки — ответом."""
    tid_s, _, aid_s = ctx.arg.partition(".")
    tid, aid = K.parse_id(tid_s), K.parse_id(aid_s)
    ua = await ctx.repo.user_address(ctx.user["id"], aid) if aid else None
    if ua is None or ua["role"] != "owner":
        await ctx.reply(T.OWNER_ONLY, menu_kb([_shared_btn()]))
        return None
    back = menu_kb([K.gbtn(C.BTN_BACK, ADDRESS, ua["id"])])
    if tid == ctx.user["id"]:
        await ctx.reply(T.REVOKE_SELF, back)
        return None
    user = await ctx.repo.get_user_by_id(tid) if tid else None
    member = await ctx.repo.user_address(tid, ua["id"]) if user else None
    if member is None or member["role"] == "owner":
        await ctx.reply(T.REVOKE_GONE, back)
        return None
    if member["access"] == "denied":
        await ctx.reply(T.REVOKE_ALREADY.format(name=esc(short_name(user["full_name"]))), back)
        return None
    return ua, user, member


@on_global(REVOKE)
async def ask_revoke(ctx: Ctx) -> None:
    found = await _member(ctx)
    if found is None:
        return
    ua, user, _ = found
    await ctx.reply(T.REVOKE_ASK.format(name=esc(short_name(user["full_name"])), label=esc(ua["full_text"])), K.kb(
        [K.gbtn(T.BTN_REVOKE_YES, REVOKE_OK, ctx.arg), K.gbtn(T.BTN_REVOKE_NO, ADDRESS, ua["id"])]))


@on_global(REVOKE_OK)
async def revoke(ctx: Ctx) -> None:
    found = await _member(ctx)
    if found is None:
        return
    ua, user, _ = found
    await ctx.clear_keyboard()
    await SH.close_access(ctx.repo, ctx.api, ctx.user["id"], user["id"], ua["id"])
    await ctx.reply(T.REVOKED.format(name=esc(short_name(user["full_name"])), label=esc(ua["full_text"])),
                    menu_kb([K.gbtn(C.BTN_BACK, ADDRESS, ua["id"])]))


# --- Отмена ссылки ---

@on_global(CANCEL)
async def cancel_invite(ctx: Ctx) -> None:
    iid = K.parse_id(ctx.arg)
    inv = await ctx.repo.get_invite_by_id(iid) if iid else None
    if inv is None or inv["owner_user_id"] != ctx.user["id"] or SH.invite_state(inv, ctx.now):
        ctx.note(T.INVITE_CANCEL_GONE)
    else:
        await ctx.repo.cancel_invite(inv["id"], ctx.now)
        labels = [a["label"] for a in await SH.invite_addresses(ctx.repo, inv)]
        ctx.note(T.INVITE_CANCELLED.format(labels=esc("; ".join(labels))))
    await show_shared(ctx)


# --- Получатель убирает адрес у себя ---

@on_global(REMOVE)
async def ask_remove(ctx: Ctx) -> None:
    aid = K.parse_id(ctx.arg)
    ua = await ctx.repo.user_address(ctx.user["id"], aid) if aid else None
    if ua is None:
        await show_address(ctx, aid)
    elif ua["role"] == "owner":
        await ctx.reply(T.REMOVE_OWN, menu_kb([_shared_btn()]))
    else:
        by = SH.sharer(ua) if ua["access"] == "granted" else None
        ask = (T.REMOVE_ASK.format(label=esc(ua["full_text"]), owner=esc(short_name(by))) if by
               else T.REMOVE_ASK_CLOSED.format(label=esc(ua["full_text"])))
        await ctx.reply(ask, K.kb(
            [K.gbtn(T.BTN_REMOVE_YES, REMOVE_OK, aid), K.gbtn(T.BTN_REMOVE_NO, ADDRESS, aid)]))


@on_global(REMOVE_OK)
async def remove(ctx: Ctx) -> None:
    aid = K.parse_id(ctx.arg)
    status, ua = await SH.remove_received(ctx.repo, ctx.api, ctx.user, aid) if aid else ("not_found", None)
    if status == "not_found":
        await show_address(ctx, aid)
        return
    if status == "own":
        await ctx.reply(T.REMOVE_OWN, menu_kb([_shared_btn()]))
        return
    await ctx.clear_keyboard()
    by = SH.sharer(ua) if status == "notified" else None
    text = (T.REMOVED_NOTIFIED.format(label=esc(ua["full_text"]), owner=esc(short_name(by))) if by
            else T.REMOVED.format(label=esc(ua["full_text"])))
    await ctx.reply(text, menu_kb([K.gbtn(PT.BTN_PROFILE, "profile")]))


# === Получатель открыл ссылку ===

async def _plain_start(ctx: Ctx) -> None:
    """Как обычный /start: регистрация, «продолжим» или меню."""
    if ctx.session.state.is_reg:
        await continue_registration(ctx)
    elif not ctx.registered:
        await drop_scenario(ctx)
        await call_hook("registration.begin", ctx)
    else:
        await cancel_scenario(ctx)
        await show_menu(ctx)


@on_hook("invite.start")
async def open_start(ctx: Ctx, arg: str = "", **_) -> None:
    """Диплинк inv_<token> / inv_new_<aid> / inv_acc_<aid>."""
    kind, _, rest = arg.partition("_")
    if kind.lower() in ("new", "acc"):
        if not ctx.registered or ctx.session.state.is_reg:
            await _plain_start(ctx)
            return
        await cancel_scenario(ctx)
        await (share if kind.lower() == "new" else show_address)(ctx, K.parse_id(rest) or -1)
        return
    await open_invite(ctx, arg)


async def _owner_view(ctx: Ctx, inv: Row) -> tuple[str, list[str]]:
    """(«Анна И.», подписи адресов приглашения) — как их видит собственник."""
    owner = await ctx.repo.get_user_by_id(inv["owner_user_id"])
    labels = [a["label"] for a in await SH.invite_addresses(ctx.repo, inv)]
    return short_name(owner["full_name"] if owner else None), labels


async def open_invite(ctx: Ctx, token: str) -> None:
    inv, err = await SH.check_invite(ctx.repo, token, ctx.now)
    if not err and not (await _owner_view(ctx, inv))[1]:
        err = "gone"
    if not ctx.registered:
        await _open_unregistered(ctx, inv, err)
        return
    await cancel_scenario(ctx)
    if err:
        await ctx.reply(T.INVALID[err], menu_kb())
        return
    if inv["owner_user_id"] == ctx.user["id"]:
        await ctx.reply(T.SELF, menu_kb([_shared_btn()]))
        return
    owner, _ = await _owner_view(ctx, inv)
    new, own, has = [], [], []
    for a in await SH.invite_addresses(ctx.repo, inv):
        mine = await ctx.repo.user_address(ctx.user["id"], a["id"])
        (own if mine and mine["role"] == "owner" else has if mine and mine["access"] == "granted" else new).append(
            (mine or a)["label"])
    if not new:
        if has:
            await ctx.reply(T.ALREADY.format(addresses=esc(SH.addresses_text(has))),
                            menu_kb([K.gbtn(PT.BTN_SUBMIT, "submit")]))
        else:
            await ctx.reply(T.INVALID["gone"], menu_kb())
        return
    text = T.OFFER.format(owner=esc(owner), addresses=esc(SH.addresses_text(new)))
    if own:
        text += "\n\n" + "\n".join(T.OFFER_OWN.format(label=esc(label)) for label in own)
    await ctx.reply(text, K.kb([K.gbtn(T.BTN_ACCEPT, "inv_ok", inv["token"]),
                                K.gbtn(T.BTN_DECLINE, "inv_no", inv["token"])]))


async def _open_unregistered(ctx: Ctx, inv: Row | None, err: str | None) -> None:
    """Незарегистрированный: «Анна И. хочет открыть вам доступ… Сначала познакомимся» → регистрация."""
    if ctx.session.state.is_reg:
        ctx.note(T.INVALID[err] if err else T.REG_KEPT)
    else:
        await drop_scenario(ctx)
        if err:
            ctx.note(T.INVALID[err])
        else:
            owner, labels = await _owner_view(ctx, inv)
            ctx.note(T.REG_INVITED.format(owner=esc(owner), addresses=esc(SH.addresses_text(labels))))
    if not err:
        ctx.data["invite"] = inv["token"]  # registration.begin его сохраняет
    if ctx.session.state.is_reg:
        await continue_registration(ctx)
    else:
        await call_hook("registration.begin", ctx)


@on_global("inv_ok")
async def accept(ctx: Ctx) -> None:
    await ctx.clear_keyboard()
    inv, err = await SH.check_invite(ctx.repo, ctx.arg, ctx.now)
    if err:
        await ctx.reply(T.INVALID[err], menu_kb())
        return
    if inv["owner_user_id"] == ctx.user["id"]:
        await ctx.reply(T.SELF, menu_kb([_shared_btn()]))
        return
    res = await use_invite(ctx, inv)
    if res is None:
        await ctx.reply(T.INVALID["used"], menu_kb())
    elif res["granted"]:
        await ctx.reply(T.ACCEPTED.format(by=esc(res["by"]), addresses=esc(res["addresses"])),
                        menu_kb([K.gbtn(PT.BTN_SUBMIT, "submit")]))
    elif res["has"]:
        labels = [(await ctx.repo.user_address(ctx.user["id"], aid))["label"] for aid in res["has"]]
        await ctx.reply(T.ALREADY.format(addresses=esc(SH.addresses_text(labels))),
                        menu_kb([K.gbtn(PT.BTN_SUBMIT, "submit")]))
    else:
        await ctx.reply(T.INVALID["gone"], menu_kb())


@on_global("inv_no")
async def decline(ctx: Ctx) -> None:
    await ctx.clear_keyboard()
    await ctx.reply(T.DECLINED, menu_kb())


async def use_invite(ctx: Ctx, inv: Row) -> Row | None:
    """Принять приглашение (repo.use_invite) и сообщить собственнику. → результат repo + by («Анны И.»),
    addresses («адресу …» глазами получателя); None — уже не действует."""
    u = ctx.user
    res = await ctx.repo.use_invite(inv["token"], u["id"], ctx.now)
    if res is None or not res["granted"]:
        return res
    for aid in res["granted"]:  # запрос доступа по этим адресам уже решён — «Разрешить» не пришлёт второй ответ
        await ctx.repo.try_mark_sent(u["id"], DECISION_KIND, f"{u['id']}:{aid}", ctx.now)
    owner = await ctx.repo.get_user_by_id(inv["owner_user_id"])
    owner_labels = [(await ctx.repo.user_address(owner["id"], aid))["label"] for aid in res["granted"]]
    await SH.notify(ctx.api, owner, T.OWNER_ACCEPTED.format(
        name=esc(short_name(u["full_name"])), addresses=esc(SH.addresses_text(owner_labels))),
        menu_kb([_shared_btn()]))
    mine = [(await ctx.repo.user_address(u["id"], aid))["label"] for aid in res["granted"]]
    return {**res, "by": short_name_gen(owner["full_name"]), "addresses": SH.addresses_text(mine)}


# === Регистрация по ссылке (вызывает flows/registration.py) ===

async def pending_invite(ctx: Ctx) -> tuple[str, list[str]] | None:
    """Действующая ссылка из сессии незарегистрированного → («Анны И.», подписи адресов) или None."""
    if not ctx.data.get("invite"):
        return None
    inv, err = await SH.check_invite(ctx.repo, ctx.data["invite"], ctx.now)
    if err:
        return None
    owner = await ctx.repo.get_user_by_id(inv["owner_user_id"])
    labels = [a["label"] for a in await SH.invite_addresses(ctx.repo, inv)]
    return (short_name_gen(owner["full_name"]), labels) if labels else None


async def accept_after_registration(ctx: Ctx) -> tuple[bool, str | None]:
    """После «Всё верно» с токеном в сессии: (открыт ли доступ, строка для сообщения или None)."""
    if not ctx.data.get("invite"):
        return False, None
    inv, err = await SH.check_invite(ctx.repo, ctx.data["invite"], ctx.now)
    res = await use_invite(ctx, inv) if not err and inv["owner_user_id"] != ctx.user["id"] else None
    if res and res["granted"]:
        return True, T.REG_ACCEPTED.format(by=esc(res["by"]), addresses=esc(res["addresses"]))
    if res and res["has"]:
        return True, None
    return False, T.REG_NOT_APPLIED
