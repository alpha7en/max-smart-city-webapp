"""
Background Long Polling worker daemon for MAX Messenger.
Provides resilient, non-blocking polling, exponential backoff, and graceful shutdown.
Zero emoji policy strictly enforced.
"""

import asyncio
import logging
from typing import Optional

from app.config import settings
from app.bot.client import MaxBotClient
from app.bot.handlers import get_bot_handler

logger = logging.getLogger("max_bot_worker")


class BotWorker:
    """
    Manages the long polling lifecycle for the MAX Bot.
    Uses non-blocking task feeds to prevent blocking the listener on slow I/O.
    """

    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
        self.token = settings.BOT_TOKEN if token is None else token
        self.base_url = base_url or settings.MAX_API_BASE
        self.client: Optional[MaxBotClient] = None
        self._is_running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._consecutive_errors: int = 0

    def is_configured(self) -> bool:
        return bool(self.token)

    async def start(self) -> Optional[asyncio.Task]:
        """Verify credentials and start background polling task."""
        if not self.is_configured():
            logger.warning("BOT_TOKEN is not configured. Bot worker will not start.")
            return None

        self.client = MaxBotClient(self.token, self.base_url)
        try:
            me_info = await asyncio.to_thread(self.client.get_me)
            logger.info(
                "Connected to MAX Bot API! Bot: %s (@%s), ID: %s",
                me_info.get("first_name") or me_info.get("name"),
                me_info.get("username"),
                me_info.get("id") or me_info.get("user_id")
            )
        except Exception as e:
            logger.error("Failed to verify bot credentials with MAX platform: %s", e)
            return None

        self._is_running = True
        self._task = asyncio.create_task(self._poll_loop())
        return self._task

    async def stop(self) -> None:
        """Stop polling and drain active update tasks."""
        self._is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Drain dispatcher active tasks
        try:
            handler = get_bot_handler()
            if hasattr(handler, "dispatcher") and hasattr(handler.dispatcher, "wait_closed"):
                await handler.dispatcher.wait_closed(timeout=5.0)
        except Exception as drain_err:
            logger.warning("Error draining dispatcher tasks during stop: %s", drain_err)

        logger.info("Bot worker stopped.")

    async def _poll_loop(self) -> None:
        logger.info("Starting MAX Bot Long Polling listener loop...")
        marker = None
        handler = get_bot_handler()

        while self._is_running:
            try:
                # Non-blocking long polling request via asyncio.to_thread
                data = await asyncio.to_thread(self.client.get_updates, marker=marker, timeout=25)
                marker = data.get("marker", marker)
                updates = data.get("updates", [])

                for upd in updates:
                    upd_type = upd.get("update_type")
                    logger.info("Received MAX update: %s", upd_type)
                    # Non-blocking update handling wrapped in task
                    if hasattr(handler, "feed_update"):
                        handler.feed_update(upd)
                    elif hasattr(handler, "dispatcher") and hasattr(handler.dispatcher, "feed_update"):
                        handler.dispatcher.feed_update(upd)
                    else:
                        asyncio.create_task(self._dispatch_update(handler, upd))

                self._consecutive_errors = 0

            except asyncio.CancelledError:
                logger.info("Bot polling loop cancelled.")
                break
            except Exception as e:
                err_str = str(e).lower()
                if "timed out" in err_str:
                    continue

                self._consecutive_errors += 1
                backoff = min(2 ** self._consecutive_errors, 30)
                logger.warning("Error in bot polling loop: %s (backing off for %ds)", e, backoff)
                await asyncio.sleep(backoff)

    async def _dispatch_update(self, handler, update: dict) -> None:
        try:
            if hasattr(handler, "process_update_async"):
                await handler.process_update_async(update)
            else:
                await asyncio.to_thread(handler.process_update, update)
        except Exception as e:
            logger.error("Error processing update in worker task: %s", e, exc_info=True)


bot_worker = BotWorker()
