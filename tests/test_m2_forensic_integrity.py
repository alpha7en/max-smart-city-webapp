"""
Forensic Integrity Verification Test Suite for Milestone 2:
1. DOM elements and CSS animations (reticle, laserScan, torch-active, roller drums).
2. Inspector Act UI contracts: POST /api/uk/inspector-act, SHA-256 calculation, GPS coords.
3. apiFetch and X-Init-Data injection contracts.
4. Arshin anti-fraud check for expired serial 991201.
5. Absence of facades, dummy returns, and hardcoded test shortcuts.
"""

import os
import re
import json
import hashlib
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_01_viewfinder_dom_and_css():
    """Verify camera viewfinder DOM elements, targeting reticle, laserScan, and torch."""
    with open("app/static/index.html", "r", encoding="utf-8") as f:
        html = f.read()

    # Viewfinder frame and reticle
    assert 'id="viewfinder-camera-frame"' in html
    assert 'class="viewfinder-reticle aim-reticle"' in html
    assert 'class="corner top-left"' in html
    assert 'class="corner top-right"' in html
    assert 'class="corner bottom-left"' in html
    assert 'class="corner bottom-right"' in html
    assert 'class="laser-scan-line"' in html
    assert 'class="reticle-caption"' in html
    assert 'id="btn-toggle-torch"' in html
    assert 'id="btn-capture-frame"' in html
    assert 'id="btn-cycle-meter-type"' in html

    with open("app/static/styles.css", "r", encoding="utf-8") as f:
        css = f.read()

    # CSS rules
    assert ".viewfinder-frame.torch-active" in css
    assert ".btn-circle.btn-torch.active" in css
    assert ".viewfinder-reticle" in css
    assert ".laser-scan-line" in css
    assert "@keyframes laserScan" in css

def test_02_split_rollers_dom_css_and_logic():
    """Verify mechanical split counter rollers (integer m3 vs decimal liters) and calculation breakdown."""
    with open("app/static/index.html", "r", encoding="utf-8") as f:
        html = f.read()

    assert 'id="roller-box"' in html
    assert 'id="roller-black-group"' in html
    assert 'class="roller-dot"' in html
    assert 'id="roller-red-group"' in html
    assert 'id="scan-calc-breakdown"' in html
    assert 'id="calc-prev-val"' in html
    assert 'id="calc-delta-val"' in html
    assert 'id="calc-cost-val"' in html

    with open("app/static/styles.css", "r", encoding="utf-8") as f:
        css = f.read()

    assert ".roller-container" in css
    assert ".roller-drum" in css
    assert ".roller-stepper" in css
    assert ".roller-digit" in css
    assert ".roller-black" in css
    assert ".roller-red" in css
    assert ".calculation-breakdown-card" in css

    with open("app/static/app.js", "r", encoding="utf-8") as f:
        js = f.read()

    assert "function renderRollers(" in js
    assert "function buildRollerDrums()" in js
    assert "function stepRollerDigit(" in js
    assert "function updateCalculationBreakdown()" in js

def test_03_inspector_arm_ui_and_backend_contract():
    """Verify АРМ Обходчика УК UI, SHA-256 hash preview, photo upload, and POST /api/uk/inspector-act."""
    with open("app/static/index.html", "r", encoding="utf-8") as f:
        html = f.read()

    assert 'id="inspector-serial-input"' in html
    assert 'id="btn-inspector-photo"' in html
    assert 'id="btn-inspector-sample-photo"' in html
    assert 'id="inspector-photo-preview"' in html
    assert 'id="inspector-hash-preview"' in html
    assert 'id="btn-generate-inspector-act"' in html
    assert 'id="inspector-act-result"' in html
    assert 'id="act-export-badge"' in html
    assert 'id="act-photo-display"' in html

    with open("app/static/app.js", "r", encoding="utf-8") as f:
        js = f.read()

    assert "function initInspectorArm()" in js
    assert "async function computeSha256(" in js
    assert "crypto.subtle.digest" in js
    assert "async function updateInspectorHashPreview()" in js
    assert 'apiFetch("/api/uk/inspector-act"' in js

    # Test backend endpoint with sample photo
    test_photo = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIzMDAiIGhlaWdodD0iMjAwIj48cmVjdCB3aWR0aD0iMTAwJSIgaGVpZ2h0PSIxMDAlIiBmaWxsPSIjMWUyOTNiIi8+PHRleHQgeD0iNTAiIHk9IjEwMCIgZmlsbD0iI2ZmZiIgZm9udC1zaXplPSIyMCI+TUVRVFlSIDI4MDkxNDI8L3RleHQ+PC9zdmc+"
    act_payload = {
        "meter_id": "meter-khvs-1",
        "reading_value": 142.385,
        "address": "г. Москва, ул. Ленина, д. 42, кв. 15",
        "inspector_name": "Смирнов А. В.",
        "gps_coordinates": "55.7558° N, 37.6173° E",
        "photo_base64": test_photo
    }
    r = client.post("/api/uk/inspector-act", json=act_payload)
    assert r.status_code == 201
    data = r.json()
    expected_hash = hashlib.sha256(test_photo.encode("utf-8")).hexdigest()
    assert data["photo_hash_sha256"] == expected_hash
    assert data["export_1c_ready"] is True
    assert "55.7558° N, 37.6173° E" in data["gps"]

def test_04_api_fetch_and_init_data():
    """Verify apiFetch wrapper injects X-Init-Data and wraps all client fetch calls."""
    with open("app/static/app.js", "r", encoding="utf-8") as f:
        js = f.read()

    assert "function getInitData()" in js
    assert "async function apiFetch(" in js
    assert 'opts.headers["X-Init-Data"] = initData;' in js

    # Ensure fetch( is only called inside apiFetch
    fetch_calls = [m.start() for m in re.finditer(r'\bfetch\s*\(', js)]
    assert len(fetch_calls) == 1, f"Found {len(fetch_calls)} direct fetch calls; all should route via apiFetch"

def test_05_arshin_anti_fraud_serial_991201():
    """Verify expired meter 991201 returns expired status and triggers anti-fraud alert in UI."""
    with open("app/static/styles.css", "r", encoding="utf-8") as f:
        css = f.read()

    assert ".shield-card.shield-danger" in css
    assert ".shield-card.shield-expired" in css
    assert ".shield-fraud-alert" in css

    with open("app/static/app.js", "r", encoding="utf-8") as f:
        js = f.read()

    assert 'apiFetch(`/api/arshin/check?serial=${encodeURIComponent(serial)}`)' in js
    assert 'data.is_fraud_warning && data.status === "expired"' in js
    assert 'class="shield-fraud-alert"' in js

    # Test backend response
    r = client.get("/api/arshin/check?serial=991201")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "expired"
    assert data["is_fraud_warning"] is True
    assert data["shield_color"] == "red"

def test_06_absence_of_facades_and_hardcoded_results():
    """Forensic scan: ensure no facade stubs or hardcoded bypasses exist."""
    with open("app/static/app.js", "r", encoding="utf-8") as f:
        js = f.read()

    # apiFetch must not be an empty dummy
    assert "fetch(url, opts)" in js

    # computeSha256 must not return a fixed constant
    assert "window.crypto.subtle.digest" in js

    # performArshinCheck must not hardcode 991201 bypass
    assert "apiFetch(`/api/arshin/check?serial=" in js
