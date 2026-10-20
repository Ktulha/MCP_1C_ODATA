import time
import logging
import functools
from typing import Callable

from mcp_1c_odata.logs.storage import log_request
from mcp_1c_odata.auth.db import verify_token

logger = logging.getLogger("mcp-1c-odata.logs")


def _extract_token_info(auth_token: str | None) -> tuple[str | None, str | None]:
    if not auth_token:
        return None, None
    try:
        info = verify_token(auth_token)
        if not info:
            return None, None
        return info.get("name"), info.get("role")
    except Exception as e:
        logger.debug(f"Не удалось извлечь info токена: {e}")
        return None, None


def _summarize_result(result: dict, tool: str) -> str | None:
    if not isinstance(result, dict):
        return None

    if tool == "list_entities":
        return f"entities={len(result.get('entities', []))}"

    if tool == "get_metadata":
        return f"fields={len(result.get('fields', []))}"

    if tool == "fetch_and_process":
        if "error" in result:
            return None
        return f"total={result.get('total_count', 0)}, returned={len(result.get('data', []))}, truncated={result.get('truncated', False)}"

    if tool == "lookup":
        if "error" in result:
            return None
        return f"found={result.get('total_found', 0)}, returned={result.get('returned', 0)}"

    if tool == "refresh_cache":
        if "error" in result:
            return None
        return f"mode={result.get('mode', '?')}, updated={result.get('updated', 0)}, total={result.get('total', 0)}"

    return None


def logged(tool_name: str):
    """Декоратор для execute() инструментов — автоматически логирует вызовы.

    Usage:
        @logged("fetch_and_process")
        async def execute(arguments: dict) -> dict:
            ...
    """
    def decorator(fn: Callable):
        @functools.wraps(fn)
        async def wrapper(arguments: dict):
            # 1. Извлекаем контекст
            auth_token = arguments.get("auth_token")
            token_name, role = _extract_token_info(auth_token)
            entity = arguments.get("entity")
            safe_args = {k: v for k, v in arguments.items() if k !=
                         "auth_token"}

            # 2. Выполняем с замером времени
            start = time.perf_counter()
            status = "ok"
            error_text = None
            result = {}

            try:
                result = await fn(arguments)
                if isinstance(result, dict) and "error" in result:
                    status = "error"
                    error_text = str(result["error"])
            except Exception as e:
                status = "error"
                error_text = f"{type(e).__name__}: {e}"
                logger.exception(f"Необработанное исключение в {tool_name}")
                result = {"error": error_text}
            finally:
                duration_ms = int((time.perf_counter() - start) * 1000)

            # 3. Пишем в лог
            try:
                log_request(
                    token_name=token_name,
                    role=role,
                    tool=tool_name,
                    entity=entity,
                    arguments=safe_args,
                    status=status,
                    result_summary=_summarize_result(result, tool_name),
                    duration_ms=duration_ms,
                    error=error_text,
                )
            except Exception as e:
                logger.warning(f"Не удалось записать лог: {e}")

            return result
        return wrapper
    return decorator
