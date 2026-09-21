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
        keyboard: Optional[Dict[str, Any]] = None,
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

        def _sanitize_btn(btn: Dict[str, Any]) -> Dict[str, Any]:
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
                    btn_copy["url"] = "https://max.ru"
                elif "localhost" in btn_url or "127.0.0.1" in btn_url:
                    btn_copy["url"] = "https://max.ru"
            return btn_copy

        if keyboard:
            # Deep sanitize buttons inside keyboard payload
            kb_copy = dict(keyboard)
            if "payload" in kb_copy and isinstance(kb_copy["payload"], dict) and "buttons" in kb_copy["payload"]:
                kb_copy["payload"] = dict(kb_copy["payload"])
                sanitized_rows = []
                for row in kb_copy["payload"]["buttons"]:
                    sanitized_rows.append([_sanitize_btn(b) for b in row])
                kb_copy["payload"]["buttons"] = sanitized_rows
            payload_data["attachments"] = [kb_copy]
        elif buttons:
            sanitized_buttons = []
            for row in buttons:
                sanitized_buttons.append([_sanitize_btn(b) for b in row])

            payload_data["attachments"] = [
                {
                    "type": "inline_keyboard",
                    "payload": {
                        "buttons": sanitized_buttons
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
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                pass
            logger.error("HTTP Error %s when sending message to %s: %s", e.code, params, err_body)
            if "attachments" in payload_data:
                logger.warning("Retrying message send without attachments to ensure user receives response...")
                retry_payload = {"text": text, "format": format_type}
                retry_req = urllib.request.Request(
                    url,
                    data=json.dumps(retry_payload).encode("utf-8"),
                    headers={
                        "Authorization": self.token,
                        "Content-Type": "application/json",
                        "User-Agent": "MAX-SmartCity-Bot/1.0"
                    },
                    method="POST"
                )
                try:
                    with urllib.request.urlopen(retry_req, context=self.ctx, timeout=10) as resp:
                        return json.loads(resp.read().decode("utf-8"))
                except Exception as retry_err:
                    logger.error("Failed retry without attachments: %s", retry_err)
            raise

    def answer_callback(self, callback_id: str, notification: Optional[str] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/answers?callback_id={urllib.parse.quote(str(callback_id))}"
        payload = {"notification": notification if notification else "Принято"}

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

    def set_webhook(self, url: str, update_types: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Registers a webhook URL with MAX Platform via POST /subscriptions.
        """
        if not url:
            raise ValueError("URL cannot be empty")
        endpoint_url = f"{self.base_url}/subscriptions"
        payload_data: Dict[str, Any] = {"url": url}
        if update_types:
            payload_data["update_types"] = update_types

        data = json.dumps(payload_data).encode("utf-8")
        req = urllib.request.Request(
            endpoint_url,
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

    def get_subscriptions(self) -> Dict[str, Any]:
        """
        Fetches active subscriptions from MAX Platform via GET /subscriptions.
        """
        endpoint_url = f"{self.base_url}/subscriptions"
        req = urllib.request.Request(
            endpoint_url,
            headers={"Authorization": self.token, "User-Agent": "MAX-SmartCity-Bot/1.0"}
        )
        with urllib.request.urlopen(req, context=self.ctx, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def delete_webhook(self, url: Optional[str] = None) -> Dict[str, Any]:
        """
        Deletes a webhook subscription via DELETE /subscriptions?url=...
        If url is None, deletes all currently active subscriptions.
        """
        if url:
            quoted = urllib.parse.quote(url, safe="")
            endpoint_url = f"{self.base_url}/subscriptions?url={quoted}"
            req = urllib.request.Request(
                endpoint_url,
                headers={"Authorization": self.token, "User-Agent": "MAX-SmartCity-Bot/1.0"},
                method="DELETE"
            )
            with urllib.request.urlopen(req, context=self.ctx, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        else:
            subs = self.get_subscriptions().get("subscriptions", [])
            for sub in subs:
                sub_url = sub.get("url")
                if sub_url:
                    self.delete_webhook(sub_url)
            return {"success": True, "deleted_count": len(subs)}

