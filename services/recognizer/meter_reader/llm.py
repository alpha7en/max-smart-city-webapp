import httpx

from .config import Settings


class LLMError(RuntimeError):
    pass


class YandexQwenClient:
    """Client for multimodal Qwen in Yandex Cloud AI Studio (OpenAI-compatible API)."""

    def __init__(self, settings: Settings):
        if not settings.api_key or not settings.folder_id:
            raise LLMError("YC_API_KEY and YC_FOLDER_ID must be set")
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            headers={"Authorization": f"Api-Key {settings.api_key}"},
            timeout=settings.timeout,
        )

    async def ask_with_image(
        self, system: str, prompt: str, image_url: str, temperature: float = 0.0
    ) -> str:
        payload = {
            "model": self.settings.model_uri,
            "temperature": temperature,
            "max_tokens": 8000 if self.settings.enable_thinking else 1500,
            "chat_template_kwargs": {"enable_thinking": self.settings.enable_thinking},
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_url}},
                        {"type": "text", "text": prompt},
                    ],
                },
            ],
        }
        try:
            response = await self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(f"request to Yandex Cloud failed: {exc}") from exc
        if response.status_code != 200:
            raise LLMError(f"Yandex Cloud returned {response.status_code}: {response.text[:500]}")

        content = response.json()["choices"][0]["message"].get("content")
        if not content:
            raise LLMError("model returned empty content")
        return content

    async def aclose(self) -> None:
        await self._client.aclose()
