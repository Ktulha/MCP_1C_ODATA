import logging
from contextlib import asynccontextmanager

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.sse import SseServerTransport
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route

from mcp_1c_odata.tools import list_entities, get_metadata, fetch_and_process, lookup, refresh_cache
from mcp_1c_odata.cache.scheduler import cache_scheduler
from mcp_1c_odata.logs.storage import init_logs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("mcp-1c-odata")


# === ВРЕМЕННЫЙ DEBUG MIDDLEWARE ===
# Он выведет в логи точный заголовок Host, который присылает n8n
class HostLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        host_header = request.headers.get("host", "UNKNOWN")
        logger.info(
            f"🔍 Входящий запрос: Host='{host_header}' | Path={request.url.path}")
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: Starlette):
    init_logs()
    cache_scheduler.start()
    logger.info("MCP SSE сервер запущен на http://0.0.0.0:8001/sse")
    yield
    cache_scheduler.shutdown()
    logger.info("MCP SSE сервер остановлен")


# 1. Создаем экземпляр MCPServer
server = MCPServer(
    name="mcp-1c-odata",
    version="0.1.0"
)


# 2. Максимально широкий список разрешенных хостов (включая варианты с портом)
security_settings = TransportSecuritySettings(
    enable_dns_rebinding_protection=False,
    allowed_hosts=[
        "192.168.18.18",
        "192.168.18.18:8001",
        "localhost",
        "localhost:8001",
        "0.0.0.0",
        "0.0.0.0:8001",
        "mcp-server",       # Имя сервиса в docker-compose
        "mcp-server:8001"
    ]
)


# 3. Создаем SSE транспорт с нашими настройками
sse_transport = SseServerTransport(
    endpoint="/messages/",
    security_settings=security_settings
)


# 4. Обработчик SSE-соединения
async def handle_sse(request: Request) -> Response:
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as (read_stream, write_stream):
        await server.run(read_stream, write_stream)  # type: ignore[arg-type]
    return Response()


# 5. Регистрируем инструменты
@server.tool()
async def list_entities_tool(auth_token: str):
    return await list_entities.execute({"auth_token": auth_token})


@server.tool()
async def get_metadata_tool(auth_token: str, entity: str):
    return await get_metadata.execute({"auth_token": auth_token, "entity": entity})


@server.tool()
async def fetch_and_process_tool(auth_token: str, entity: str, select: list[str] = [], where: list[dict] = [], group_by: list[str] = [], measures: list[dict] = [], order_by: list[dict] = [], limit: int = 50):
    return await fetch_and_process.execute({"auth_token": auth_token, "entity": entity, "select": select, "where": where, "group_by": group_by, "measures": measures, "order_by": order_by, "limit": limit})


@server.tool()
async def lookup_tool(auth_token: str, entity: str, query: str, fields: list[str] = [], limit: int = 10):
    return await lookup.execute({"auth_token": auth_token, "entity": entity, "query": query, "fields": fields, "limit": limit})


@server.tool()
async def refresh_cache_tool(auth_token: str, entity: str, mode: str = "full"):
    return await refresh_cache.execute({"auth_token": auth_token, "entity": entity, "mode": mode})


# 6. Создаем Starlette приложение
routes = [
    Route("/sse", endpoint=handle_sse, methods=["GET"]),
    Mount("/messages/", app=sse_transport.handle_post_message),
]
starlette_app = Starlette(routes=routes, lifespan=lifespan)

# Добавляем наш middleware для отладки заголовков
starlette_app.add_middleware(HostLoggingMiddleware)


def run_sse():
    uvicorn.run(starlette_app, host="0.0.0.0", port=8001, log_level="info")


if __name__ == "__main__":
    run_sse()
