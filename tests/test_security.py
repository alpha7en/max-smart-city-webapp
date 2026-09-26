"""Гигиена секретов: в git нет ключей и .env, CA Минцифры — ровно тот, что ожидаем, TLS не отключён."""
from __future__ import annotations

import hashlib
import re
import shutil
import ssl
import subprocess
from pathlib import Path

import pytest

from app.integrations import max_api

ROOT = Path(__file__).resolve().parents[1]

# certs/README.md: отпечатки SHA-256 с https://www.gosuslugi.ru/crt
CA_FINGERPRINTS = {
    "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31",  # Russian Trusted Root CA, до 2032
    "bbbde2103e790b999ec62bd03cf625a5a2e7c316e10afe6a490eedead8b3fd9b",  # Russian Trusted Sub CA, до 03.2027
}

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAQVN[A-Za-z0-9_-]{30,}"),          # API-ключ Yandex Cloud
    re.compile(r"\by0_[A-Za-z0-9_-]{30,}"),           # OAuth-токен Яндекса
    re.compile(r"\bt1\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),  # IAM-токен Yandex Cloud
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{30,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # непустое значение секрета в примерах окружения / compose
    re.compile(r"^[ \t]*-?[ \t]*(BOT_TOKEN|YC_API_KEY|DADATA_API_KEY)[ \t]*[=:][ \t]*[^\s$#{][^\s#]{7,}", re.M),
]


def tracked_files() -> list[Path]:
    if not shutil.which("git") or not (ROOT / ".git").exists():
        pytest.skip("нет git-репозитория")
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [ROOT / p for p in out.decode().split("\0") if p]


def test_russian_ca_is_pinned():
    pem = max_api.RUSSIAN_CA.read_text()
    certs = re.findall(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", pem, re.S)
    got = {hashlib.sha256(ssl.PEM_cert_to_DER_cert(c)).hexdigest() for c in certs}
    assert got == CA_FINGERPRINTS
    assert "PRIVATE KEY" not in pem


def test_ssl_context_verifies_and_needs_ca(monkeypatch, tmp_path):
    ctx = max_api.ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    monkeypatch.setattr(max_api, "RUSSIAN_CA", tmp_path / "missing.pem")
    with pytest.raises(FileNotFoundError):
        max_api.ssl_context()


def test_no_secrets_in_git():
    leaks = []
    for f in tracked_files():
        name = f.name.lower()
        if name == ".env" or (name.endswith(".env") and not name.endswith(".example")):
            leaks.append(f"{f.relative_to(ROOT)}: файл окружения")
        if f.suffix.lower() in {".key", ".p12", ".pfx", ".jks"}:
            leaks.append(f"{f.relative_to(ROOT)}: файл ключа")
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        leaks += [f"{f.relative_to(ROOT)}: {p.pattern[:30]}" for p in SECRET_PATTERNS if p.search(text)]
    assert not leaks, leaks


def test_tls_never_disabled():
    bad = re.compile(r"verify\s*=\s*False|CERT_NONE|check_hostname\s*=\s*False|_create_unverified_context")
    hits = [str(f.relative_to(ROOT)) for f in tracked_files()
            if f.suffix == ".py" and f.name != "test_security.py" and bad.search(f.read_text(encoding="utf-8"))]
    assert not hits, hits
