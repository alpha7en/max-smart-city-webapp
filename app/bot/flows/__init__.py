"""Сценарии бота. Импорт пакета регистрирует обработчики (декораторы из app.bot.router).

hackathon_demo — ТОЛЬКО ДЛЯ ХАКАТОНА (/demo_profile), см. docstring модуля.
"""
from app.bot.flows import hackathon_demo, menu, meters, notify, profile, registration, sharing, submission  # noqa: F401
