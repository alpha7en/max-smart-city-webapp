#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Autonomous Standalone Verification Suite for Hackathon Smart City ЖКХ in MAX.
Executes completely non-interactively without human intervention.

Scope:
1. All FastAPI REST Endpoints (11 routes):
   - GET  /api/health
   - GET  /api/meters
   - POST /api/meters/submit
   - POST /api/ai/scan
   - GET  /api/arshin/check
   - POST /api/billing/parse-qr
   - POST /api/uk/inspector-act
   - POST /api/guest/generate
   - POST & GET /api/tickets
   - GET/POST   /api/auth/verify-init-data
   - POST /api/bot/webhook

2. All 4 Core User Scenarios:
   - Scenario 1: Direct Drop Photo (AI vision, red roller cutoff, GIS ZHKH registration)
   - Scenario 2: «Зеленый Щит» АРШИН (FGIS ARSHIN registry lookup & anti-fraud alert)
   - Scenario 3: Гостевой доступ для арендаторов (No ESIA barrier, secure token)
   - Scenario 4: АРМ Обходчика УК (Digital act, GPS, ISO-8601, SHA-256, 1C:ZHKH export)

3. Button Text Length Conformance:
   - Every inline button label in app/bot/handlers.py strictly <= 18 characters

Exits with code 0 on 100% pass, non-zero on any failure.
"""

import sys
import os
import ast
import time
import json
from pathlib import Path
from datetime import date

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Ensure virtualenv python is used if system python lacks dependencies
try:
    import fastapi
except ImportError:
    venv_py = ROOT_DIR / ".venv" / "bin" / "python3"
    if venv_py.exists() and sys.executable != str(venv_py):
        os.execv(str(venv_py), [str(venv_py)] + sys.argv)
    else:
        raise

from fastapi.testclient import TestClient
from app.main import app
from app.config import settings
from app.api.security import generate_init_data
from app.models.domain import MeterType
from app.models.schemas import MeterBase, MeterReadingSubmitRequest
from app.services.meter_service import meter_service, INITIAL_METERS
from app.services.arshin import arshin_service
from app.services.gost_qr import parse_gost_qr_payload
from app.services.guest_service import guest_service
from app.services.inspector_service import inspector_service
from app.bot.handlers import get_main_menu_keyboard, get_bot_handler

# ANSI Color Codes
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def reset_meters_state():
    """Reset meter_service in-memory singleton to pristine state."""
    pristine = {
        "meter-khvs-1": MeterBase(
            id="meter-khvs-1",
            meter_type=MeterType.COLD_WATER,
            serial_number="2809142",
            name="ХВС (Холодная вода)",
            installation_place="Санузел",
            last_reading_value=142.0,
            last_reading_date=date(2026, 8, 20),
            verification_date_valid_until=date(2029, 10, 18),
            unit="м³",
            decimal_digits=3
        ),
        "meter-gvs-1": MeterBase(
            id="meter-gvs-1",
            meter_type=MeterType.HOT_WATER,
            serial_number="3910844",
            name="ГВС (Горячая вода)",
            installation_place="Санузел",
            last_reading_value=98.0,
            last_reading_date=date(2026, 8, 20),
            verification_date_valid_until=date(2028, 4, 12),
            unit="м³",
            decimal_digits=3
        ),
        "meter-el-1": MeterBase(
            id="meter-el-1",
            meter_type=MeterType.ELECTRICITY_MULTI,
            serial_number="01458291",
            name="Электроэнергия (Меркурий 208)",
            installation_place="Щит на лестничной клетке",
            last_reading_value=1840.0,
            last_reading_date=date(2026, 8, 20),
            verification_date_valid_until=date(2032, 11, 5),
            unit="кВт*ч",
            decimal_digits=1
        ),
        "meter-heat-1": MeterBase(
            id="meter-heat-1",
            meter_type=MeterType.HEAT,
            serial_number="7741209",
            name="Отопление (Теплосчетчик)",
            installation_place="Коридор",
            last_reading_value=14.2,
            last_reading_date=date(2026, 8, 20),
            verification_date_valid_until=date(2027, 9, 30),
            unit="Гкал",
            decimal_digits=2
        ),
        "meter-gas-1": MeterBase(
            id="meter-gas-1",
            meter_type=MeterType.GAS,
            serial_number="5540912",
            name="Газоснабжение (ВК-G4)",
            installation_place="Кухня",
            last_reading_value=340.0,
            last_reading_date=date(2026, 8, 20),
            verification_date_valid_until=date(2030, 6, 15),
            unit="м³",
            decimal_digits=3
        )
    }
    INITIAL_METERS.clear()
    INITIAL_METERS.update({k: v.model_copy() for k, v in pristine.items()})
    meter_service._meters = {k: v.model_copy() for k, v in pristine.items()}
    meter_service._history = []


class VerificationRunner:
    def __init__(self):
        self.client = TestClient(app)
        self.results = []
        self.start_time = time.time()

    def record(self, category: str, test_name: str, passed: bool, details: str = ""):
        self.results.append({
            "category": category,
            "name": test_name,
            "passed": passed,
            "details": details
        })
        status_marker = f"{GREEN}✓ PASS{RESET}" if passed else f"{RED}✗ FAIL{RESET}"
        print(f"  [{status_marker}] {test_name}: {details}")

    # ================= 1. REST Endpoints Verification =================
    def verify_endpoints(self):
        print(f"\n{BOLD}{CYAN}=== 1. FastAPI REST Endpoints Verification ==={RESET}")
        reset_meters_state()

        # 1.1 /api/health
        res = self.client.get("/api/health")
        passed = (res.status_code == 200 and res.json().get("status") == "healthy")
        self.record("Endpoints", "GET /api/health", passed, f"status={res.status_code}, data={res.json().get('status')}")

        # 1.2 /api/meters
        res = self.client.get("/api/meters")
        passed = (res.status_code == 200 and len(res.json()) >= 4)
        self.record("Endpoints", "GET /api/meters", passed, f"status={res.status_code}, count={len(res.json())}")

        # 1.3 /api/meters/submit
        payload = {
            "meter_id": "meter-khvs-1",
            "reading_value": 145.5,
            "submission_channel": "verify_script"
        }
        res = self.client.post("/api/meters/submit", json=payload)
        passed = (res.status_code == 200 and res.json().get("is_valid") is True)
        self.record("Endpoints", "POST /api/meters/submit", passed, f"status={res.status_code}, accepted={res.json().get('current_value')}")

        # 1.4 /api/ai/scan
        payload = {"device_type_hint": "cold_water"}
        res = self.client.post("/api/ai/scan", json=payload)
        data = res.json()
        passed = (res.status_code == 200 and "recognized_reading" in data and "recognized_serial_number" in data)
        self.record("Endpoints", "POST /api/ai/scan", passed, f"status={res.status_code}, serial={data.get('recognized_serial_number')}")

        # 1.5 /api/arshin/check
        res = self.client.get("/api/arshin/check?serial=2809142")
        data = res.json()
        passed = (res.status_code == 200 and data.get("shield_color") == "green")
        self.record("Endpoints", "GET /api/arshin/check", passed, f"status={res.status_code}, shield={data.get('shield_color')}")

        # 1.6 /api/billing/parse-qr
        qr_sample = (
            "ST00012|Name=ООО УК ДОМОВОЙ СЕРВИС|PersonalAcc=40821810938000012345|"
            "BIC=044525225|CorrespAcc=30101810400000000225|PayeeINN=7701234567|"
            "Sum=485050|PersAcc=1004567890|Period=092026"
        )
        res = self.client.post("/api/billing/parse-qr", json={"qr_payload": qr_sample})
        data = res.json()
        passed = (res.status_code == 200 and data.get("is_40821_split_supported") is True and data.get("is_valid_gost") is True)
        self.record("Endpoints", "POST /api/billing/parse-qr", passed, f"status={res.status_code}, 40821_split={data.get('is_40821_split_supported')}")

        # 1.7 /api/uk/inspector-act
        act_payload = {
            "meter_id": "meter-khvs-1",
            "reading_value": 145.8,
            "address": "г. Москва, ул. Ленина, д. 42, кв. 15",
            "inspector_name": "Контролер Смирнов А. В.",
            "gps_coordinates": "55.7558° N, 37.6173° E"
        }
        res = self.client.post("/api/uk/inspector-act", json=act_payload)
        data = res.json()
        passed = (res.status_code == 201 and data.get("export_1c_ready") is True and len(data.get("photo_hash_sha256", "")) == 64)
        self.record("Endpoints", "POST /api/uk/inspector-act", passed, f"status={res.status_code}, act={data.get('act_id')}, 1c={data.get('export_1c_ready')}")

        # 1.8 /api/guest/generate
        res = self.client.post("/api/guest/generate", json={"property_id": "flat-42-15", "tenant_name": "Тестовый Арендатор"})
        data = res.json()
        passed = (res.status_code == 200 and "guest_token" in data and "direct_max_link" in data)
        self.record("Endpoints", "POST /api/guest/generate", passed, f"status={res.status_code}, link={data.get('direct_max_link')}")

        # 1.9 /api/tickets (POST & GET)
        ticket_payload = {
            "author_name": "Иванов И. И.",
            "address": "ул. Ленина, д. 42, кв. 15",
            "phone": "+79991234567",
            "description": "Протечка трубы стояка в санузле",
            "category": "water_supply",
            "priority": "urgent"
        }
        res_post = self.client.post("/api/tickets", json=ticket_payload)
        res_get = self.client.get("/api/tickets")
        passed = (res_post.status_code == 201 and res_get.status_code == 200 and len(res_get.json()) >= 1)
        self.record("Endpoints", "POST & GET /api/tickets", passed, f"created={res_post.json().get('id')}, total={len(res_get.json())}")

        # 1.10 /api/auth/verify-init-data (HMAC-SHA256)
        token = settings.BOT_TOKEN or "test_token_123"
        raw_auth = {
            "auth_date": str(int(time.time())),
            "query_id": "verify_all_query",
            "user": json.dumps({"id": 999888, "first_name": "Аудитор"})
        }
        signed_init_data = generate_init_data(raw_auth, token)
        res_auth_valid = self.client.get("/api/auth/verify-init-data", headers={"X-Init-Data": signed_init_data})
        res_auth_invalid = self.client.get("/api/auth/verify-init-data", headers={"X-Init-Data": "invalid=signature&hash=0000"})
        passed = (res_auth_valid.status_code == 200 and res_auth_invalid.status_code == 401)
        self.record("Endpoints", "GET /api/auth/verify-init-data", passed, f"valid={res_auth_valid.status_code}, invalid_rejected={res_auth_invalid.status_code}")

        # 1.11 /api/bot/webhook
        webhook_payload = {
            "update_type": "bot_started",
            "user": {"id": 777111, "first_name": "Верификатор"},
            "chat_id": 777111
        }
        from max_bot_sdk.client import MaxBotClient
        orig_send = MaxBotClient.send_message
        try:
            MaxBotClient.send_message = lambda *args, **kwargs: {"status": "ok"}
            res_webhook = self.client.post("/api/bot/webhook", json=webhook_payload)
        finally:
            MaxBotClient.send_message = orig_send
        passed = (res_webhook.status_code == 200 and res_webhook.json().get("status") == "ok")
        self.record("Endpoints", "POST /api/bot/webhook", passed, f"status={res_webhook.status_code}, response={res_webhook.json()}")

        # 1.12 /api/profile & /api/profile/switch-property
        res_prof = self.client.get("/api/profile?user_id=100456")
        res_switch = self.client.post("/api/profile/switch-property", json={
            "user_id": 100456,
            "property_id": "prop-dacha-8"
        })
        passed = (
            res_prof.status_code == 200 and
            res_switch.status_code == 200 and
            res_switch.json().get("active_property_id") == "prop-dacha-8"
        )
        self.record("Endpoints", "GET & POST /api/profile", passed, f"status={res_prof.status_code}, switched_prop=prop-dacha-8")


    # ================= 2. Core Scenarios Verification =================
    def verify_scenarios(self):
        print(f"\n{BOLD}{CYAN}=== 2. Core User Scenarios Verification ==={RESET}")
        reset_meters_state()

        # Scenario 1: Direct drop photo & red roller cutoff
        print(f"\n  {YELLOW}▶ Scenario 1: Direct Drop Photo & 1000x Overbilling Prevention{RESET}")
        scan_res = self.client.post("/api/ai/scan", json={"device_type_hint": "cold_water"})
        assert scan_res.status_code == 200
        scan_data = scan_res.json()

        # User submits reading with unscaled 8 digits (145789 -> 145 m³ + 789 liters)
        submit_res = self.client.post("/api/meters/submit", json={
            "meter_id": "meter-khvs-1",
            "reading_value": 145789.0,
            "submission_channel": "chat_photo_drop"
        })
        sub_data = submit_res.json()
        s1_passed = (
            sub_data.get("is_valid") is True and
            sub_data.get("red_roller_filtered") is True and
            sub_data.get("current_value") == 145.0
        )
        self.record(
            "Scenarios",
            "Scenario 1: Direct Drop Photo & Red Roller Filtering",
            s1_passed,
            f"input=145789.0 -> filtered={sub_data.get('current_value')} m³, red_roller_filtered={sub_data.get('red_roller_filtered')}"
        )

        # Scenario 2: «Зеленый Щит» АРШИН (Anti-fraud verification)
        print(f"\n  {YELLOW}▶ Scenario 2: «Зеленый Щит» АРШИН Anti-Fraud{RESET}")
        res_legit = self.client.get("/api/arshin/check?serial=2809142")
        legit_data = res_legit.json()
        legit_ok = (legit_data.get("status") in ("valid", "verified") and legit_data.get("shield_color") == "green")

        res_fraud = self.client.get("/api/arshin/check?serial=991201")
        fraud_data = res_fraud.json()
        fraud_ok = (
            fraud_data.get("status") == "expired" and
            fraud_data.get("shield_color") == "red" and
            fraud_data.get("is_fraud_warning") is True
        )
        s2_passed = (legit_ok and fraud_ok)
        self.record(
            "Scenarios",
            "Scenario 2: Зеленый Щит АРШИН (Legit vs Expired Fraud Warning)",
            s2_passed,
            f"legit_shield={legit_data.get('shield_color')}, fraud_shield={fraud_data.get('shield_color')}, alert={fraud_data.get('is_fraud_warning')}"
        )

        # Scenario 3: Гостевой доступ для арендаторов
        print(f"\n  {YELLOW}▶ Scenario 3: Гостевой доступ для арендаторов (No ESIA barrier){RESET}")
        gen_res = self.client.post("/api/guest/generate", json={
            "property_id": "flat-42-15",
            "tenant_name": "Арендатор Сидоров"
        })
        gen_data = gen_res.json()
        token = gen_data.get("guest_token")
        val_res = self.client.get(f"/api/guest/{token}")
        val_data = val_res.json()
        s3_passed = (
            gen_res.status_code == 200 and
            val_res.status_code == 200 and
            val_data.get("status") == "active" and
            "submit_meter_readings" in val_data.get("allowed_actions", [])
        )
        self.record(
            "Scenarios",
            "Scenario 3: Гостевой доступ для арендаторов",
            s3_passed,
            f"token={token[:16]}..., status={val_data.get('status')}, actions={val_data.get('allowed_actions')}"
        )

        # Scenario 4: АРМ Обходчика УК
        print(f"\n  {YELLOW}▶ Scenario 4: АРМ Обходчика УК (Цифровой акт с GPS и SHA-256){RESET}")
        act_res = self.client.post("/api/uk/inspector-act", json={
            "meter_id": "meter-khvs-1",
            "reading_value": 145.82,
            "address": "г. Москва, ул. Ленина, д. 42, кв. 15",
            "inspector_name": "Инспектор Кузнецов Д. С.",
            "gps_coordinates": "55.7558° N, 37.6173° E"
        })
        act_data = act_res.json()
        s4_passed = (
            act_res.status_code == 201 and
            act_data.get("export_1c_ready") is True and
            len(act_data.get("photo_hash_sha256", "")) == 64 and
            "55.7558" in act_data.get("gps", "")
        )
        self.record(
            "Scenarios",
            "Scenario 4: АРМ Обходчика УК (Цифровой акт)",
            s4_passed,
            f"act_id={act_data.get('act_id')}, 1c_ready={act_data.get('export_1c_ready')}, sha256={act_data.get('photo_hash_sha256')[:16]}..."
        )

        # Scenario 5: Профиль жителя и привязка адресов (1-tap switch & ЕЛС)
        print(f"\n  {YELLOW}▶ Scenario 5: Профиль жителя и мульти-адреса (ЕЛС ГИС ЖКХ){RESET}")
        s5_prof = self.client.get("/api/profile?user_id=881122").json()
        s5_add = self.client.post("/api/profile/add-property", json={
            "user_id": 881122,
            "address": "г. Москва, Ломоносовский пр-т, д. 27, кв. 104",
            "els": "9988776655"
        })
        s5_passed = (
            s5_add.status_code == 201 and
            s5_add.json().get("active_property_id", "").startswith("prop-") and
            len(s5_add.json().get("properties", [])) >= 3
        )
        self.record(
            "Scenarios",
            "Scenario 5: Профиль жителя и мульти-адреса (ЕЛС)",
            s5_passed,
            f"user_id=881122, props_count={len(s5_add.json().get('properties', []))}, active={s5_add.json().get('active_property_id')}"
        )


    # ================= 3. Button Text Length Verification =================
    def verify_button_length(self):
        print(f"\n{BOLD}{CYAN}=== 3. MAX Messenger Button Text Length Audit (<= 18 chars) ==={RESET}")
        handlers_path = ROOT_DIR / "app" / "bot" / "handlers.py"
        with open(handlers_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())

        violators = []
        tested_buttons = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and hasattr(node.func, "attr") and node.func.attr in ("open_app", "callback", "link"):
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    label = node.args[0].value
                    tested_buttons.append((node.lineno, label, len(label)))
                    if len(label) > 18:
                        violators.append((node.lineno, label, len(label)))

        # Also verify live runtime buttons from get_main_menu_keyboard
        kb = get_main_menu_keyboard()
        for row in kb.get("payload", {}).get("buttons", []):
            for b in row:
                t = b.get("text", "")
                tested_buttons.append(("runtime_menu", t, len(t)))
                if len(t) > 18:
                    violators.append(("runtime_menu", t, len(t)))

        # Also verify dynamic meter buttons
        from app.services.meter_service import meter_service
        for m in meter_service.get_all_meters():
            name_short = m.name.split('(')[0].strip()
            if name_short == "Электроэнергия":
                name_short = "Свет"
            elif name_short == "Газоснабжение":
                name_short = "Газ"
            btn_label = f"Сдать {name_short}"
            tested_buttons.append(("runtime_meter", btn_label, len(btn_label)))
            if len(btn_label) > 18:
                violators.append(("runtime_meter", btn_label, len(btn_label)))

        passed = (len(violators) == 0 and len(tested_buttons) > 0)
        self.record(
            "UI Compliance",
            "Inline Button Text Length (<= 18 chars)",
            passed,
            f"audited={len(tested_buttons)} buttons, violators={len(violators)}"
        )

    # ================= Summary & Exit =================
    def print_summary(self) -> int:
        elapsed = time.time() - self.start_time
        total = len(self.results)
        passed_count = sum(1 for r in self.results if r["passed"])
        failed_count = total - passed_count

        print(f"\n{BOLD}{'=' * 65}{RESET}")
        print(f"{BOLD}MAX Smart City ЖКХ Verification Suite — Final Summary{RESET}")
        print(f"{BOLD}{'=' * 65}{RESET}")
        print(f"Total Checks:   {BOLD}{total}{RESET}")
        print(f"Passed:         {GREEN}{BOLD}{passed_count}{RESET}")
        print(f"Failed:         {RED if failed_count > 0 else GREEN}{BOLD}{failed_count}{RESET}")
        print(f"Elapsed Time:   {elapsed:.2f} seconds")
        print(f"{BOLD}{'=' * 65}{RESET}")

        if failed_count == 0:
            print(f"{GREEN}{BOLD}✓ ALL CHECKS PASSED SUCCESSFULLY (100% PASS RATE)!{RESET}\n")
            return 0
        else:
            print(f"{RED}{BOLD}✗ SOME CHECKS FAILED! Please review details above.{RESET}\n")
            return 1


def main():
    runner = VerificationRunner()
    runner.verify_endpoints()
    runner.verify_scenarios()
    runner.verify_button_length()
    sys.exit(runner.print_summary())


if __name__ == "__main__":
    main()
