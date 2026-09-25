"""Сценарии бота. Импорт пакета регистрирует обработчики (декораторы из app.bot.router)."""
from app.bot.flows import invite, menu, notify, profile, registration, submission  # noqa: F401
