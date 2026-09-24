# E2E Test Infra: Hackathon Smart City ЖКХ in MAX

## Test Philosophy
- Opaque-box, requirement-driven. Derives from `ORIGINAL_REQUEST.md` and regulatory standards.
- Methodology: Category-Partition + Boundary Value Analysis + Pairwise Combinatorial + Real-World Workload Testing.
- Non-interactive, autonomous execution exiting code 0 upon success.

## Feature Inventory & Test Mapping
| # | Feature | Source | Tier 1 (Coverage) | Tier 2 (Boundary) | Tier 3 (Pairwise) | Tier 4 (Workload) |
|---|---------|--------|:-----------------:|:-----------------:|:-----------------:|:-----------------:|
| 1 | Health & Discovery | `ORIGINAL_REQUEST §B3` | 5 | 5 | ✓ | ✓ |
| 2 | Meters & Monotonicity | `ORIGINAL_REQUEST §B2, B3` | 5 | 5 | ✓ | ✓ |
| 3 | Red Rollers Decimal Filter | `ORIGINAL_REQUEST §B2, B4` | 5 | 5 | ✓ | ✓ |
| 4 | AI Vision Bounding Box & OCR | `ORIGINAL_REQUEST §B3` | 5 | 5 | ✓ | ✓ |
| 5 | FGIS Arshin & Green Shield | `ORIGINAL_REQUEST §B1, B2` | 5 | 5 | ✓ | ✓ |
| 6 | GOST R 56042-2014 QR & 40821 | `ORIGINAL_REQUEST §B1, B3` | 5 | 5 | ✓ | ✓ |
| 7 | Bank of Russia Checksum 7-1-3 | `ORIGINAL_REQUEST §B1` | 5 | 5 | ✓ | ✓ |
| 8 | Night Flow ODPU & Napkin Test | `ORIGINAL_REQUEST §B1, B2` | 5 | 5 | ✓ | ✓ |
| 9 | Emergency Tickets & SLA (PP 40)| `ORIGINAL_REQUEST §B1, B3` | 5 | 5 | ✓ | ✓ |
| 10| Tenant Guest Access (no ESIA) | `ORIGINAL_REQUEST §B2` | 5 | 5 | ✓ | ✓ |
| 11| Inspector Act (GPS, SHA-256) | `ORIGINAL_REQUEST §B2, B3` | 5 | 5 | ✓ | ✓ |
| 12| HMAC-SHA256 initData Auth | `ORIGINAL_REQUEST §B3` | 5 | 5 | ✓ | ✓ |
| 13| MAX Bot Handlers & Drop Photo | `ORIGINAL_REQUEST §B2, B3` | 5 | 5 | ✓ | ✓ |

## Test Architecture
- **Test Runner**: `.venv/bin/pytest tests/` and standalone runner `python3 scripts/verify_all.py`
- **Isolation**: Root `conftest.py` with `autouse=True` fixture resetting `MeterService` in-memory state before every test.
- **Offline / Mocking**: Fully hermetic mocks for MAX Bot platform API when running offline without internet/token.
- **Pass / Fail Semantics**: Zero assertions failed, exit code 0, all 5 business scenarios validated.

## Coverage Thresholds
- **Tier 1 (Feature Coverage)**: ≥5 test cases per feature covering happy paths and core contracts.
- **Tier 2 (Boundary & Corner)**: ≥5 test cases per feature (monotonicity violation, red roller >10000, expired meter 991201, corrupt QR, bank checksum invalid, night flow thresholds).
- **Tier 3 (Cross-Feature Combinations)**: Multi-step workflows (e.g. photo scan -> Arshin check -> reading submission -> ticket creation -> guest access).
- **Tier 4 (Real-World Workloads)**: End-to-end verification of the 5 hackathon scenarios in sequence.
