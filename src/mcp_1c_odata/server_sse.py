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

# Было:
# current_auth_token = contextvars.ContextVar("current_auth_token", default=None)

# Стало (с явным указанием типа):
current_auth_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_auth_token", default=None)

# === СИСТЕМНЫЕ ИНСТРУКЦИИ ДЛЯ АГЕНТА ===
# Этот текст автоматически отправляется агенту при подключении по SSE
SYSTEM_INSTRUCTIONS = """
Ты работаешь с MCP-сервером для 1С. 
ПРАВИЛО №1: При вызове любых инструментов (get_metadata, fetch_and_process, lookup, refresh_cache) в параметре `entity` ВСЕГДА используй короткий `alias` из списка сущностей (например: 'nomenclature', 'stock_balance', 'prices').
ПРАВИЛО №2: НИКОГДА не используй полные имена 1С (например, 'Catalog_Номенклатура' или 'InformationRegister_ОстаткиТоваров') в качестве значения параметра `entity`. Это вызовет ошибку.
ПРАВИЛО №3: Сначала вызови инструмент `list_entities`, чтобы получить актуальный список доступных `alias`.
"""


@asynccontextmanager
async def lifespan(server: MCPServer):
    init_logs()
    cache_scheduler.start()
    logger.info("MCP SSE сервер запущен на http://0.0.0.0:8001/sse")
    yield
    cache_scheduler.shutdown()
    logger.info("MCP SSE сервер остановлен")


# 1. Создаем экземпляр MCPServer с инструкциями
server = MCPServer(
    name="mcp-1c-odata",
    version="0.1.0",
    instructions=SYSTEM_INSTRUCTIONS,  # <-- КЛЮЧЕВОЕ ДОБАВЛЕНИЕ
    lifespan=lifespan
)


class AuthAndHostMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            current_auth_token.set(token)
            logger.debug(f"Перехвачен токен из заголовка: {token[:10]}...")

        new_headers = []
        for k, v in request.scope["headers"]:
            if k == b"host":
                new_headers.append((b"host", b"localhost:8001"))
            else:
                new_headers.append((k, v))
        request.scope["headers"] = new_headers

        return await call_next(request)


# 2. Регистрируем инструменты (auth_token опционален)
@server.tool()
async def list_entities_tool(auth_token: str | None = None):
    return await list_entities.execute({"auth_token": auth_token or current_auth_token.get()})


@server.tool()
async def get_metadata_tool(auth_token: str | None = None, entity: str = ""):
    return await get_metadata.execute({"auth_token": auth_token or current_auth_token.get(), "entity": entity})


@server.tool()
async def fetch_and_process_tool(auth_token: str | None = None, entity: str = "", select: list[str] = [], where: list[dict] = [], group_by: list[str] = [], measures: list[dict] = [], order_by: list[dict] = [], limit: int = 50):
    return await fetch_and_process.execute({"auth_token": auth_token or current_auth_token.get(), "entity": entity, "select": select, "where": where, "group_by": group_by, "measures": measures, "order_by": order_by, "limit": limit})


@server.tool()
async def lookup_tool(auth_token: str | None = None, entity: str = "", query: str = "", fields: list[str] = [], limit: int = 10):
    return await lookup.execute({"auth_token": auth_token or current_auth_token.get(), "entity": entity, "query": query, "fields": fields, "limit": limit})


@server.tool()
async def refresh_cache_tool(auth_token: str | None = None, entity: str = "", mode: str = "full"):
    return await refresh_cache.execute({"auth_token": auth_token or current_auth_token.get(), "entity": entity, "mode": mode})


def run_sse():
    app = server.sse_app()
    app = AuthAndHostMiddleware(app)
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")


if __name__ == "__main__":
    run_sse()
