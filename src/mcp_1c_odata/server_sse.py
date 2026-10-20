import logging
from contextlib import asynccontextmanager

import uvicorn
from mcp.server.mcpserver import MCPServer

from mcp_1c_odata.tools import list_entities, get_metadata, fetch_and_process, lookup, refresh_cache
from mcp_1c_odata.cache.scheduler import cache_scheduler
from mcp_1c_odata.logs.storage import init_logs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("mcp-1c-odata")


@asynccontextmanager
async def lifespan(server: MCPServer):
    # Startup
    init_logs()
    cache_scheduler.start()
    logger.info("MCP SSE сервер запущен на http://0.0.0.0:8001/sse")
    yield
    # Shutdown
    cache_scheduler.shutdown()
    logger.info("MCP SSE сервер остановлен")


# 1. Создаем экземпляр MCPServer (MCP SDK 2.x)
server = MCPServer(
    name="mcp-1c-odata",
    version="0.1.0",
    lifespan=lifespan
)


# 2. Регистрируем инструменты (имена функций с суффиксом _tool)

@server.tool()
async def list_entities_tool(auth_token: str):
    """Список доступных сущностей 1С с алиасами"""
    return await list_entities.execute({"auth_token": auth_token})


@server.tool()
async def get_metadata_tool(auth_token: str, entity: str):
    """Структура полей сущности с алиасами"""
    return await get_metadata.execute({"auth_token": auth_token, "entity": entity})


@server.tool()
async def fetch_and_process_tool(
    auth_token: str,
    entity: str,
    select: list[str] = [],
    where: list[dict] = [],
    group_by: list[str] = [],
    measures: list[dict] = [],
    order_by: list[dict] = [],
    limit: int = 50
):
    """Запрос и обработка данных из 1С"""
    return await fetch_and_process.execute({
        "auth_token": auth_token,
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
    auth_token: str,
    entity: str,
    query: str,
    fields: list[str] = [],
    limit: int = 10
):
    """Текстовый поиск по кэшированным справочникам 1С"""
    return await lookup.execute({
        "auth_token": auth_token,
        "entity": entity,
        "query": query,
        "fields": fields,
        "limit": limit,
    })


@server.tool()
async def refresh_cache_tool(auth_token: str, entity: str, mode: str = "full"):
    """Принудительное обновление кэша сущности"""
    return await refresh_cache.execute({
        "auth_token": auth_token,
        "entity": entity,
        "mode": mode
    })


def run_sse():
    """Точка входа для запуска SSE-сервера"""
    # Получаем готовое ASGI-приложение для SSE из MCPServer
    sse_app = server.sse_app()
    uvicorn.run(sse_app, host="0.0.0.0", port=8001, log_level="info")


if __name__ == "__main__":
    run_sse()
