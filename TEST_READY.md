# TEST_READY — MAX Smart City ЖКХ Test Verification Dossier

**Дата формирования:** 2026-09-19  
**Статус готовности:** ✅ **100% READY (ALL TESTS PASSING)**  
**Проект:** MAX Умный Дом — Хакатон «Умный город» (ЖКХ в мессенджере MAX)  
**Регламент:** Соответствие требованиям хакатона (стр. 9–10)  

---

## 1. Команды для запуска тестирования

### 1.1 Запуск полного набора тестов Pytest
```bash
pytest -v
```
*(или `.venv/bin/pytest -v` при использовании виртуального окружения)*  
**Результат:** **84 passed** из 84 тестов (~0.50 с).

### 1.2 Запуск автономного проверочного скрипта
```bash
python3 scripts/verify_all.py
```
**Результат:** **16 проверок из 16 пройдены** (100% PASS RATE, ~0.04 с, код возврата `0`).  
Скрипт полностью неинтерактивен, не требует ввода пользователя, автоматически проверяет все 11 REST-эндпоинтов, 4 ключевых сценария и ограничение длины кнопок MAX Bot (< 25 символов).

---

## 2. Сводка покрытия по уровням (Tiers 1–4)

| Tier | Категория | Описание покрытия | Число тестов | Статус |
|:---:|---|---|:---:|:---:|
| **Tier 1** | **Domain Core & Services** | Модели Pydantic v2, монотонность показаний, алгоритм отсечения красных барабанов (защита от завышения в 1000 раз), реестр ФГИС «АРШИН», парсинг ГОСТ Р 56042-2014, сплитование по 103-ФЗ | 22 | ✅ PASS (100%) |
| **Tier 2** | **API & MAX Bot SDK** | 11 REST-эндпоинтов FastAPI, спецификация OpenAPI 3.1, диспетчеризация вебхуков, клиент `max_bot_sdk`, типизированные события `Update`, клавиатуры | 28 | ✅ PASS (100%) |
| **Tier 3** | **Security & Forensics** | Валидация криптографической подписи HMAC-SHA256 (`initData`), целостность актов обходчика УК (SHA-256 + GPS), аудит длины кнопок (< 25 симв.), стандарты MAX UI Bridge | 17 | ✅ PASS (100%) |
| **Tier 4** | **Stress & Boundary** | Граничные условия расхода, поверка газового счетчика (ВК-G4), нулевое потребление, попытки передачи некорректных форматов, устойчивость к гонкам | 17 | ✅ PASS (100%) |
| **Итого** | **Полный тестовый комплект** | **Комплексная автоматизированная верификация всей платформы** | **84** | ✅ **100% PASS** |

---

## 3. Чек-лист реализованных и верифицированных функций

- [x] **Единая гармонизация портов (8080)**:
  - Порт `8080` синхронизирован в `Dockerfile`, `docker-compose.yml`, `.env.example` и `README.md`.
  - Healthcheck контейнера настроен на `http://localhost:8080/api/health`.
- [x] **Исключение модуля «Ночной дозор»**:
  - Вкладка «Ночной дозор» скрыта/удалена из активной пользовательской навигации `app/static/index.html` по директиве пользователя.
  - Навигационная панель содержит 5 активных вкладок (Счетчики, Антифрод, Оплата, Заявки, Обходчик).
- [x] **Лаконичные кнопки бота (< 25 символов)**:
  - Все инлайн-кнопки в `app/bot/handlers.py` сокращены (например, `[📱 Мини-приложение]`, `[🛡️ Проверить АРШИН]`, `[💧 Передать ХВС]`, `[📋 Акт обходчика]`).
  - Текст кнопок гарантированно не обрезается в мобильном интерфейсе мессенджера MAX.
- [x] **Изоляция состояния тестов (`conftest.py`)**:
  - В корне проекта создан `conftest.py` с `pythonpath = .` и `autouse`-фикстурой сброса `meter_service` к эталонному состоянию перед каждым тестом.
  - Устранена флаки-проблема мутации синглтона.
- [x] **Сценарий 1: Прямой дроп фото**:
  - Распознавание счетчиков воды, света и газа через AI-пайплайн.
  - Автоматическое отсечение красных барабанов (литров).
- [x] **Сценарий 2: «Зеленый Щит» АРШИН (Антифрод)**:
  - Сверка заводского номера с реестром ФГИС «АРШИН» Росстандарта (102-ФЗ).
  - «Зеленый Щит» для поверенных приборов (№ 2809142), блокировка и предупреждение о мошенниках для просроченных (№ 991201) и фальшивых (№ 000000).
- [x] **Сценарий 3: Гостевой доступ для арендаторов**:
  - Генерация прямой ссылки для мессенджера MAX без входа через Госуслуги (ЕСИА).
  - Безопасная передача показаний и оплата без доступа к личным документам собственника.
- [x] **Сценарий 4: АРМ Обходчика УК**:
  - Формирование цифрового акта контрольного осмотра с геометкой GPS, таймстемпом ISO-8601, хэшем фото SHA-256 и готовностью к экспорту в 1С:ЖКХ.
- [x] **Сценарий 5: Оплата по ГОСТ Р 56042-2014 и спецсчет 40821**:
  - Парсинг QR-кода платежки, проверка контрольного ключа расчетного счета Банка России.
  - Расщепление платежа на специальный банковский счет 40821 (103-ФЗ, 59-ФЗ).
- [x] **Безопасность WebApp (HMAC-SHA256)**:
  - Криптографическая валидация `initData` по алгоритму SHA256(bot_token, "WebAppData").
- [x] **Автономный проверочный скрипт `scripts/verify_all.py`**:
  - 100% неинтерактивное прохождение всех проверок с кодом возврата 0.

---

## 4. Протокол последнего прогона тестов

```
============================= test session starts ==============================
platform darwin -- Python 3.11.9, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/alpha7en/Documents/antigravity/max хакатон
plugins: anyio-4.15.1
collected 84 items

tests/test_all_scenarios.py ........                                     [  9%]
tests/test_api_routes.py ..............                                  [ 26%]
tests/test_arshin_antifraud.py ....                                      [ 30%]
tests/test_audit_edge_cases.py .......                                   [ 39%]
tests/test_gost_qr.py ......                                             [ 46%]
tests/test_m2_forensic_integrity.py ......                               [ 53%]
tests/test_max_bot_sdk.py ....                                           [ 58%]
tests/test_meter_service.py .......                                      [ 66%]
tests/test_night_flow.py ...                                             [ 70%]
tests/test_stress_m1_2.py .........................                      [100%]

======================== 84 passed, 2 warnings in 0.42s ========================
```

```
$ python3 scripts/verify_all.py

=== 1. FastAPI REST Endpoints Verification ===
  [✓ PASS] GET /api/health: status=200, data=healthy
  [✓ PASS] GET /api/meters: status=200, count=5
  [✓ PASS] POST /api/meters/submit: status=200, accepted=145.5
  [✓ PASS] POST /api/ai/scan: status=200, serial=2809142
  [✓ PASS] GET /api/arshin/check: status=200, shield=green
  [✓ PASS] POST /api/billing/parse-qr: status=200, 40821_split=True
  [✓ PASS] POST /api/uk/inspector-act: status=201, act=ACT-ЖКХ-1789850884, 1c=True
  [✓ PASS] POST /api/guest/generate: status=200, link=https://max.ru/...
  [✓ PASS] POST & GET /api/tickets: created=TCK-2026-5F57, total=2
  [✓ PASS] GET /api/auth/verify-init-data: valid=200, invalid_rejected=401
  [✓ PASS] POST /api/bot/webhook: status=200, response={'status': 'ok'}

=== 2. Core User Scenarios Verification ===
  ▶ Scenario 1: Direct Drop Photo & 1000x Overbilling Prevention
  [✓ PASS] Scenario 1: Direct Drop Photo & Red Roller Filtering: input=145789.0 -> filtered=145.0 m³, red_roller_filtered=True

  ▶ Scenario 2: «Зеленый Щит» АРШИН Anti-Fraud
  [✓ PASS] Scenario 2: Зеленый Щит АРШИН (Legit vs Expired Fraud Warning): legit_shield=green, fraud_shield=red, alert=True

  ▶ Scenario 3: Гостевой доступ для арендаторов (No ESIA barrier)
  [✓ PASS] Scenario 3: Гостевой доступ для арендаторов: token=guest_..., status=active, actions=['submit_meter_readings', 'view_bills', 'pay_split_sbp']

  ▶ Scenario 4: АРМ Обходчика УК (Цифровой акт с GPS и SHA-256)
  [✓ PASS] Scenario 4: АРМ Обходчика УК (Цифровой акт): act_id=ACT-ЖКХ-1789850884, 1c_ready=True, sha256=731de4f6...

=== 3. MAX Messenger Button Text Length Audit (< 25 chars) ===
  [✓ PASS] Inline Button Text Length (< 25 chars): audited=27 buttons, violators=0

=================================================================
MAX Smart City ЖКХ Verification Suite — Final Summary
=================================================================
Total Checks:   16
Passed:         16
Failed:         0
Elapsed Time:   0.04 seconds
=================================================================
✓ ALL CHECKS PASSED SUCCESSFULLY (100% PASS RATE)!
```
