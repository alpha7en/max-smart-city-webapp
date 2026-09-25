"""Разметка сообщений MAX: помощники fmt (b, quote, with_notes) и демо-оговорки последним блоком-цитатой."""
from __future__ import annotations

from datetime import date

from app import arshin_service as AS
from app.bot.flows import menu
from app.bot import router as R
from app.bot.texts import arshin as TA
from app.bot.texts import menu as T
from app.bot.texts import profile as PT
from app.bot.texts.fmt import b, days, left_days, meter_title, quote, with_notes
from app.domain.dashboard import DashboardData, build_dashboard
from app.domain.verification import Record
from tests.test_dashboard import GRANTED, TODAY, bill, meter
from tests.test_menu import make_user


def last_block(text: str) -> list[str]:
    """Последний блок сообщения (после последней пустой строки)."""
    return text.rsplit("\n\n", 1)[-1].split("\n")


# --- Помощники ---

def test_bold_escapes_and_skips_empty():
    assert b("4 312 ₽") == "**4 312 ₽**"
    assert b("a*b_c`[x]") == r"**a\*b\_c\`\[x\]**"  # пользовательское не ломает разметку
    assert b("") == "" and b(None) == "" and b(" ") == ""
    assert b(2030) == "**2030**"


def test_quote_single_and_multiline():
    assert quote("Демо-версия.") == "> Демо-версия."
    assert quote(["Первая", None, "", "Вторая\nТретья"]) == "> Первая\n> Вторая\n> Третья"
    assert quote([]) == ""


def test_with_notes_last_block_deduped():
    text = with_notes("Готово!\n**1 м³**", "Демо A.", None, "Демо B.", "Демо A.")
    assert text == "Готово!\n**1 м³**\n\n> Демо A.\n> Демо B."
    assert with_notes("Текст", None) == "Текст"
    assert with_notes("", "Демо.") == "> Демо."


def test_days_bold_and_title():
    assert left_days(6, bold=True) == "осталось **6 дней**" and left_days(6) == "осталось 6 дней"
    assert left_days(0, bold=True) == "сегодня **последний день**"
    assert days(-3, bold=True) == "просрочено на **3 дня**"
    assert meter_title("hot_water", "Гор. вода · Дубнинская 37к1, кв 198") == "Горячая вода · Дубнинская 37к1, кв 198"


# --- Демо-оговорки — последним блоком-цитатой ---

def test_dashboard_demo_bill_is_last_quote_and_api_lines_plain():
    d = build_dashboard(DashboardData([meter(1)], [bill(1, date(2026, 11, 10))], GRANTED), TODAY)
    assert last_block(d.markdown) == [f"> {T.BILL_NOTE}"]
    assert "Счёт: **4 312 ₽** до 10 ноября\n" in d.markdown and "(демо)" not in d.markdown
    assert "до **25 октября**, осталось **6 дней**" in d.markdown
    # /api/me: простой текст без разметки, пометка «(демо)» — как раньше
    assert "Счёт: 4 312 ₽ до 10 ноября (демо)" in d.lines
    assert not any("**" in x or x.startswith(">") for x in d.lines)


def test_dashboard_escapes_labels_in_markdown_only():
    d = build_dashboard(DashboardData([meter(1, label="Ул_Мира 1")], [], GRANTED), TODAY)
    assert r"Хол. вода · Ул\_Мира 1" in d.markdown and "Хол. вода · Ул_Мира 1 — не подано" in d.lines


def test_dashboard_arshin_demo_note():
    m = dict(meter(1, verif=date(2026, 11, 8)), verification_source="arshin", arshin_vri_id="demo-1-1")
    d = build_dashboard(DashboardData([m], [], GRANTED), TODAY)
    assert f"до **8 ноября**, {TA.SOURCE}" in d.markdown
    assert last_block(d.markdown) == [f"> {TA.DEMO_NOTE}"]
    assert any(TA.SOURCE_DEMO in x for x in d.lines)


def test_picked_line_demo_quote():
    rec = Record.from_dict({"vri_id": "demo-1-1", "valid_date": "2031-06-04", "verification_date": "2025-06-05"})
    assert AS.picked_line(rec, demo=True).endswith(f"**04.06.2031** по данным ФГИС «Аршин». Напомним заранее.\n\n> {TA.DEMO_NOTE}")
    assert "> " not in AS.picked_line(rec, demo=False)


async def test_note_head_keeps_quote_last(chat, api, repo, monkeypatch):
    """ctx.note()/header добавляются в начало — оговорка-цитата остаётся последней."""
    await make_user(repo)

    async def with_header(ctx):
        ctx.note("Предыдущую подачу отменили.")
        await menu.send_menu(ctx)

    monkeypatch.setitem(R.GLOBAL_ACTIONS, "menu", with_header)
    await chat.payload("g|menu|")
    text = api.last_text()
    assert text.startswith("Предыдущую подачу отменили.\n\n") and last_block(text) == [f"> {T.BILL_NOTE}"]


def test_rights_model_is_separate_note():
    assert "демо" not in PT.NO_ACCESS and "демо" not in PT.ACCESS_CLAIMED and "демо" not in PT.DEMO_GRANTED
