"""API «Поделиться доступом» (/api/shares*, /api/me.addresses): контракт, права, уведомления в чат."""
from __future__ import annotations

import dataclasses
import re
from datetime import timedelta
from unittest.mock import ANY

from app import clock
from app.bot.events import parse_update
from app.bot.router import Router
from app.bot.texts import invite as IT
from app.bot.texts.api import SHARE_MSG
from app.domain.addresses import norm_key
from app.integrations.address_service import AddressService
from tests import fakes
from tests.test_api import (  # noqa: F401 — фикстуры client/make_client
    OTHER, TENANT, UID, add_meter, assert_error, auth, client, make_client, register, repo, run,
)


def share(client, address_ids, uid: int = UID):
    return client.post("/api/shares/invites", json={"address_ids": address_ids}, headers=auth(uid))


def accept(client, token: str, uid: int) -> dict:
    """Получатель принимает ссылку (как [Принять] в боте)."""
    user = run(client, repo(client).get_user, uid)
    return run(client, repo(client).use_invite, token, user["id"], clock.now())


def sent_to(api, uid: int) -> list[str]:
    return [kw["text"] for kw in api.named("send") if kw["user_id"] == uid]


def test_shares_contract_and_invite_lifecycle(client):
    u = register(client)
    assert client.get("/api/shares", headers=auth()).json() == {
        "owned": [{"address_id": u["address_id"], "label": u["label"], "full_text": ANY, "members": []}],
        "invites": [], "received": [], "limits": {"active_invites": 5}}
    resp = share(client, [u["address_id"]])
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body) == {"id", "url", "expires_at", "share_text"}
    assert re.fullmatch(r"https://max\.ru/test_bot\?start=inv_[a-z0-9]{20}", body["url"])
    assert body["expires_at"] == "2026-10-26" and "до 26 октября" in body["share_text"]
    assert body["share_text"].startswith("Анна И. открывает вам доступ к адресу") and "http" not in body["share_text"]
    (inv,) = client.get("/api/shares", headers=auth()).json()["invites"]
    assert inv == {"id": body["id"], "addresses": [{"address_id": u["address_id"], "label": u["label"]}],
                   "expires_at": "2026-10-26", "url": body["url"]}
    (a,) = client.get("/api/me", headers=auth()).json()["addresses"]
    assert (a["invites_count"], a["shared_count"], a["owner"], a["owner_gen"]) == (1, 0, None, None)
    assert client.delete(f"/api/shares/invites/{body['id']}", headers=auth()).json() == {
        "status": "cancelled", "id": body["id"]}
    assert client.get("/api/shares", headers=auth()).json()["invites"] == []
    assert client.get("/api/me", headers=auth()).json()["addresses"][0]["invites_count"] == 0


def test_invite_errors_and_limit(client):
    u = register(client)
    other = register(client, OTHER, flat="40")
    assert_error(share(client, []), 422, "empty")
    assert_error(share(client, [other["address_id"]]), 403, "not_owner")
    assert_error(share(client, [u["address_id"], 999]), 403, "not_owner")
    assert_error(client.post("/api/shares/invites", json={"address_ids": "x"}, headers=auth()), 422, "bad_request")
    for _ in range(5):
        assert share(client, [u["address_id"]]).status_code == 201
    resp = share(client, [u["address_id"]])
    assert_error(resp, 409, "limit")
    assert resp.json()["message"] == SHARE_MSG["limit"].format(count=5)
    assert_error(client.get("/api/shares", headers=auth(777999)), 403, "no_access")  # без профиля
    assert client.get("/api/shares").status_code == 401
    mine = client.get("/api/shares", headers=auth()).json()["invites"][0]["id"]
    assert_error(client.delete(f"/api/shares/invites/{mine}", headers=auth(OTHER)), 404, "not_found")
    assert_error(client.delete("/api/shares/invites/abc", headers=auth()), 404, "not_found")
    token = run(client, repo(client).get_invite_by_id, mine)["token"]
    accept(client, token, OTHER)
    assert_error(client.delete(f"/api/shares/invites/{mine}", headers=auth()), 409, "invite_used")


def test_members_received_close_and_remove(client, api):
    owner = register(client)
    tenant = register(client, TENANT, flat="77")  # свой адрес — собственник
    run(client, repo(client).update_user, tenant["id"], full_name="Петров Пётр Иванович")
    mid = add_meter(client, owner["address_id"])
    token = run(client, repo(client).get_invite_by_id, share(client, [owner["address_id"]]).json()["id"])["token"]
    assert accept(client, token, TENANT)["granted"] == [owner["address_id"]]

    (owned,) = client.get("/api/shares", headers=auth()).json()["owned"]
    assert owned["members"] == [{"member_id": tenant["id"], "name": "Пётр П.", "since": "2026-10-19",
                                 "last_submitted_at": None, "submitted_this_period": False}]
    run(client, repo(client).add_reading, mid, tenant["id"], "2026-10", {"t1": 5000, "t2": None, "t3": None}, "manual")
    (m,) = client.get("/api/shares", headers=auth()).json()["owned"][0]["members"]
    assert (m["last_submitted_at"], m["submitted_this_period"]) == ("2026-10-19", True)
    me = client.get("/api/me", headers=auth()).json()
    assert me["addresses"][0]["shared_count"] == 1
    assert me["meters"][0]["last"]["by"] == "Пётр П."  # подал не он сам
    assert client.get(f"/api/meters/{mid}", headers=auth()).json()["history"][0]["by"] == "Пётр П."

    shares = client.get("/api/shares", headers=auth(TENANT)).json()
    assert shares["received"] == [{"address_id": owner["address_id"], "label": ANY, "full_text": ANY,
                                   "owner": "Анна И.", "owner_gen": "Анны И.", "since": "2026-10-19"}]
    assert [a["address_id"] for a in shares["owned"]] == [tenant["address_id"]]
    shared = next(a for a in client.get("/api/me", headers=auth(TENANT)).json()["addresses"]
                  if a["id"] == owner["address_id"])
    assert (shared["role"], shared["access"], shared["owner"], shared["owner_gen"]) == (
        "tenant", "granted", "Анна И.", "Анны И.")
    assert "members" not in shared and shared["shared_count"] == 0
    tm = client.get(f"/api/meters/{mid}", headers=auth(TENANT)).json()
    assert tm["last"]["by"] is None and tm["history"][0]["by"] is None  # своё показание — без подписи

    # права: получатель не закрывает чужой доступ, собственник не может «выйти» из своего адреса
    path = f"/api/shares/{owner['address_id']}/members/{tenant['id']}"
    assert_error(client.delete(path, headers=auth(TENANT)), 403, "not_owner")
    assert_error(client.delete(f"/api/shares/{owner['address_id']}/members/{owner['id']}", headers=auth()),
                 404, "not_found")
    assert_error(client.delete(f"/api/shares/received/{owner['address_id']}", headers=auth()), 404, "not_found")
    assert_error(client.delete("/api/shares/received/999", headers=auth(TENANT)), 404, "not_found")

    api.clear()
    assert client.delete(path, headers=auth()).json() == {"status": "closed", "address_id": owner["address_id"],
                                                          "member_id": tenant["id"]}
    assert run(client, repo(client).user_address, tenant["id"], owner["address_id"])["access"] == "denied"
    (notice,) = sent_to(api, TENANT)
    assert notice.startswith(IT.TENANT_REVOKED.split("{")[0])
    assert_error(client.delete(path, headers=auth()), 404, "not_found")  # уже закрыт
    assert client.get("/api/shares", headers=auth(TENANT)).json()["received"] == []
    assert client.get("/api/shares", headers=auth()).json()["owned"][0]["members"] == []

    # новая ссылка → снова доступ; получатель убирает адрес у себя → собственнику сообщение
    token = run(client, repo(client).get_invite_by_id, share(client, [owner["address_id"]]).json()["id"])["token"]
    accept(client, token, TENANT)
    api.clear()
    resp = client.delete(f"/api/shares/received/{owner['address_id']}", headers=auth(TENANT))
    assert resp.json() == {"status": "removed", "address_id": owner["address_id"]}
    assert run(client, repo(client).user_address, tenant["id"], owner["address_id"]) is None
    (notice,) = sent_to(api, UID)
    assert notice.startswith("Пётр П. вышел из общего доступа к адресу")
    assert len(run(client, repo(client).history, mid)) == 1  # показания остались


def test_demo_profile_shares_in_api(client, api):
    """ТОЛЬКО ДЛЯ ХАКАТОНА: /demo_profile — в /api/shares и /api/me видны жильцы и чужой адрес; второй свой адрес
    без людей и ссылок (в мини-приложении у первого — «управлять доступом», у второго — «поделиться»)."""
    deps = client.app.state.deps
    router = Router(dataclasses.replace(deps, settings=dataclasses.replace(deps.settings, hackathon_demo_profile=True)))
    run(client, router.handle, parse_update(fakes.message_created(UID, "/demo_profile")))
    s = client.get("/api/shares", headers=auth()).json()
    first, second = s["owned"]
    assert 1 <= len(first["members"]) <= 2 and second["members"] == []
    assert first["members"][0]["submitted_this_period"] is True and first["members"][0]["last_submitted_at"]
    assert s["invites"] == []
    (received,) = s["received"]
    assert re.fullmatch(r"[А-ЯЁ][а-яё]+ [А-ЯЁ]\.", received["owner"])
    by_id = {a["id"]: a for a in client.get("/api/me", headers=auth()).json()["addresses"]}
    assert by_id[first["address_id"]]["shared_count"] == len(first["members"])
    assert (by_id[second["address_id"]]["invites_count"], by_id[second["address_id"]]["shared_count"]) == (0, 0)
    shared = by_id[received["address_id"]]
    assert shared["role"] == "tenant" and shared["owner"] == received["owner"]
    assert shared["owner_gen"] == received["owner_gen"]  # «доступ от Анны И.» одинаково в /api/me и /api/shares
    assert shared["since"] == received["since"]  # страница общего адреса: «с 19 октября»
    assert all(kw["user_id"] > 0 for kw in api.named("send") if kw["user_id"] is not None)



# --- Иконка у своего адреса в мини-приложении («человек+» / «управлять доступом») строится из shared_count и
# invites_count в /api/me: они обязаны следовать за людьми и ДЕЙСТВУЮЩИМИ ссылками при любом переходе состояний.

def counts(client, uid: int = UID) -> dict[int, tuple[int, int]]:
    return {a["id"]: (a["shared_count"], a["invites_count"])
            for a in client.get("/api/me", headers=auth(uid)).json()["addresses"] if a["role"] == "owner"}


def token_of(client, invite_id: int) -> str:
    return run(client, repo(client).get_invite_by_id, invite_id)["token"]


def second_address(client, uid: int, text: str = "Москва, Тверская 1, кв 5") -> int:
    (c,) = AddressService().parse_local(text)
    user = run(client, repo(client).get_user, uid)
    return run(client, repo(client).add_user_address, user["id"], c.to_dict(), norm_key(c), text,
               clock.today())["address_id"]


def test_counts_follow_close_cancel_reinvite_remove(client):
    owner = register(client)
    register(client, TENANT, flat="77")
    a = owner["address_id"]
    assert counts(client) == {a: (0, 0)}
    inv = share(client, [a]).json()["id"]
    assert counts(client) == {a: (0, 1)}
    accept(client, token_of(client, inv), TENANT)
    assert counts(client) == {a: (1, 0)}                       # ссылку приняли — она больше не «висит»
    other = share(client, [a]).json()["id"]                     # вторая ссылка, ещё не открыта
    assert counts(client) == {a: (1, 1)}
    tenant_id = run(client, repo(client).get_user, TENANT)["id"]
    assert client.delete(f"/api/shares/{a}/members/{tenant_id}", headers=auth()).status_code == 200
    assert client.delete(f"/api/shares/invites/{other}", headers=auth()).status_code == 200
    assert counts(client) == {a: (0, 0)}                       # закрыт (denied) и отменена → «человек+»
    s = client.get("/api/shares", headers=auth()).json()
    assert s["owned"][0]["members"] == [] and s["invites"] == []
    # denied → новая ссылка → снова доступ, с новой даты
    clock.set_now(clock.now() + timedelta(days=2))
    accept(client, token_of(client, share(client, [a]).json()["id"]), TENANT)
    assert counts(client) == {a: (1, 0)}
    (recv,) = client.get("/api/shares", headers=auth(TENANT)).json()["received"]
    assert recv["since"] == "2026-10-21"
    # убрал у себя → снова пригласить
    assert client.delete(f"/api/shares/received/{a}", headers=auth(TENANT)).status_code == 200
    assert counts(client) == {a: (0, 0)}
    accept(client, token_of(client, share(client, [a]).json()["id"]), TENANT)
    assert counts(client) == {a: (1, 0)}


def test_counts_expired_link_and_multi_address_link(client):
    owner = register(client)
    register(client, TENANT, flat="77")
    a, b = owner["address_id"], second_address(client, UID)
    share(client, [a, b])
    assert counts(client) == {a: (0, 1), b: (0, 1)}            # одна ссылка на два адреса — у обоих
    clock.set_now(clock.now() + timedelta(days=7, minutes=1))
    assert counts(client) == {a: (0, 0), b: (0, 0)}            # истекла — не считаем
    assert client.get("/api/shares", headers=auth()).json()["invites"] == []
    accept(client, token_of(client, share(client, [a]).json()["id"]), TENANT)  # у получателя уже есть A
    res = accept(client, token_of(client, share(client, [a, b]).json()["id"]), TENANT)
    assert (res["granted"], res["has"]) == ([b], [a])
    assert counts(client) == {a: (1, 0), b: (1, 0)}
    assert client.delete(f"/api/shares/received/{a}", headers=auth(TENANT)).status_code == 200
    assert counts(client) == {a: (0, 0), b: (1, 0)}
    assert [x["address_id"] for x in client.get("/api/shares", headers=auth(TENANT)).json()["received"]] == [b]


def test_counts_after_account_deletion(client):
    owner = register(client)
    register(client, TENANT, flat="77")
    register(client, OTHER, flat="40")
    a = owner["address_id"]
    for uid in (TENANT, OTHER):
        accept(client, token_of(client, share(client, [a]).json()["id"]), uid)
    share(client, [a])                                         # ещё одна неоткрытая ссылка
    assert counts(client) == {a: (2, 1)}
    run(client, repo(client).delete_user_data, run(client, repo(client).get_user, OTHER)["id"])  # получатель ушёл
    assert counts(client) == {a: (1, 1)}
    run(client, repo(client).delete_user_data, run(client, repo(client).get_user, UID)["id"])    # собственник ушёл
    mine = next(x for x in client.get("/api/me", headers=auth(TENANT)).json()["addresses"] if x["id"] == a)
    assert (mine["role"], mine["shared_count"], mine["invites_count"], mine["owner"]) == ("owner", 0, 0, None)
    assert client.get("/api/shares", headers=auth(TENANT)).json()["invites"] == []  # ссылки прежнего — не его


def test_closed_recipient_can_remove_address_at_self(client, api):
    """Доступ закрыли (denied): бот советует убрать адрес у себя — API это позволяет, собственнику не пишем."""
    owner = register(client)
    register(client, TENANT, flat="77")
    a = owner["address_id"]
    accept(client, token_of(client, share(client, [a]).json()["id"]), TENANT)
    tenant_id = run(client, repo(client).get_user, TENANT)["id"]
    client.delete(f"/api/shares/{a}/members/{tenant_id}", headers=auth())
    closed = next(x for x in client.get("/api/me", headers=auth(TENANT)).json()["addresses"] if x["id"] == a)
    assert (closed["role"], closed["access"], closed["owner"]) == ("tenant", "denied", None)
    api.clear()
    assert client.delete(f"/api/shares/received/{a}", headers=auth(TENANT)).status_code == 200
    assert run(client, repo(client).user_address, tenant_id, a) is None
    assert sent_to(api, UID) == []
