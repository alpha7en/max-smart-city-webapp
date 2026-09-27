"""Сценарии бота. Импорт пакета регистрирует обработчики (декораторы из app.bot.router)."""
from app.bot.flows import menu, meters, notify, profile, registration, sharing, submission  # noqa: F401
from app.bot.flows import hackathon_demo  # noqa: F401 — ТОЛЬКО ДЛЯ ХАКАТОНА, см. docstring модуля
