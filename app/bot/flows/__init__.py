"""Сценарии бота. Импорт пакета регистрирует обработчики (декораторы из app.bot.router)."""
from app.bot.flows import invite, menu, meters, notify, profile, registration, submission  # noqa: F401
from app.bot.flows import hackathon_demo  # noqa: F401 — ТОЛЬКО ДЛЯ ХАКАТОНА, см. docstring модуля
