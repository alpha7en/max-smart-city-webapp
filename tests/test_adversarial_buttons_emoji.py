"""
Adversarial Stress Test Suite: Buttons, Emojis, and WebApp Links.
Executed by challenger_buttons_emoji_1.

Scope:
1. Button lengths: generator stress-testing all meter button labels, edge-case names,
   serials, commands, and numbers to assert len(text) <= 18.
2. Unicode emoji scan: comprehensive regex scan across app/, max_bot_sdk/, scripts/
   for Unicode emoji blocks to confirm 0 emojis.
3. WebApp links: verify all outgoing bot message templates contain valid clickable
   WebApp URLs without broken backticks.
"""

import os
import re
import unicodedata
from datetime import date
from typing import List, Dict, Any, Optional

import pytest
from app.config import settings
from app.bot.handlers import BotHandler, get_main_menu_keyboard
from app.bot.client import MaxBotClient
from app.models.domain import MeterType
from app.models.schemas import MeterBase
from app.services.meter_service import meter_service, INITIAL_METERS
from max_bot_sdk.keyboards import Button, KeyboardBuilder, _sanitize_text, MAX_BUTTON_TEXT_LEN


class MockCaptureClient:
    """Mock client capturing all outgoing messages and callbacks for assertions."""
    def __init__(self):
        self.sent_messages: List[Dict[str, Any]] = []
        self.answered_callbacks: List[Dict[str, Any]] = []

    def send_message(self, **kwargs) -> Dict[str, Any]:
        self.sent_messages.append(kwargs)
        return {"status": "ok"}

    def answer_callback(self, **kwargs) -> Dict[str, Any]:
        self.answered_callbacks.append(kwargs)
        return {"status": "ok"}


# =========================================================================
# 1. ADVERSARIAL TEST: BUTTON LENGTHS (len(text) <= 18)
# =========================================================================

def test_adversarial_button_sanitize_exhaustive():
    """Stress-test _sanitize_text across lengths 0 to 500, boundaries 17, 18, 19, 20."""
    # Test empty and whitespace
    assert len(_sanitize_text("")) <= 18
    assert len(_sanitize_text("   ")) <= 18
    assert len(_sanitize_text("\n\t  \r ")) <= 18

    # Test boundary lengths exactly 17, 18, 19, 20
    for l in [17, 18, 19, 20, 21, 50, 100, 250, 500]:
        sample = "A" * l
        sanitized = _sanitize_text(sample)
        assert len(sanitized) <= 18, f"Failed for length {l}: {sanitized!r} (len={len(sanitized)})"
        if l > 18:
            assert sanitized.endswith("…")

    # Test space-padded strings
    for l in range(1, 200):
        sample = ("X" * l) + "   "
        sanitized = _sanitize_text(sample)
        assert len(sanitized) <= 18, f"Failed on padded length {l}: {sanitized!r}"

    # Test Cyrillic and multi-word strings
    phrases = [
        "Сдать показания прибора учета холодного водоснабжения",
        "Проверка поверки прибора во ФГИС АРШИН Росстандарта",
        "Оплата квитанции за жилищно-коммунальные услуги по СБП",
        "Формирование цифрового акта обходчика управляющей компании",
        "Заявка на срочный вызов аварийного сантехника",
        "ОченьДлинноеСловоБезЕдиногоПробелаДляПроверкиГраниц1234567890",
        "Сдать ХВС",
        "Сдать ГВС",
        "Сдать Свет",
        "Сдать Газ",
        "Сдать Тепло"
    ]
    for p in phrases:
        s = _sanitize_text(p)
        assert len(s) <= 18, f"Failed on phrase {p!r} -> {s!r} (len={len(s)})"


def test_adversarial_button_constructors():
    """Verify Button.callback, Button.link, Button.open_app guarantee len <= 18."""
    extreme_inputs = [
        "", "a", "12345678901234567", "123456789012345678", "1234567890123456789",
        "Сверхдлинная кнопка превышающая все допустимые лимиты",
        "   пробелы спереди и сзади   ",
        "Кнопка с символами: [ХВС] №123456 (кухня/ванная)!"
    ]
    for inp in extreme_inputs:
        btn_cb = Button.callback(inp, "payload")
        assert len(btn_cb["text"]) <= 18, f"Button.callback exceeded: {btn_cb['text']}"

        btn_link = Button.link(inp, "https://max.ru")
        assert len(btn_link["text"]) <= 18, f"Button.link exceeded: {btn_link['text']}"

        btn_app = Button.open_app(inp, "https://max.ru")
        assert len(btn_app["text"]) <= 18, f"Button.open_app exceeded: {btn_app['text']}"


def test_adversarial_meter_buttons_generator():
    """
    Generate meters with adversarial names, serials, and types.
    Verify all generated button labels in _send_meters_list satisfy len(text) <= 18.
    """
    client = MockCaptureClient()
    handler = BotHandler(client)

    adversarial_meters = [
        # Normal
        MeterBase(id="m1", meter_type=MeterType.COLD_WATER, serial_number="111", name="ХВС (Холодная вода)", installation_place="Кухня", last_reading_value=10.0, last_reading_date=date.today(), verification_date_valid_until=date.today(), unit="м³", decimal_digits=3),
        MeterBase(id="m2", meter_type=MeterType.HOT_WATER, serial_number="222", name="ГВС (Горячая вода)", installation_place="Кухня", last_reading_value=20.0, last_reading_date=date.today(), verification_date_valid_until=date.today(), unit="м³", decimal_digits=3),
        MeterBase(id="m3", meter_type=MeterType.ELECTRICITY_MULTI, serial_number="333", name="Электроэнергия (Меркурий 208)", installation_place="Щит", last_reading_value=30.0, last_reading_date=date.today(), verification_date_valid_until=date.today(), unit="кВт*ч", decimal_digits=1),
        MeterBase(id="m4", meter_type=MeterType.GAS, serial_number="444", name="Газоснабжение (ВК-G4)", installation_place="Кухня", last_reading_value=40.0, last_reading_date=date.today(), verification_date_valid_until=date.today(), unit="м³", decimal_digits=3),
        # Adversarial Long Names
        MeterBase(id="m5", meter_type=MeterType.COLD_WATER, serial_number="55555555555555555555", name="Холодное водоснабжение санузел второго этажа коттеджа", installation_place="Санузел", last_reading_value=50.0, last_reading_date=date.today(), verification_date_valid_until=date.today(), unit="м³", decimal_digits=3),
        MeterBase(id="m6", meter_type=MeterType.HEAT, serial_number="666", name="Теплоснабжение центральное общедомовое распределительное", installation_place="Подвал", last_reading_value=60.0, last_reading_date=date.today(), verification_date_valid_until=date.today(), unit="Гкал", decimal_digits=2),
        MeterBase(id="m7", meter_type=MeterType.COLD_WATER, serial_number="777", name="БезСкобокОченьДлинныйПриборУчетаВодыИСтоков", installation_place="Улица", last_reading_value=70.0, last_reading_date=date.today(), verification_date_valid_until=date.today(), unit="м³", decimal_digits=3),
        MeterBase(id="m8", meter_type=MeterType.GAS, serial_number="888", name="(СкобкаВНачалеИмени) Газ", installation_place="Кухня", last_reading_value=80.0, last_reading_date=date.today(), verification_date_valid_until=date.today(), unit="м³", decimal_digits=3),
        MeterBase(id="m9", meter_type=MeterType.HOT_WATER, serial_number="999", name="   Пробелы   (ГВС)", installation_place="Санузел", last_reading_value=90.0, last_reading_date=date.today(), verification_date_valid_until=date.today(), unit="м³", decimal_digits=3),
    ]

    # Inject into meter_service
    orig_meters = dict(meter_service._meters)
    try:
        meter_service._meters.clear()
        for m in adversarial_meters:
            meter_service._meters[m.id] = m

        handler._send_meters_list(chat_id="c1", user_id=1)
        assert len(client.sent_messages) == 1
        msg = client.sent_messages[0]
        kb = msg.get("keyboard", {})
        buttons = kb.get("payload", {}).get("buttons", [])
        assert len(buttons) > 0

        for row in buttons:
            for btn in row:
                lbl = btn["text"]
                assert len(lbl) <= 18, f"Meter button label exceeded 18 chars: {lbl!r} (len={len(lbl)})"
    finally:
        meter_service._meters.clear()
        meter_service._meters.update(orig_meters)


def test_adversarial_photo_confirmation_and_payment_buttons():
    """
    Test button labels under huge reading values, long serial numbers, and huge payment sums.
    """
    client = MockCaptureClient()
    handler = BotHandler(client)

    # 1. Test photo confirmation button with extreme float values
    extreme_readings = [0.0, 142.385, 999999999.999, 1234567890.123, -5.0]
    for val in extreme_readings:
        confirm_text = f"Принять {val}"
        btn = Button.callback(confirm_text, "submit_meter_khvs")
        assert len(btn["text"]) <= 18, f"Confirm button too long: {btn['text']} (len={len(btn['text'])})"

    # 2. Test GOST QR payment button with extreme ruble amounts
    extreme_sums = [0.0, 150.0, 4850.50, 9999999.0, 999999999999.99]
    for s in extreme_sums:
        pay_label = f"Оплата {s:.0f} ₽"
        btn = Button.open_app(pay_label, settings.MINIAPP_URL)
        assert len(btn["text"]) <= 18, f"Payment button too long: {btn['text']} (len={len(btn['text'])})"


# =========================================================================
# 2. ADVERSARIAL TEST: UNICODE EMOJI SCAN (app/, max_bot_sdk/, scripts/)
# =========================================================================

# Strict Unicode regex pattern covering all Emoji blocks
EMOJI_REGEX = re.compile(
    r"["
    r"\U0001F600-\U0001F64F"  # Emoticons
    r"\U0001F300-\U0001F5FF"  # Misc Symbols & Pictographs (🤖, 💧, 🔥, 🏠, 🛡, 🛑)
    r"\U0001F680-\U0001F6FF"  # Transport & Map Symbols (🚀, 🚨)
    r"\U0001F700-\U0001F77F"  # Alchemical Symbols
    r"\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
    r"\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
    r"\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs (🤖, 🛑, etc.)
    r"\U0001FA00-\U0001FA6F"  # Chess Symbols
    r"\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
    r"\U00002600-\U000026FF"  # Misc Symbols (⚠️, ⚡, etc.)
    r"\U00002700-\U000027BF"  # Dingbats (✅, etc.)
    r"\U0001F1E6-\U0001F1FF"  # Regional Indicator Flags
    r"\U00002300-\U000023FF"  # Misc Technical (⏳, etc.)
    r"\U00002B50"              # Star
    r"\U00002B55"              # Circle
    r"]"
)

def scan_file_for_emojis(filepath: str) -> List[Dict[str, Any]]:
    """Scan a single file for emoji unicode characters."""
    findings = []
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for lineno, line in enumerate(f, 1):
            matches = EMOJI_REGEX.findall(line)
            if matches:
                for char in matches:
                    findings.append({
                        "file": filepath,
                        "line": lineno,
                        "char": char,
                        "name": unicodedata.name(char, "UNKNOWN"),
                        "codepoint": f"U+{ord(char):04X}",
                        "context": line.strip()
                    })
    return findings


def test_adversarial_unicode_emoji_scan_zero_tolerance():
    """
    Scans all source files in app/, max_bot_sdk/, scripts/.
    Asserts 0 emojis exist.
    """
    target_dirs = ["app", "max_bot_sdk", "scripts"]
    all_violations = []

    for d in target_dirs:
        for root, _, files in os.walk(d):
            for f in files:
                if f.endswith((".py", ".html", ".css", ".js")):
                    path = os.path.join(root, f)
                    violations = scan_file_for_emojis(path)
                    all_violations.extend(violations)

    # Filter out acceptable typographical math/punctuation symbols if any
    # Notice: '✓' (U+2713) and '✕' (U+2715) are dingbats, but let's see actual emojis:
    emoji_violations = [
        v for v in all_violations
        if v["char"] not in ('✓', '✕', '✗', '▶', '▲', '▼', '●', '■')
    ]

    print(f"\n[EMOJI AUDIT] Found {len(emoji_violations)} true emoji violations:")
    for v in emoji_violations:
        print(f"  {v['file']}:{v['line']} -> {v['char']} ({v['codepoint']} {v['name']}) in: {v['context'][:60]}")

    assert len(emoji_violations) == 0, (
        f"Found {len(emoji_violations)} emojis in source files! Zero emoji policy violated:\n" +
        "\n".join(f"{v['file']}:{v['line']} {v['char']} ({v['codepoint']}) in {v['context'][:50]}" for v in emoji_violations[:10])
    )


# =========================================================================
# 3. ADVERSARIAL TEST: WEBAPP LINKS & BACKTICKS
# =========================================================================

def test_adversarial_outgoing_bot_webapp_urls():
    """
    Assert that all outgoing bot message templates contain valid clickable WebApp URLs
    without broken backticks.
    """
    client = MockCaptureClient()
    handler = BotHandler(client)

    # 1. bot_started
    handler.handle_bot_started({"sender_user_id": 1, "chat_id": "c1"})

    # 2. commands
    commands = [
        "/start", "/app", "/meters", "/check", "/check 2809142", "/check 991201", "/check 000000",
        "/pay", "/ticket", "/guest", "/inspector",
        "st00012|Name=ООО УК ДОМОВОЙ СЕРВИС|PersonalAcc=40821810938000012345|BIC=044525225|Sum=485050",
        "st0001_invalid",
        "хвс 145.5", "гвс 99.1", "свет 1900", "вода abc", "случайный текст"
    ]
    for cmd in commands:
        handler.handle_message_created({
            "message": {
                "body": {"text": cmd},
                "sender": {"id": 1, "first_name": "Test"},
                "recipient": {"chat_id": "c1"}
            }
        })

    # 3. callbacks
    callbacks = [
        "cmd_meters", "cmd_arshin", "cmd_pay", "cmd_ticket", "cmd_guest", "cmd_inspector", "cmd_menu",
        "submit_meter_meter-khvs-1", "submit_meter_meter-gvs-1",
        "rescan_meter_cold_water", "rescan_meter_hot_water", "rescan_meter_electricity", "rescan_meter_gas",
        "unknown_callback"
    ]
    for cb in callbacks:
        handler.handle_callback({
            "callback": {"callback_id": "cb1", "payload": cb},
            "sender": {"id": 1},
            "chat_id": "c1"
        })

    # 4. photo drop
    handler.handle_message_created({
        "message": {
            "body": {"text": "фото хвс", "attachments": [{"type": "image", "url": "http://img"}]},
            "sender": {"id": 1},
            "recipient": {"chat_id": "c1"}
        }
    })

    assert len(client.sent_messages) > 0, "No messages were sent by BotHandler"

    webapp_url = settings.MINIAPP_URL or "http://localhost:8080/static/index.html"

    has_webapp = any(
        (webapp_url in m.get("text", "")) or
        ("http://" in m.get("text", "")) or
        ("https://" in m.get("text", "")) or
        (m.get("keyboard") and any(b.get("url") for row in m["keyboard"].get("payload", {}).get("buttons", []) for b in row if isinstance(b, dict)))
        for m in client.sent_messages
    )
    assert has_webapp, "Session must provide WebApp access via link or keyboard button"

    for i, msg in enumerate(client.sent_messages):
        text = msg.get("text", "")

        # Check 1: Even count of backticks (no broken / unclosed backticks)
        backtick_count = text.count("`")
        assert backtick_count % 2 == 0, (
            f"Message #{i} has broken (unclosed) backticks (count={backtick_count}):\n{text}"
        )

        # Check 2: WebApp URL must NOT be wrapped in backticks (e.g. `http://...`)
        # Wrapped URLs become inline code and are not clickable links on mobile/MAX clients
        escaped_url = re.escape(webapp_url)
        bad_link_match = re.search(r"`" + escaped_url + r"`", text)
        assert not bad_link_match, (
            f"Message #{i} wraps WebApp URL in backticks (unclickable link):\n{text}"
        )


# =========================================================================
# 4. ADVERSARIAL TEST: COMPREHENSIVE BUTTON LENGTH FUZZING (<= 18 chars)
# =========================================================================

def test_adversarial_button_length_fuzzing_comprehensive():
    """
    Generate adversarial edge case inputs:
    - Cyrillic pangrams and long phrases
    - Escapes, control chars, zero-width chars, ANSI sequences
    - Numeric boundaries, extreme floats, huge exponents, negative numbers
    - Special symbols, quotes, punctuation, brackets
    - RTL scripts (Arabic, Hebrew)
    - Randomized prefix + symbol + length combinations (1000+ tests)
    Verify all inline buttons passing through SDK or emitted by handlers NEVER exceed 18 characters.
    """
    edge_cases = [
        "",
        " ",
        "   \t\n\r   ",
        "12345678901234567",     # 17 chars
        "123456789012345678",    # 18 chars
        "1234567890123456789",   # 19 chars
        "12345678901234567890",  # 20 chars
        "Принять 142.385",
        "Принять 999999999999999999999999999999999999999999999999999999999999999999999",
        "Оплата 12345678901234567890 ₽",
        "Оплата 0.00 ₽",
        "Сдать " + "А" * 100,
        "Сдать\nХВС\t142",
        "Кнопка \u200B\u200C\u200D\uFEFF с невидимками",
        "Текст с \x00 и \x1b[31m escape sequences",
        "مرحبا بالعالم длинный арабский текст",
        "שָׁלוֹם עֲלֵיכֶם עולם длинный иврит",
        "e\u0301" * 20,
        "«»—–…\"\"''",
        "X" * 10000,
        "Сдать показания прибора учета холодного водоснабжения",
        "Проверка поверки прибора во ФГИС АРШИН Росстандарта",
        "Оплата квитанции за жилищно-коммунальные услуги по СБП",
        "Формирование цифрового акта обходчика управляющей компании",
        "Заявка на срочный вызов аварийного сантехника",
    ]

    for prefix in ["", " ", "  ", "\t", "Сдать ", "Оплата ", "Принять "]:
        for char in ["A", "Я", "0", " ", "\n", "…", "!", "@", "#", "$", "%", "^", "&", "*", "(", ")", "-", "_", "+", "=", "[", "]", "{", "}", ";", ":", "'", "\"", "<", ">", ",", ".", "/", "?", "\\", "|", "`", "~"]:
            for length in [0, 1, 10, 16, 17, 18, 19, 20, 25, 50, 100]:
                edge_cases.append(prefix + (char * length))

    for case in edge_cases:
        sanitized = _sanitize_text(case)
        assert len(sanitized) <= 18, f"_sanitize_text failed on input: {case!r} -> {sanitized!r} (len={len(sanitized)})"

        cb_btn = Button.callback(case, "payload")
        assert len(cb_btn["text"]) <= 18, f"Button.callback exceeded: {cb_btn['text']!r} (len={len(cb_btn['text'])})"

        link_btn = Button.link(case, "https://max.ru")
        assert len(link_btn["text"]) <= 18, f"Button.link exceeded: {link_btn['text']!r} (len={len(link_btn['text'])})"

        app_btn = Button.open_app(case, "https://max.ru")
        assert len(app_btn["text"]) <= 18, f"Button.open_app exceeded: {app_btn['text']!r} (len={len(app_btn['text'])})"


# =========================================================================
# 5. ADVERSARIAL TEST: ZERO-BRACKET SCANNER ACROSS ALL COMMAND FLOWS
# =========================================================================

def test_adversarial_zero_bracket_scanner_all_command_flows():
    """
    Empirically verify that outgoing bot messages generated across all command flows:
    - /start, start, menu, bot_started event, cmd_menu
    - /meters, meters, readings, cmd_meters
    - /pay, pay, receipt, cmd_pay
    - /ticket, ticket, master, cmd_ticket
    - /guest, guest, tenant, cmd_guest
    - /inspector, inspector, act, cmd_inspector
    - Photo drops (cold water, hot water, electricity, gas, heat, unhinted)
    - Reading callbacks (submit_meter_*, rescan_*)
    - ARSHIN check (/check <serial>, cmd_arshin, valid, expired, fake, unknown)
    - Text readings (хвс, гвс, свет, газ, тепло, commas, invalid values, monotonicity, anomalies)
    - GOST QR text payloads (valid, malformed)
    - Fallback responses
    contain ZERO junk brackets [...] (like [ХВС], [ГВС], [АРШИН], [ОПЛАТА], [OK], [ERR], etc.).
    And verify that all attached inline buttons NEVER exceed 18 characters.
    """
    client = MockCaptureClient()
    handler = BotHandler(client)

    # 1. bot_started event
    handler.handle_bot_started({"sender_user_id": 101, "chat_id": "c101"})

    # 2. Text command flows
    commands = [
        "/start", "старт", "меню",
        "/app", "приложение",
        "/meters", "счетчики", "показания",
        "/check", "/check 2809142", "/check 991201", "/check 000000", "/check 3910844",
        "/check 01458291", "/check 7741209", "/check 4819024", "/check 5091823",
        "/check 5540912", "/check FAKE01", "/check EXPIRED000", "/check 999999",
        "поверка 2809142", "аршин 991201",
        "/pay", "оплата", "квитанция",
        "/ticket", "заявка", "мастер",
        "/guest", "арендатор",
        "/inspector", "обходчик", "акт",
        "/profile", "/account", "профиль", "аккаунт",
        "/address", "адрес",
        "/register", "регистрация",
        "st00012|Name=ООО УК ДОМОВОЙ СЕРВИС|PersonalAcc=40821810938000012345|BIC=044525225|Sum=485050",
        "st0001_malformed",
        "хвс 145.5", "гвс 99.1", "свет 1900", "тепло 15.0", "газ 342.0",
        "хвс 145,5", "вода abc", "хвс 10.0", "хвс 500.0",
        "произвольная команда", "123456"
    ]
    for cmd in commands:
        handler.handle_message_created({
            "message": {
                "body": {"text": cmd},
                "sender": {"id": 101, "first_name": "Тестер"},
                "recipient": {"chat_id": "c101"}
            }
        })

    # 3. Callbacks flow
    callbacks = [
        "cmd_meters", "cmd_arshin", "cmd_pay", "cmd_ticket", "cmd_guest", "cmd_inspector", "cmd_menu",
        "cmd_profile", "cmd_address", "cmd_register", "cmd_add_address", "switch_prop_prop-dacha-8",
        "submit_meter_meter-khvs-1", "submit_meter_meter-gvs-1", "submit_meter_meter-el-1",
        "submit_meter_meter-gas-1", "submit_meter_meter-heat-1",
        "rescan_meter_cold_water", "rescan_meter_hot_water", "rescan_meter_electricity", "rescan_meter_gas",
        "rescan_hvs", "rescan_gvs", "rescan_el", "rescan_gas",
        "unknown_callback_edge"
    ]
    for cb in callbacks:
        handler.handle_callback({
            "callback": {"callback_id": f"cb_{cb}", "payload": cb},
            "sender": {"id": 101},
            "chat_id": "c101"
        })

    # 4. Photo drops
    for hint in ["фото хвс", "фото гвс", "свет фото", "газ", "отопление", "просто фото", ""]:
        handler.handle_message_created({
            "message": {
                "body": {"text": hint, "attachments": [{"type": "image", "url": "http://img.test/pic.jpg"}]},
                "sender": {"id": 101},
                "recipient": {"chat_id": "c101"}
            }
        })

    assert len(client.sent_messages) >= 60, f"Expected at least 60 sent messages, got {len(client.sent_messages)}"

    junk_bracket_pattern = re.compile(r"\[[A-Za-zА-Яа-я0-9\s:_-]+\]")
    bracket_violations = []
    button_length_violations = []

    for idx, msg in enumerate(client.sent_messages):
        text = msg.get("text", "")
        matches = junk_bracket_pattern.findall(text)
        if matches:
            bracket_violations.append({
                "message_idx": idx,
                "matches": matches,
                "snippet": text[:80]
            })

        kb = msg.get("keyboard")
        if kb and isinstance(kb, dict):
            buttons = kb.get("payload", {}).get("buttons", [])
            for row in buttons:
                for b in row:
                    lbl = b.get("text", "")
                    if len(lbl) > 18:
                        button_length_violations.append({
                            "message_idx": idx,
                            "label": lbl,
                            "length": len(lbl)
                        })

    assert len(bracket_violations) == 0, (
        f"Zero-bracket policy violated! Found {len(bracket_violations)} messages with junk brackets:\n" +
        "\n".join(f"Msg #{bv['message_idx']}: {bv['matches']} in {bv['snippet']!r}" for bv in bracket_violations)
    )

    assert len(button_length_violations) == 0, (
        f"Button length limit (18 chars) violated! Found {len(button_length_violations)} oversized buttons:\n" +
        "\n".join(f"Msg #{blv['message_idx']}: {blv['label']!r} (len={blv['length']})" for blv in button_length_violations)
    )


# =========================================================================
# 6. ADVERSARIAL TEST: ZERO-EMOJI SCANNER (MESSAGES AND UI FILES)
# =========================================================================

def test_adversarial_zero_emoji_scanner_messages_and_ui():
    """
    Verify 0 emojis across:
    1. All outgoing bot messages across all command flows.
    2. All UI files in app/static/ (index.html, styles.css, app.js).
    """
    # 1. Check outgoing bot messages
    client = MockCaptureClient()
    handler = BotHandler(client)

    # Run core flows to generate messages
    handler.handle_bot_started({"sender_user_id": 202, "chat_id": "c202"})
    for cmd in ["/meters", "/check 2809142", "/check 991201", "/check 000000", "/pay", "/ticket", "/guest", "/inspector", "/profile", "/account", "/address", "/register", "хвс 145.5"]:
        handler.handle_message_created({
            "message": {
                "body": {"text": cmd},
                "sender": {"id": 202, "first_name": "Тестер"},
                "recipient": {"chat_id": "c202"}
            }
        })
    handler.handle_message_created({
        "message": {
            "body": {"text": "фото хвс", "attachments": [{"type": "image", "url": "http://img"}]},
            "sender": {"id": 202},
            "recipient": {"chat_id": "c202"}
        }
    })

    msg_emoji_violations = []
    for idx, msg in enumerate(client.sent_messages):
        text = msg.get("text", "")
        matches = EMOJI_REGEX.findall(text)
        emojis = [c for c in matches if c not in ('✓', '✕', '✗', '▶', '▲', '▼', '●', '■')]
        if emojis:
            msg_emoji_violations.append({
                "message_idx": idx,
                "emojis": emojis,
                "text": text[:60]
            })

    assert len(msg_emoji_violations) == 0, (
        f"Zero-emoji policy violated in bot messages! Violations:\n" +
        "\n".join(f"Msg #{v['message_idx']}: {v['emojis']} in {v['text']!r}" for v in msg_emoji_violations)
    )

    # 2. Check UI files in app/static/
    static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "static")
    ui_violations = []
    for fname in ["index.html", "styles.css", "app.js"]:
        fpath = os.path.join(static_dir, fname)
        if os.path.exists(fpath):
            violations = scan_file_for_emojis(fpath)
            clean_violations = [v for v in violations if v["char"] not in ('✓', '✕', '✗', '▶', '▲', '▼', '●', '■')]
            ui_violations.extend(clean_violations)

    assert len(ui_violations) == 0, (
        f"Zero-emoji policy violated in UI files! Violations:\n" +
        "\n".join(f"{v['file']}:{v['line']} {v['char']} ({v['codepoint']}) in {v['context'][:50]}" for v in ui_violations)
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

