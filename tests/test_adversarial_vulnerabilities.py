"""
Empirical Vulnerability and Adversarial Challenge Harness.
Authored by challenger_pipeline_e2e_1 (Empirical Challenger).

This module empirically documents and verifies failure modes, edge cases,
and vulnerabilities discovered during stress-testing of the MAX Smart City pipeline.
"""

import hashlib
import uuid
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models.domain import MeterType, VerificationStatus
from app.services.arshin import arshin_service
from app.services.gost_qr import verify_bank_account_checksum, parse_gost_qr_payload
from app.services.meter_service import meter_service
from app.services.ticket_service import ticket_service

client = TestClient(app, raise_server_exceptions=False)


class TestAdversarialVulnerabilities:
    """
    Empirical tests capturing critical edge case vulnerabilities.
    """

    def test_vulnerability_nan_causes_unhandled_500_in_submit(self):
        """
        VULNERABILITY:
        Sending NaN in reading_value causes ValueError: cannot convert float NaN to integer
        at meter_service.py:96 (scaled = float(int(raw_value) // 1000)).
        This escapes unhandled and crashes with HTTP 500 Internal Server Error.
        """
        resp = client.post("/api/meters/submit", json={"meter_id": "meter-khvs-1", "reading_value": "NaN"})
        assert resp.status_code == 500, f"Expected 500 on unhandled NaN, got {resp.status_code}"

    def test_vulnerability_infinity_causes_unhandled_500_in_submit(self):
        """
        VULNERABILITY:
        Sending Infinity in reading_value causes OverflowError: cannot convert float infinity to integer
        at meter_service.py:96.
        This escapes unhandled and crashes with HTTP 500 Internal Server Error.
        """
        resp = client.post("/api/meters/submit", json={"meter_id": "meter-khvs-1", "reading_value": "Infinity"})
        assert resp.status_code == 500, f"Expected 500 on unhandled Infinity, got {resp.status_code}"

    def test_vulnerability_hash_prefix_bypasses_arshin_expired_meter_991201(self):
        """
        VULNERABILITY / BYPASS:
        clean_serial in ArshinService only strips '№' and spaces, but NOT '#', 'N', or 'SN:'.
        When querying expired meter 991201 as '#991201', it misses the mock database key,
        falls back to default unknown serial handler, and falsely reports VERIFIED with green shield!
        """
        # 1. Clean serial 991201 correctly shows EXPIRED and red shield
        clean_res = arshin_service.check_verification("991201")
        assert clean_res.status == VerificationStatus.EXPIRED
        assert clean_res.shield_color == "red"

        # 2. Adversarial query '#991201' bypasses the check and returns VERIFIED green shield!
        bypassed_res = arshin_service.check_verification("#991201")
        assert bypassed_res.status == VerificationStatus.VERIFIED
        assert bypassed_res.shield_color == "green"
        assert bypassed_res.serial_number == "#991201"

    def test_vulnerability_non_digit_bik_crashes_checksum_function(self):
        """
        VULNERABILITY:
        verify_bank_account_checksum does not validate that BIK and account contain only digits.
        If length is 9 and 20 respectively but contains letters (e.g. '04452522A' or account with 'A'),
        int(control_string[i]) raises an unhandled ValueError.
        """
        with pytest.raises(ValueError) as exc_info:
            verify_bank_account_checksum("044525225", "4070281093800001234A")
        assert "invalid literal for int()" in str(exc_info.value)

    def test_gps_suffix_formatting_divergence(self):
        """
        EDGE CASE:
        Comparing GPS suffix between routes.py and inspector_service.py:
        - routes.py appends: '(Метка подтверждена)'
        - inspector_service.py appends: '(Метка подтверждена ГЛОНАСС/GPS)'
        """
        from app.models.schemas import InspectorActCreateRequest
        from app.services.inspector_service import inspector_service

        req = InspectorActCreateRequest(gps_coordinates="55.7558° N, 37.6173° E")
        act = inspector_service.create_act(req)
        assert "ГЛОНАСС/GPS" in act.gps_coordinates

        resp = client.post("/api/uk/inspector-act", json={"gps_coordinates": "55.7558° N, 37.6173° E"})
        assert resp.status_code == 201
        assert "ГЛОНАСС/GPS" not in resp.json()["gps_coordinates"]
        assert "(Метка подтверждена)" in resp.json()["gps_coordinates"]

    def test_large_photo_base64_sha256_hashing(self):
        """
        BOUNDARY TEST:
        Verify that large photo base64 strings (1MB payload) hash deterministically
        without memory overflow or timeout.
        """
        large_payload = "A" * 1_000_000
        expected_hash = hashlib.sha256(large_payload.encode("utf-8")).hexdigest()

        resp = client.post(
            "/api/uk/inspector-act",
            json={
                "meter_id": "meter-khvs-1",
                "reading_value": 145.0,
                "photo_base64": large_payload
            }
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["photo_hash_sha256"] == expected_hash
        assert data["crypto_hash"] == expected_hash

    def test_ticket_id_entropy_analysis(self):
        """
        DESIGN CHALLENGE:
        Ticket ID is formatted as TCK-2026-XXXX where XXXX is 4 hex chars from uuid4.
        Only 16 bits of entropy (65,536 combinations). Birthday paradox guarantees
        high probability of duplicate IDs in production environments.
        """
        from app.models.schemas import TicketCreateRequest, TicketPriority
        for i in range(10):
            t = ticket_service.create_ticket(
                TicketCreateRequest(
                    category="Тест энтропии",
                    description=f"Тестовая заявка {i}",
                    priority=TicketPriority.PLANNED
                )
            )
            assert len(t.id) == 13  # 'TCK-2026-XXXX'
            assert t.id.startswith("TCK-2026-")
