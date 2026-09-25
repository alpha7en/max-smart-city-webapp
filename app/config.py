"""Настройки приложения из переменных окружения."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


def _choice(value: str | None, options: tuple[str, ...], default: str) -> str:
    v = (value or "").strip().lower()
    return v if v in options else default


@dataclass(frozen=True)
class Settings:
    bot_token: str = ""
    bot_username: str = ""              # пусто → берём из GET /me
    max_api_base: str = "https://platform-api2.max.ru"
    dadata_api_key: str = ""
    recognizer_url: str = ""            # пусто → демо-распознавание (StubRecognizer)
    demo_mode: bool = True
    dev_auth: bool = False              # только для локальной разработки мини-приложения
    init_data_ttl: int = 24 * 3600      # секунды
    data_dir: Path = Path("data")
    tz: str = "Europe/Moscow"
    submit_day_from: int = 15
    submit_day_to: int = 25
    miniapp_origins: tuple[str, ...] = ()  # CORS: мини-приложение на другом домене (GitHub Pages)
    arshin_mode: str = "live"           # ФГИС «Аршин»: live | fixtures (демо-данные) | off
    arshin_base: str = "https://fgis.gost.ru/fundmetrology/eapi"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bot.db"

    @property
    def photos_dir(self) -> Path:
        return self.data_dir / "photos"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    e = os.environ if env is None else env
    return Settings(
        bot_token=e.get("BOT_TOKEN", "").strip(),
        bot_username=e.get("BOT_USERNAME", "").strip().lstrip("@"),
        max_api_base=e.get("MAX_API_BASE", "").strip().rstrip("/") or Settings.max_api_base,
        dadata_api_key=e.get("DADATA_API_KEY", "").strip(),
        recognizer_url=e.get("RECOGNIZER_URL", "").strip(),
        demo_mode=_bool(e.get("DEMO_MODE"), True),
        dev_auth=_bool(e.get("DEV_AUTH"), False),
        init_data_ttl=_int(e.get("INIT_DATA_TTL"), Settings.init_data_ttl),
        data_dir=Path(e.get("DATA_DIR", "").strip() or "data"),
        tz=e.get("TZ", "").strip() or Settings.tz,
        submit_day_from=_int(e.get("SUBMIT_DAY_FROM"), 15),
        submit_day_to=_int(e.get("SUBMIT_DAY_TO"), 25),
        arshin_mode=_choice(e.get("ARSHIN_MODE"), ("live", "fixtures", "off"), "live"),
        arshin_base=e.get("ARSHIN_BASE", "").strip().rstrip("/") or Settings.arshin_base,
        miniapp_origins=tuple(o.strip().rstrip("/") for o in e.get("MINIAPP_ORIGINS", "").split(",") if o.strip()),
    )
