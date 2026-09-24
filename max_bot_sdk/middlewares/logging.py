"""
Logging Middleware for MAX Bot SDK.
Zero emoji policy strictly enforced.
"""

import time
import logging
from typing import Dict, Any, Optional

from max_bot_sdk.middlewares.base import BaseMiddleware

logger = logging.getLogger("max_bot_sdk.middleware.logging")


class LoggingMiddleware(BaseMiddleware):
    """
    Middleware that records structured timing and routing metrics for incoming updates.
    """

    async def pre_process(self, update: Any, data: Dict[str, Any]) -> bool:
        data["_start_time"] = time.time()
        upd_type = getattr(update, "update_type", "unknown")
        chat_id = getattr(update, "effective_chat_id", None)
        user_id = getattr(update, "sender_user_id", None)
        logger.info(
            "Incoming update: type=%s, chat_id=%s, user_id=%s",
            upd_type,
            chat_id,
            user_id
        )
        return True

    async def post_process(
        self,
        update: Any,
        data: Dict[str, Any],
        result: Any = None,
        exception: Optional[Exception] = None
    ) -> None:
        start_time = data.get("_start_time")
        duration_ms = ((time.time() - start_time) * 1000.0) if start_time else 0.0
        upd_type = getattr(update, "update_type", "unknown")
        if exception:
            logger.error(
                "Update failed: type=%s, duration=%.2fms, error=%s",
                upd_type,
                duration_ms,
                exception
            )
        else:
            logger.info(
                "Update processed: type=%s, duration=%.2fms",
                upd_type,
                duration_ms
            )
