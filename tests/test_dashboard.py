"""Дашборд меню (SPEC §5.7): D1 приоритет срочного, D2 объём и крайние случаи, D3 форматы."""
from __future__ import annotations

from datetime import date, timedelta

from app.bot.texts import menu as T
from app.bot.texts.fmt import left_days, n_days
from app.domain.dashboard import MAX_LINES, DashboardData, build_dashboard

TODAY = date(2026, 10, 19)          # окно подачи открыто, до 25-го 6 дн.
ARBAT = "Арбат 47к1, кв 32"


def meter(mid: int, type: str = "cold_water", *, label: str = ARBAT, period: str | None = None,
          created: str = "2026-10-12 09:00:00", verif: date | None = None, t1: int = 123456,
          tariffs: int = 1) -> dict:
    return {
        "id": mid, "type": type, "tariffs": tariffs, "address_id": 1, "address_label": label, "serial": None,
        "verification_due": verif.isoformat() if verif else None, "verification_source": "user" if verif else None,
        "last_id": mid * 10 if period else None, "last_period": period, "last_t1": t1 if period else None,
        "last_t2": None, "last_t3": None, "last_created_at": created if period else None,
    }


def bill(bid: int, due: date, amount: int = 431200, label: str = ARBAT) -> dict:
    return {"id": bid, "address_id": 1, "period": "2026-09", "amount_kop": amount, "due_date": due.isoformat(),
            "status": "unpaid", "is_demo": 1, "address_label": label}


GRANTED = [{"label": ARBAT, "access": "granted", "role": "owner"}]


def dash(meters=(), bills=(), addresses=GRANTED, today=TODAY):
    return build_dashboard(DashboardData(list(meters), list(bills), list(addresses)), today)


def content(d) -> list[str]:
    return [x for x in d.lines if x]


# --- D1: приоритет срочного ---

def test_urgent_priority_verification_bill_submit():
    today = date(2026, 10, 23)  # до конца окна 2 дня
    m = meter(1, verif=today + timedelta(days=10))
    b = bill(7, today + timedelta(days=3))
    d = dash([m], [b], today=today)
    assert (d.urgent.kind, d.urgent.text, d.urgent.days_left, d.urgent.ref) == (
        "verification", "Запишитесь на поверку: 10 дней", 10, 1)
    assert d.lines[0] == d.urgent.text  # срочное — первой строкой (мини-приложение его пропускает)
    d = dash([meter(1)], [b], today=today)
    assert (d.urgent.kind, d.urgent.text, d.urgent.ref) == ("bill", "Оплатите счёт: 3 дня", 7)
    d = dash([meter(1)], today=today)
    assert (d.urgent.kind, d.urgent.text) == ("submit", "Подайте показания: 2 дня")
    assert d.urgent.to_dict() == {"kind": "submit", "text": "Подайте показания: 2 дня", "days_left": 2}


def test_urgent_thresholds_and_edge_texts():
    assert dash([meter(1, verif=TODAY + timedelta(days=31))]).urgent is None  # поверка > 30 дн. — не срочно
    assert dash([meter(1)], [bill(1, TODAY + timedelta(days=6))]).urgent is None
    assert dash([meter(1)]).urgent is None  # окно открыто, но до конца 6 дн.
    today = date(2026, 10, 25)
    assert dash([meter(1, period="2026-10")], today=today).urgent is None  # всё подано
    assert dash([meter(1)], today=today).urgent.text == "Подайте показания сегодня"
    overdue = dash([meter(1, verif=TODAY - timedelta(days=2))]).urgent
    assert (overdue.text, overdue.days_left) == ("Поверка просрочена — запишитесь", -2)
    assert dash([meter(1)], [bill(1, TODAY)]).urgent.text == "Оплатите счёт сегодня"
    assert dash([meter(1)], [bill(1, TODAY - timedelta(days=1))]).urgent.text == "Счёт просрочен — оплатите"
    longest = [T.URGENT_VERIFICATION.format(n=30), T.URGENT_VERIFICATION_OVERDUE, T.URGENT_BILL_OVERDUE,
               T.URGENT_SUBMIT.format(n=3), T.URGENT_VERIFICATION_TODAY]
    assert all(len(t) <= 32 for t in longest)


# --- D2: объём, много счётчиков, нет счётчиков, вне окна ---

def test_typical_dashboard_matches_spec():
    d = dash([meter(1, period="2026-10"), meter(2, "electricity"),
              meter(3, "hot_water", verif=date(2026, 11, 3) + timedelta(days=30))],
             [bill(1, date(2026, 11, 10))])
    assert d.lines == [
        "Показания за октябрь — до 25 октября, осталось 6 дней",
        "Хол. вода · Арбат 47к1, кв 32 — подано 12.10",
        "Свет · Арбат 47к1, кв 32 — не подано",
        "Гор. вода · Арбат 47к1, кв 32 — не подано",
        "",
        "Поверка: Гор. вода · Арбат 47к1, кв 32 — до 3 декабря",
        "Счёт: 4 312 ₽ до 10 ноября (демо)",
        "",
        "Подробнее — в мини-приложении.",
    ]
    assert d.urgent is None and not d.all_submitted


def test_many_meters_summary_and_line_limit():
    meters = [meter(i, period="2026-10" if i <= 3 else None, verif=TODAY + timedelta(days=5 + i))
              for i in range(1, 6)]
    addresses = GRANTED + [{"label": "Ленина 5", "access": "pending", "role": "tenant"}]
    d = dash(meters, [bill(1, TODAY + timedelta(days=2)), bill(2, TODAY + timedelta(days=9), 100000)], addresses)
    assert "Счётчиков: 5, не подано: 2" in d.lines
    assert "Счета: 2 на 5 312 ₽, ближайший до 21 октября (демо)" in d.lines
    assert any(x.endswith(", и ещё 4") for x in d.lines)
    assert "По адресу «Ленина 5» передавать показания можно после одобрения собственника." in d.lines
    assert len(content(d)) <= MAX_LINES
    # Худший случай при ≤3 счётчиках: срочное, доступ, заголовок + 3 счётчика, поверка, счёт, «подробнее».
    d = dash(meters[:3] + [], [bill(1, TODAY + timedelta(days=2))],
             addresses + [{"label": "Мира 1", "access": "pending", "role": "tenant"}])
    assert len(content(d)) == MAX_LINES
    assert "По 2 адресам передавать показания можно после одобрения собственников." in d.lines
    all_done = dash([meter(i, period="2026-10") for i in range(1, 5)])
    assert "Счётчиков: 4, всё подано" in all_done.lines and all_done.all_submitted


def test_no_meters_and_pending_only():
    assert dash().lines == [T.NO_METERS, "", T.FOOTER]
    pending = [{"label": ARBAT, "access": "pending", "role": "tenant"}]
    d = dash(addresses=pending)
    assert T.NO_METERS not in d.lines  # фото без прав не поможет — не зовём присылать
    assert d.lines[0] == f"По адресу «{ARBAT}» передавать показания можно после одобрения собственника."
    assert d.addresses == [{"label": ARBAT, "access": "pending", "role": "tenant"}]


def test_outside_window():
    # Одинаковые тип и адрес различаем так же, как в подаче (domain.meters.meter_labels): «№1», «№2».
    d = dash([meter(1, period="2026-10", created="2026-10-20 08:00:00"), meter(2)], today=date(2026, 10, 28))
    assert d.lines[:3] == ["Следующая подача — с 15 ноября", "Хол. вода · Арбат 47к1, кв 32 №1 — подано 20.10",
                           "Хол. вода · Арбат 47к1, кв 32 №2 — не подано"]
    d = dash([meter(1)], today=date(2026, 10, 5))  # окно ещё не открылось — «не подано» не пишем
    assert d.lines[:2] == ["Следующая подача — с 15 октября", "Хол. вода · Арбат 47к1, кв 32"]


# --- D3: форматы ---

def test_formats_and_verification_window():
    d = dash([meter(1, verif=TODAY + timedelta(days=60))], [bill(1, date(2026, 11, 10), 431200)])
    text = d.text
    assert "до 25 октября, осталось 6 дней" in text
    assert "Счёт: 4 312 ₽ до 10 ноября (демо)" in text
    assert "Поверка: Хол. вода · Арбат 47к1, кв 32 — до 18 декабря" in text
    assert "Поверка" not in dash([meter(1, verif=TODAY + timedelta(days=61))]).text
    assert "срок истёк 17 октября" in dash([meter(1, verif=date(2026, 10, 17))]).text
    # Дата подачи — по Москве: 22:30 UTC 11.10 — это уже 12.10.
    assert "подано 12.10" in dash([meter(1, period="2026-10", created="2026-10-11 22:30:00")]).text
    assert [n_days(n) for n in (1, 2, 5, 11, 21, 22, 25)] == [
        "1 день", "2 дня", "5 дней", "11 дней", "21 день", "22 дня", "25 дней"]
    assert (left_days(1), left_days(3), left_days(0)) == ("остался 1 день", "осталось 3 дня", "сегодня последний день")


def test_api_payload():
    d = dash([meter(1, period="2026-10"), meter(2, "electricity", tariffs=2)])
    assert d.to_api()["urgent"] is None and d.to_api()["lines"] == d.lines
    m1, m2 = d.meters
    assert m1 == {
        "id": 1, "type": "cold_water", "type_label": "Хол. вода", "unit": "м³", "tariffs": 1,
        "address_label": ARBAT, "serial": None, "submitted_this_period": True, "verification_due": None,
        "last": {"period": "2026-10", "values": {"t1": "123,456"}, "created_at": "2026-10-12T12:00:00+03:00"},
    }
    assert m2["last"] is None and m2["submitted_this_period"] is False and m2["tariffs"] == 2


# --- Структура для мини-приложения (строки не разбираются) ---

def test_structured_window_open_and_closed():
    d = dash([meter(1, period="2026-10"), meter(2)])
    assert d.window == {"period": "2026-10", "month_label": "октябрь", "from": "2026-10-15", "to": "2026-10-25",
                        "open": True, "days_left": 6, "next_from": None}
    assert (d.submitted, d.total) == (1, 2)
    assert dash([meter(1)], today=date(2026, 10, 25)).window["days_left"] == 0  # последний день
    before = dash([meter(1)], today=date(2026, 10, 5)).window
    assert before == {"period": "2026-10", "month_label": "октябрь", "from": "2026-10-15", "to": "2026-10-25",
                      "open": False, "days_left": None, "next_from": "2026-10-15"}
    after = dash([meter(1)], today=date(2026, 12, 28)).window  # после окна — следующее, через Новый год
    assert (after["period"], after["month_label"], after["open"], after["next_from"]) == (
        "2027-01", "январь", False, "2027-01-15")
    empty = dash()
    assert empty.window["open"] and (empty.submitted, empty.total, empty.bill, empty.verification) == (0, 0, None, None)


def test_structured_bill():
    assert dash([meter(1)]).bill is None
    d = dash([meter(1)], [bill(9, TODAY + timedelta(days=9), 100000), bill(7, TODAY + timedelta(days=3))])
    assert [b["id"] for b in d.bills] == [7, 9] and "count" not in d.bills[0]
    assert d.bill == {"id": 7, "address_id": 1, "amount_kop": 431200, "amount_text": "4 312 ₽", "due": "2026-10-22",
                      "days_left": 3, "demo": True, "count": 2}
    late = dash([meter(1)], [{**bill(1, TODAY - timedelta(days=2)), "is_demo": 0}]).bill
    assert (late["days_left"], late["demo"]) == (-2, False)


def test_structured_verification_window_60_days():
    d = dash([meter(1, verif=TODAY + timedelta(days=61)), meter(2, "hot_water", verif=TODAY + timedelta(days=60))])
    assert d.verification == {"meter_id": 2, "meter_label": f"Гор. вода · {ARBAT}", "type": "hot_water",
                              "due": "2026-12-18", "days_left": 60}
    assert dash([meter(1, verif=TODAY + timedelta(days=61))]).verification is None
    assert dash([meter(1, verif=TODAY - timedelta(days=3))]).verification["days_left"] == -3  # просрочена


def test_structured_pending_and_api():
    addresses = GRANTED + [{"label": "Ленина 5", "access": "pending", "role": "tenant"},
                           {"label": "Мира 1", "access": "pending", "role": "tenant"}]
    d = dash([meter(1)], addresses=addresses)
    assert d.pending == [{"label": "Ленина 5"}, {"label": "Мира 1"}]
    api = d.to_api()
    assert set(api) == {"lines", "urgent", "window", "bill", "bills", "verification", "pending", "submitted",
                        "total"}
    assert api["pending"] == d.pending and api["window"] == d.window and api["total"] == 1
    assert dash().pending == []
