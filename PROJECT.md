# Project: Hackathon Smart City ЖКХ in MAX

## Architecture
- **Backend**: FastAPI (`app/main.py`), Pydantic v2 schemas (`app/models/schemas.py`), modular domain services (`app/services/` for Arshin, GOST QR, Meter readings, Night flow leaks, Emergency tickets, Guest access, AI vision stub).
- **Bot Service**: Async Long Polling worker (`app/bot/client.py`, `app/bot/handlers.py`) connecting to MAX API (`platform-api2.max.ru`), with Webhook capability (`POST /subscriptions`).
- **Mini-App (MAX WebApp)**: Responsive SPA in MAX UI design standards (`app/static/index.html`, `styles.css`, `app.js`) with `max-web-app.js` bridge integration (`window.WebApp.openCodeReader()`, `HapticFeedback`, `BackButton`, `initData`).
- **Packaging**: Containerized via `Dockerfile` and `docker-compose.yml`, exposed on port `8080`.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Regulatory & Competitor Baseline | 209-ФЗ, 102-ФЗ, 261-ФЗ, ГОСТ Р 56042-2014, 103-ФЗ, ПП РФ № 40, Сбер/ВТБ анализ | M1 | B1 Survey |
| 2 | Direct Drop Photo (Multi-modal) | Chat photo drop supporting cold/hot water, electricity, gas | M1 | B2 Survey |
| 3 | Зеленый Щит АРШИН & Anti-Fraud | OCR serial check, FGIS ARSHIN lookup, fraud alert for expired 991201 | M1 | B2 Survey |
| 4 | АРМ Обходчика УК (Backend) | Digital act generation with GPS coordinates, timestamp, SHA-256 hash, 1C export | M1 | B2/B3 Survey |
| 5 | HMAC-SHA256 initData Validation | Cryptographic validation of WebApp initData with bot secret key | M1 | B3 Survey |
| 6 | Bot Webhook Support | Webhook endpoint (`POST /api/bot/webhook`) and `set_webhook` registration client | M1 | B3 Survey |
| 7 | Camera Viewfinder & Reticle | MAX WebApp camera interface with targeting reticle overlay and torch toggle | M2 | B4 Survey |
| 8 | Split Rollers Display | Reading display separating integer (black) and fractional/liters (red) rollers | M2 | B4 Survey |
| 9 | АРМ Обходчика УК (Frontend) | WebApp UI tab for inspector acts, photo preview, GPS/hash display | M2 | B2/B4 Survey |
| 10 | MAX Bridge Native Integration | Haptic feedback, back button, native QR scanner (`openCodeReader`) | M2 | B4 Survey |
| 11 | Docker & Port Harmonization | Unify port 8080 across Dockerfile, compose, env, and README | M3 | B3/B5 Survey |
| 12 | Hackathon README & Submission Docs | Complete jury walkthrough, commands, architecture, meeting criteria (pp. 9-10) | M3 | B5 Survey |
| 13 | Test Infrastructure & State Isolation | Root conftest.py/pytest.ini, pythonpath=., meter_service state isolation fixture | M4 | B5 Survey |
| 14 | Bot Handlers & Scenarios Test Suite | Automated tests for bot callbacks, inspector acts, HMAC, and all 5 scenarios | M4 | B5 Survey |
| 15 | Autonomous Verification Script | Standalone non-interactive `scripts/verify_all.py` covering all endpoints, exit 0 | M4 | B5 Survey |
| 16 | E2E & Adversarial Hardening | 100% E2E test pass (Tiers 1-4) + Tier 5 adversarial stress testing | M5 | Final Milestone |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Backend Convergence & Regulations | Port inspector act to FastAPI, fix Arshin 991201, HMAC initData, Webhook, multi-modal bot | none | DONE |
| M2 | Mini-App MAX UI & Scenarios | Viewfinder reticle, torch toggle, rollers UI, inspector tab, MAX bridge | M1 | DONE |
| M3 | Packaging & Documentation Sync | Dockerfile, docker-compose, README.md, .env.example port 8080 harmonization | M1 | DONE |
| M4 | Comprehensive Test Suite & Script | conftest.py, test isolation, bot handler tests, verify_all.py, TEST_READY.md | M1, M2, M3 | DONE |
| M5 | Final Verification & Hardening | 100% E2E test pass (Tiers 1-4) + Tier 5 adversarial coverage hardening | M4 | DONE |

## Interface Contracts
### WebApp ↔ FastAPI Backend (`app/api/routes.py`)
- `POST /api/uk/inspector-act`:
  - Request: `InspectorActCreateRequest(meter_id: str, reading_value: float, address: str, inspector_name: str, photo_base64: Optional[str])`
  - Response: `InspectorActResponse(act_id: str, timestamp: str, gps: str, photo_hash_sha256: str, meter_id: str, reading_value: float, status: str, export_1c_ready: bool)`
- `Dependency verify_max_init_data(x_init_data: Header)`:
  - Validates HMAC-SHA256 using SHA256(bot_token, "WebAppData")
- `POST /api/bot/webhook`:
  - Request: `dict` (Update payload from MAX platform API)
  - Response: `{"status": "ok"}`
- `GET /api/arshin/check?serial=991201`:
  - Response: `ArshinCheckResponse(status="expired", shield_color="red", is_fraud_warning=True)`

## Code Layout
- `app/` - Production FastAPI service, Pydantic schemas, domain services, bot worker, and static SPA.
- `app/api/` - REST API routers and security dependencies.
- `app/models/` - Domain enums and Pydantic v2 schemas.
- `app/services/` - Business logic (arshin, gost_qr, meter_service, night_flow, ticket_service, guest_service, ai_vision).
- `app/bot/` - MAX Bot client and message/callback handlers.
- `app/static/` - WebApp HTML, CSS, and JS.
- `tests/` - pytest test suite (Tiers 1-4).
- `scripts/` - Autonomous verification script (`verify_all.py`).
- `docs/` - System architecture, legal analysis, and evidence dossier.
