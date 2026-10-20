import functools
import logging
from typing import Callable

from mcp_1c_odata.rate_limit.limiter import rate_limiter

logger = logging.getLogger("mcp-1c-odata.rate_limit")


def rate_limited(tool_name: str):
    """Декоратор для execute() инструментов — проверяет rate limit ДО выполнения.

    Если лимит превышен — возвращает {"error": ...} без вызова инструмента.
    Должен применяться ПЕРВЫМ (внешним) декоратором, перед @logged.

    Usage:
        @rate_limited("fetch_and_process")
        @logged("fetch_and_process")
        async def execute(arguments: dict) -> dict:
            ...
    """
    def decorator(fn: Callable):
        @functools.wraps(fn)
        async def wrapper(arguments: dict):
            auth_token = arguments.get("auth_token")

            # Проверяем лимит
            result = rate_limiter.check(auth_token)

            if not result.allowed:
                # Логируем отказ (но не через @logged, чтобы не засорять основную статистику)
                logger.warning(
                    f"[RATE_LIMIT] Отказ: tool={tool_name}, "
                    f"token={result.token_name}, role={result.role}, "
                    f"message={result.message}"
                )
                return {
                    "error": result.message or "Rate limit exceeded",
                    "rate_limit": {
                        "limit": result.limit,
                        "window_sec": rate_limiter.window_sec,
                        "retry_after_sec": result.remaining_sec,
                    }
                }

            # Лимит пройден — выполняем инструмент
            return await fn(arguments)
        return wrapper
    return decorator
