import time
import logging
from typing import Callable, Any

from mcp_1c_odata.logs.storage import log_request
from mcp_1c_odata.auth.db import verify_token

logger = logging.getLogger("mcp-1c-odata.logs")


def _extract_token_info(auth_token: str | None) -> tuple[str | None, str | None]:
    """Извлекает имя токена и роль из сырого токена. Сам токен НЕ логируем."""
    if not auth_token:
        return None, None
    try:
        info = verify_token(auth_token)
        if not info:
            logger.warning("Токен невалиден или просрочен")
            return None, None
        return info.get("name"), info.get("role")
    except Exception as e:
        logger.debug(f"Не удалось извлечь info токена: {e}")
        return None, None


def _summarize_result(result: dict, tool: str) -> str | None:
    """Компактное описание результата для лога (без самих данных)."""
    if not isinstance(result, dict):
        return None

    if tool == "list_entities":
        count = len(result.get("entities", []))
        return f"entities={count}"

    if tool == "get_metadata":
        count = len(result.get("fields", []))
        return f"fields={count}"

    if tool == "fetch_and_process":
        if "error" in result:
            return None
        total = result.get("total_count", 0)
        returned = len(result.get("data", []))
        truncated = result.get("truncated", False)
        return f"total={total}, returned={returned}, truncated={truncated}"

    if tool == "lookup":
        if "error" in result:
            return None
        found = result.get("total_found", 0)
        returned = result.get("returned", 0)
        return f"found={found}, returned={returned}"

    if tool == "refresh_cache":
        if "error" in result:
            return None
        mode = result.get("mode", "?")
        updated = result.get("updated", 0)
        total = result.get("total", 0)
        return f"mode={mode}, updated={updated}, total={total}"

    return None


def _extract_entity(arguments: dict) -> str | None:
    return arguments.get("entity") or arguments.get("catalog") or arguments.get("document")


async def execute_with_logging(
    tool_name: str,
    execute_fn: Callable,
    arguments: dict,
) -> dict:
    """Обёртка над инструментом: замеряет время и логирует вызов.

    Args:
        tool_name: имя инструмента (list_entities, fetch_and_process, ...)
        execute_fn: асинхронная функция execute() инструмента
        arguments: исходные аргументы от LLM
    """
    logger.info(f"[LOG] Начинаем логирование вызова {tool_name}")

    # 1. Извлекаем контекст (не трогаем сам токен)
    auth_token = arguments.get("auth_token")
    token_name, role = _extract_token_info(auth_token)
    entity = _extract_entity(arguments)

    # 2. Логируем аргументы БЕЗ auth_token
    safe_args = {k: v for k, v in arguments.items() if k != "auth_token"}

    # 3. Выполняем с замером времени
    start = time.perf_counter()
    status = "ok"
    error_text = None
    result: dict = {}

    try:
        result = await execute_fn(arguments)
        if isinstance(result, dict) and "error" in result:
            status = "error"
            error_text = str(result["error"])
    except Exception as e:
        status = "error"
        error_text = f"{type(e).__name__}: {e}"
        logger.exception(
            f"Необработанное исключение в инструменте {tool_name}")
        result = {"error": error_text}
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)

    # 4. Пишем в лог
    try:
        logger.info(
            f"[LOG] Записываем лог: tool={tool_name}, status={status}, duration={duration_ms}ms")
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
        logger.info(f"[LOG] ✓ Лог записан успешно")
    except Exception as e:
        # Логирование не должно ломать основной поток
        logger.error(
            f"[LOG] ✗ Не удалось записать лог запроса: {e}", exc_info=True)

    return result
