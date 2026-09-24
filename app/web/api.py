"""API мини-приложения (/api/*) — ЗАГЛУШКА S0. Поток S5b заменит файл (контракт — SPEC §6).
/api/health объявлен в app/main.py."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api")
