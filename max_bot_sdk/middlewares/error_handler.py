"""
Error Handling Middleware for MAX Bot SDK.
Zero emoji policy strictly enforced.
"""

import logging
from typing import Dict, Any, Optional

from max_bot_sdk.middlewares.base import BaseMiddleware

logger = logging.getLogger("max_bot_sdk.middleware.error_handler")


class ErrorHandlingMiddleware(BaseMiddleware):
    """
    Middleware that safely intercepts and logs exceptions arising during handler execution.
    """

    def __init__(self, fallback_message: Optional[str] = None):
        self.fallback_message = fallback_message

    async def pre_process(self, update: Any, data: Dict[str, Any]) -> bool:
        return True

    async def post_process(
        self,
        update: Any,
        data: Dict[str, Any],
        result: Any = None,
        exception: Optional[Exception] = None
    ) -> None:
        if exception is not None:
            logger.error(
                "ErrorHandlingMiddleware intercepted exception: %s",
                exception,
                exc_info=True
            )
            client = data.get("client")
            if client and self.fallback_message:
                chat_id = getattr(update, "effective_chat_id", None)
                user_id = getattr(update, "sender_user_id", None)
                try:
                    if hasattr(client, "send_message"):
                        client.send_message(
                            chat_id=chat_id,
                            user_id=user_id,
                            text=self.fallback_message
                        )
                except Exception as send_err:
                    logger.error("Failed to deliver fallback error message: %s", send_err)
