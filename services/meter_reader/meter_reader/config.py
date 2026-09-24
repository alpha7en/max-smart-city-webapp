import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    api_key: str = os.getenv("YC_API_KEY", "")
    folder_id: str = os.getenv("YC_FOLDER_ID", "")
    model: str = os.getenv("YC_MODEL", "qwen3.6-35b-a3b")
    base_url: str = os.getenv("YC_BASE_URL", "https://llm.api.cloud.yandex.net/v1")
    enable_thinking: bool = _bool("YC_ENABLE_THINKING", False)
    timeout: float = float(os.getenv("YC_TIMEOUT", "120"))
    max_image_side: int = int(os.getenv("MAX_IMAGE_SIDE", "1600"))

    @property
    def model_uri(self) -> str:
        return f"gpt://{self.folder_id}/{self.model}/latest"


settings = Settings()
