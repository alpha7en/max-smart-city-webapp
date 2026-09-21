"""
Configuration module for MAX Smart City housing ecosystem.
Loads BOT_TOKEN from token.env or .env safely without logging secrets.
"""

import os
from pathlib import Path
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent.parent

def load_token_from_files() -> str:
    # First check process environment
    token = os.environ.get("BOT_TOKEN", "").strip()
    if token:
        return token
    
    for filename in ["token.env", ".env"]:
        filepath = BASE_DIR / filename
        if filepath.is_file():
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip() in ["BOT_TOKEN", "TOKEN", "MAX_BOT_TOKEN"]:
                            return v.strip().strip("\"'")
                    elif len(line) > 10 and not line.startswith("{"):
                        return line.strip().strip("\"'")
    return ""

class Settings:
    PROJECT_NAME: str = "MAX Smart City - Умный город ЖКХ"
    VERSION: str = "1.0.0"
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8080"))
    BOT_TOKEN: str = load_token_from_files()
    MAX_API_BASE: str = os.getenv("MAX_API_BASE", "https://platform-api2.max.ru")
    BOT_USERNAME: str = "t226_hakaton_max_bot"
    BOT_ID: str = os.getenv("BOT_ID", "423938205")
    MINIAPP_URL: str = os.getenv("MINIAPP_URL", "https://alpha7en.github.io/max-smart-city-webapp/")

    # Security & Webhook Settings
    DEV_MODE: bool = os.getenv("DEV_MODE", "true").lower() in ("true", "1", "yes")
    USE_WEBHOOK: bool = os.getenv("USE_WEBHOOK", "false").lower() in ("true", "1", "yes")
    WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "")
    WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "")
    VALIDATE_INIT_DATA: bool = os.getenv("VALIDATE_INIT_DATA", "false").lower() in ("true", "1", "yes")

settings = Settings()
