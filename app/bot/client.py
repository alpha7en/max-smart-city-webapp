"""
MAX Platform API Client for Python.
Supports:
- HTTPS requests to platform-api2.max.ru
- Authorization header
- Sending messages with inline keyboards, links, callbacks
- Answer callback queries
- Long polling loop
"""

import ssl
import json
import urllib.request
import urllib.parse
from typing import Optional, Dict, Any, List
import logging

logger = logging.getLogger("max_bot_client")

class MaxBotClient:
    def __init__(self, token: str, base_url: str = "https://platform-api2.max.ru"):
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.ctx = ssl._create_unverified_context()

    def get_me(self) -> Dict[str, Any]:
        req = urllib.request.Request(
            f"{self.base_url}/me",
            headers={"Authorization": self.token, "User-Agent": "MAX-SmartCity-Bot/1.0"}
        )
        with urllib.request.urlopen(req, context=self.ctx, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def send_message(
        self,
        chat_id: Optional[str] = None,
        user_id: Optional[int] = None,
        text: str = "",
        buttons: Optional[List[List[Dict[str, Any]]]] = None,
        format_type: str = "markdown"
    ) -> Dict[str, Any]:
        params = {}
        if chat_id:
            params["chat_id"] = chat_id
        elif user_id:
            params["user_id"] = user_id
        else:
            raise ValueError("Нужно указать chat_id или user_id")

        url = f"{self.base_url}/messages?{urllib.parse.urlencode(params)}"
        payload_data: Dict[str, Any] = {
            "text": text,
            "format": format_type
        }

        if buttons:
            payload_data["attachments"] = [
                {
                    "type": "inline_keyboard",
                    "payload": {
                        "buttons": buttons
                    }
                }
            ]

        data = json.dumps(payload_data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": self.token,
                "Content-Type": "application/json",
                "User-Agent": "MAX-SmartCity-Bot/1.0"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, context=self.ctx, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def answer_callback(self, callback_id: str, notification: Optional[str] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/answers"
        payload = {"callback_id": callback_id}
        if notification:
            payload["notification"] = notification

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": self.token,
                "Content-Type": "application/json",
                "User-Agent": "MAX-SmartCity-Bot/1.0"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.warning("Could not answer callback %s: %s", callback_id, e)
            return {}

    def get_updates(self, marker: Optional[int] = None, timeout: int = 30) -> Dict[str, Any]:
        url = f"{self.base_url}/updates"
        params = {}
        if marker:
            params["marker"] = marker
        if params:
            url += f"?{urllib.parse.urlencode(params)}"

        req = urllib.request.Request(
            url,
            headers={"Authorization": self.token, "User-Agent": "MAX-SmartCity-Bot/1.0"}
        )
        with urllib.request.urlopen(req, context=self.ctx, timeout=timeout + 5) as resp:
            return json.loads(resp.read().decode("utf-8"))
