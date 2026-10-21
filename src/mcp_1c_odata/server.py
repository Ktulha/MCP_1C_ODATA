import asyncio
import logging
from contextlib import asynccontextmanager

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
    init_logs()
    cache_scheduler.start()
    logger.info(
        "MCP сервер запущен в режиме stdio (логирование + планировщик активны)")
    yield
    cache_scheduler.shutdown()
    logger.info("MCP сервер остановлен")


server = MCPServer(
    name="mcp-1c-odata",
    version="0.1.0",
    lifespan=lifespan
)


# === Инструменты (stdio режим) ===

@server.tool()
async def list_entities_tool(auth_token: str | None = None):
    return await list_entities.execute({"auth_token": auth_token})


@server.tool()
async def get_metadata_tool(auth_token: str | None = None, entity: str = ""):
    return await get_metadata.execute({"auth_token": auth_token, "entity": entity})


@server.tool()
async def fetch_and_process_tool(
    auth_token: str | None = None, entity: str = "", select: list[str] = [],
    where: list[dict] = [], group_by: list[str] = [], measures: list[dict] = [],
    order_by: list[dict] = [], limit: int = 50
):
    return await fetch_and_process.execute({
        "auth_token": auth_token, "entity": entity, "select": select,
        "where": where, "group_by": group_by, "measures": measures,
        "order_by": order_by, "limit": limit,
    })


@server.tool()
async def lookup_tool(
    auth_token: str | None = None, entity: str = "", query: str = "",
    fields: list[str] = [], limit: int = 10
):
    return await lookup.execute({
        "auth_token": auth_token, "entity": entity, "query": query,
        "fields": fields, "limit": limit,
    })


@server.tool()
async def refresh_cache_tool(auth_token: str | None = None, entity: str = "", mode: str = "full"):
    return await refresh_cache.execute({"auth_token": auth_token, "entity": entity, "mode": mode})


# === ТОЧКА ВХОДА ДЛЯ STDIO РЕЖИМА ===
async def main():
    logger.info("Запуск MCP сервера mcp-1c-odata (stdio mode)...")
    await server.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())
