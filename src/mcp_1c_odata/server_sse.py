import logging
import contextvars
from contextlib import asynccontextmanager

import uvicorn
from mcp.server.fastmcp import FastMCP
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

current_auth_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_auth_token", default=None
)

SYSTEM_INSTRUCTIONS = """
Ты — ассистент для работы с производственно-складским учетом 1С через MCP.

СТРОГИЕ ПРАВИЛА:
1. ВСЕГДА используй короткий `alias` (например, 'nomenclature', 'stock_balance_info', 'spec_composition') в параметре `entity`. НИКОГДА не используй полные имена 1С.
2. Сначала вызови `list_entities`, чтобы получить актуальный список доступных `alias` и их описаний.
3. Для остатков и цен ВСЕГДА используй оптимизированные витрины: `stock_enriched`, `prices_enriched` или `stock_balance_info`. Не запрашивай сырые регистры накопления без явного фильтра по дате (`period`).
4. Для прогноза потребности и планирования используй витрины: `spec_composition_enriched`, `production_output_enriched`.
5. Если инструмент возвращает мало данных, укажи нужные поля явно в параметре `select`.
"""


@asynccontextmanager
async def lifespan(app: FastMCP):
    init_logs()
    cache_scheduler.start()
    logger.info("MCP SSE сервер запущен на http://0.0.0.0:8001/sse")
    yield
    cache_scheduler.shutdown()
    logger.info("MCP SSE сервер остановлен")

app = FastMCP(
    name="mcp-1c-odata",
    instructions=SYSTEM_INSTRUCTIONS,
    lifespan=lifespan
)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            current_auth_token.set(token)
            logger.debug(f"Токен установлен из заголовка: {token[:8]}...")
        return await call_next(request)


@app.tool()
async def list_entities_tool(auth_token: str | None = None):
    return await list_entities.execute({"auth_token": auth_token or current_auth_token.get()})


@app.tool()
async def get_metadata_tool(auth_token: str | None = None, entity: str = ""):
    return await get_metadata.execute({
        "auth_token": auth_token or current_auth_token.get(),
        "entity": entity
    })


@app.tool()
async def fetch_and_process_tool(
    auth_token: str | None = None,
    entity: str = "",
    select: list[str] | None = None,
    where: list[dict] | None = None,
    group_by: list[str] | None = None,
    measures: list[dict] | None = None,
    order_by: list[dict] | None = None,
    limit: int = 50
):
    return await fetch_and_process.execute({
        "auth_token": auth_token or current_auth_token.get(),
        "entity": entity,
        "select": select or [],
        "where": where or [],
        "group_by": group_by or [],
        "measures": measures or [],
        "order_by": order_by or [],
        "limit": limit
    })


@app.tool()
async def lookup_tool(
    auth_token: str | None = None,
    entity: str = "",
    query: str = "",
    fields: list[str] | None = None,
    limit: int = 10
):
    return await lookup.execute({
        "auth_token": auth_token or current_auth_token.get(),
        "entity": entity,
        "query": query,
        "fields": fields or [],
        "limit": limit
    })


@app.tool()
async def refresh_cache_tool(auth_token: str | None = None, entity: str = "", mode: str = "full"):
    return await refresh_cache.execute({
        "auth_token": auth_token or current_auth_token.get(),
        "entity": entity,
        "mode": mode
    })


def run_sse():
    starlette_app = app.sse_app()
    # Оставляем только AuthMiddleware
    starlette_app.add_middleware(AuthMiddleware)
    uvicorn.run(starlette_app, host="0.0.0.0", port=8001, log_level="info")


if __name__ == "__main__":
    run_sse()
