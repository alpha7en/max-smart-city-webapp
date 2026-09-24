"""
Base Middleware Interface for MAX Bot SDK.
Zero emoji policy strictly enforced.
"""

from typing import Dict, Any, Optional
import inspect


class BaseMiddleware:
    """
    Base class for dispatcher middleware.
    Middleware can inspect or modify context data before and after handler execution.
    """

    async def pre_process(self, update: Any, data: Dict[str, Any]) -> bool:
        """
        Executed before handler execution.
        Return True to continue pipeline, False to abort handler execution.
        """
        return True

    async def post_process(
        self,
        update: Any,
        data: Dict[str, Any],
        result: Any = None,
        exception: Optional[Exception] = None
    ) -> None:
        """
        Executed after handler execution (success or error).
        """
        pass
