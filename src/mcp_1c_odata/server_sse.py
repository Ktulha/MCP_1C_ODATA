import logging
import contextvars
from contextlib import asynccontextmanager

import uvicorn
from mcp.server.mcpserver import MCPServer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from mcp_1c_odata.tools import list_entities, get_metadata, fetch_and_process, lookup, refresh_cache
from mcp_1c_odata.cache.scheduler import cache_scheduler
from mcp_1c_odata.logs.storage import init_logs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("mcp-1c-odata")

# === КОНТЕКСТНАЯ ПЕРЕМЕННАЯ ДЛЯ ХРАНЕНИЯ ТОКЕНА ЗАПРОСА ===
# Она безопасна для асинхронных запросов и изолирует токены разных клиентов
current_auth_token = contextvars.ContextVar("current_auth_token", default=None)


@asynccontextmanager
async def lifespan(server: MCPServer):
    init_logs()
    cache_scheduler.start()
    logger.info("MCP SSE сервер запущен на http://0.0.0.0:8001/sse")
    yield
    cache_scheduler.shutdown()
    logger.info("MCP SSE сервер остановлен")


# 1. Создаем экземпляр MCPServer
server = MCPServer(
    name="mcp-1c-odata",
    version="0.1.0",
    lifespan=lifespan
)


# 2. Middleware, который делает ДВЕ вещи:
#    а) Перехватывает токен из заголовка Authorization
#    б) Обходит защиту от DNS-rebinding (подменяет Host)
class AuthAndHostMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # а) Извлекаем Bearer токен
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            current_auth_token.set(token)
            logger.debug(f"Перехвачен токен из заголовка: {token[:10]}...")

        # б) Подменяем Host для обхода защиты MCP SDK
        new_headers = []
        for k, v in request.scope["headers"]:
            if k == b"host":
                new_headers.append((b"host", b"localhost:8001"))
            else:
                new_headers.append((k, v))
        request.scope["headers"] = new_headers

        return await call_next(request)


# 3. Регистрируем инструменты.
# ВАЖНО: auth_token теперь ОПЦИОНАЛЬНЫЙ (str | None = None)
@server.tool()
async def list_entities_tool(auth_token: str | None = None):
    """Список доступных сущностей 1С с алиасами"""
    # Если токен не передан в аргументах, берем его из HTTP-заголовка
    token_to_use = auth_token or current_auth_token.get()
    return await list_entities.execute({"auth_token": token_to_use})


@server.tool()
async def get_metadata_tool(auth_token: str | None = None, entity: str = ""):
    """Структура полей сущности с алиасами"""
    token_to_use = auth_token or current_auth_token.get()
    return await get_metadata.execute({"auth_token": token_to_use, "entity": entity})


@server.tool()
async def fetch_and_process_tool(
    auth_token: str | None = None,
    entity: str = "",
    select: list[str] = [],
    where: list[dict] = [],
    group_by: list[str] = [],
    measures: list[dict] = [],
    order_by: list[dict] = [],
    limit: int = 50
):
    """Запрос и обработка данных из 1С"""
    token_to_use = auth_token or current_auth_token.get()
    return await fetch_and_process.execute({
        "auth_token": token_to_use,
        "entity": entity,
        "select": select,
        "where": where,
        "group_by": group_by,
        "measures": measures,
        "order_by": order_by,
        "limit": limit,
    })


@server.tool()
async def lookup_tool(
    auth_token: str | None = None,
    entity: str = "",
    query: str = "",
    fields: list[str] = [],
    limit: int = 10
):
    """Текстовый поиск по кэшированным справочникам 1С"""
    token_to_use = auth_token or current_auth_token.get()
    return await lookup.execute({
        "auth_token": token_to_use,
        "entity": entity,
        "query": query,
        "fields": fields,
        "limit": limit,
    })


@server.tool()
async def refresh_cache_tool(auth_token: str | None = None, entity: str = "", mode: str = "full"):
    """Принудительное обновление кэша сущности"""
    token_to_use = auth_token or current_auth_token.get()
    return await refresh_cache.execute({
        "auth_token": token_to_use,
        "entity": entity,
        "mode": mode
    })


def run_sse():
    app = server.sse_app()
    # Оборачиваем приложение в наш новый middleware
    app = AuthAndHostMiddleware(app)
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")


if __name__ == "__main__":
    run_sse()
