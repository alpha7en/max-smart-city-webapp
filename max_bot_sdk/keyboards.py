"""
Keyboard builders and button utilities for MAX Bot API.
Ensures:
- Truncation of long button text (max 18 chars to prevent clipping on mobile screens)
- Automatic sanitization of URLs for 'link' buttons (rejects localhost, requires https)
- Correct serialization for MAX Platform API
"""

from typing import List, Dict, Any, Optional

MAX_BUTTON_TEXT_LEN = 18

def _sanitize_text(text: str) -> str:
    clean = text.strip()
    if len(clean) > MAX_BUTTON_TEXT_LEN:
        return clean[:MAX_BUTTON_TEXT_LEN - 1].strip() + "…"
    return clean

class Button:
    @staticmethod
    def callback(text: str, payload: str) -> Dict[str, Any]:
        """Создает inline callback-кнопку"""
        return {
            "type": "callback",
            "text": _sanitize_text(text),
            "payload": payload
        }

    @staticmethod
    def link(text: str, url: str) -> Dict[str, Any]:
        """
        Создает inline кнопку-ссылку.
        Гарантирует валидный HTTPS URL (платформа MAX отклоняет localhost и не-http схемы).
        """
        clean_url = url.strip()
        if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
            clean_url = "https://max.ru"
        elif "localhost" in clean_url or "127.0.0.1" in clean_url:
            clean_url = "https://max.ru"

        return {
            "type": "link",
            "text": _sanitize_text(text),
            "url": clean_url
        }

    @staticmethod
    def open_app(text: str, web_app: Optional[str] = None, payload: Optional[str] = None) -> Dict[str, Any]:
        """
        Создает inline кнопку открытия мини-приложения (Mini App) строго внутри MAX WebView.
        В соответствии с официальной спецификацией MAX Bot API (https://dev.max.ru/docs-api, https://dev.max.ru/docs/webapps/introduction):
        - type: 'open_app'
        - web_app: имя бота в MAX (например, 't226_hakaton_max_bot')
        - payload: опциональный параметр запуска (до 512 символов, передается в initDataUnsafe.start_param)
        Ни в коем случае не конвертируется в 'link', так как 'link' открывает браузер в отдельной вкладке!
        """
        clean_bot = "t226_hakaton_max_bot"
        clean_payload = payload

        if web_app:
            target = str(web_app).strip()
            if "?" in target:
                base_part, query_part = target.split("?", 1)
                if not clean_payload:
                    clean_payload = query_part[:512]
                target = base_part
            if "max.ru/" in target:
                target = target.split("max.ru/")[-1].split("/")[0].split("?")[0]
            elif target.startswith("@"):
                target = target.lstrip("@")
            elif not target.startswith("http://") and not target.startswith("https://"):
                clean_bot = target

        btn: Dict[str, Any] = {
            "type": "open_app",
            "text": _sanitize_text(text),
            "web_app": clean_bot
        }
        if clean_payload:
            btn["payload"] = str(clean_payload)[:512]
        return btn

    @staticmethod
    def request_contact(text: str = "Поделиться") -> Dict[str, Any]:
        """
        Создает кнопку запроса контакта (номера телефона) пользователя в MAX Bot API.
        При нажатии MAX отправляет вложение с типом 'contact', vcf_info и hash.
        """
        return {
            "type": "request_contact",
            "text": _sanitize_text(text)
        }



class KeyboardBuilder:
    @staticmethod
    def inline(rows: List[List[Dict[str, Any]]]) -> Dict[str, Any]:
        """
        Упаковывает строки кнопок в объект вложения inline_keyboard для MAX API.
        Автоматически проверяет безопасность ссылочных кнопок и Mini App кнопок.
        """
        sanitized_rows = []
        for row in rows:
            sanitized_row = []
            for btn in row:
                btn_copy = dict(btn)
                if btn_copy.get("type") == "open_app":
                    target = str(btn_copy.get("web_app") or btn_copy.get("url") or "").strip()
                    payload = btn_copy.get("payload")
                    if "?" in target:
                        base_part, query_part = target.split("?", 1)
                        if not payload:
                            payload = query_part[:512]
                        target = base_part
                    if "max.ru/" in target:
                        target = target.split("max.ru/")[-1].split("/")[0].split("?")[0]
                    clean_bot = target.lstrip("@")
                    if clean_bot.startswith("http://") or clean_bot.startswith("https://") or not clean_bot:
                        clean_bot = "t226_hakaton_max_bot"
                    btn_copy["type"] = "open_app"
                    btn_copy["web_app"] = clean_bot
                    btn_copy.pop("url", None)
                    if payload:
                        btn_copy["payload"] = str(payload)[:512]
                elif btn_copy.get("type") == "link":
                    btn_url = str(btn_copy.get("url", "")).strip()
                    if not (btn_url.startswith("http://") or btn_url.startswith("https://")):
                        btn_url = "https://max.ru"
                    elif "localhost" in btn_url or "127.0.0.1" in btn_url:
                        btn_url = "https://max.ru"
                sanitized_row.append(btn_copy)
            sanitized_rows.append(sanitized_row)

        return {
            "type": "inline_keyboard",
            "payload": {
                "buttons": sanitized_rows
            }
        }
