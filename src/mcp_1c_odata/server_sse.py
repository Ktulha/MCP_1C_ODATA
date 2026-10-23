import logging
import contextvars
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP

from mcp_1c_odata.tools import list_entities, get_metadata, fetch_and_process, lookup, refresh_cache
from mcp_1c_odata.cache.scheduler import cache_scheduler
from mcp_1c_odata.logs.storage import init_logs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("mcp-1c-odata")

# Глобальная переменная для хранения токена в контексте запроса
current_auth_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_auth_token", default=None
)

# === ЗАГРУЗКА ИНСТРУКЦИЙ ИЗ ФАЙЛА ===
PROMPT_PATH = Path(__file__).parent / "prompts" / "system_prompt.md"


def _load_instructions() -> str:
    if PROMPT_PATH.exists():
        try:
            return PROMPT_PATH.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Ошибка чтения файла промпта {PROMPT_PATH}: {e}")
            return "Ты — ассистент для работы с производственно-складским учетом 1С через MCP."
    logger.warning(f"Файл промпта не найден: {PROMPT_PATH}")
    return "Ты — ассистент для работы с производственно-складским учетом 1С через MCP."


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
    instructions=_load_instructions(),
    lifespan=lifespan
)


# 1. НИЗКОУРОВНЕВЫЙ ASGI Middleware для обхода проверки Host
class HostValidationBypassMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = []
            for name, value in scope.get("headers", []):
                if name.lower() == b"host":
                    headers.append((b"host", b"localhost:8001"))
                else:
                    headers.append((name, value))
            scope["headers"] = headers
            scope["server"] = ("localhost", 8001)
        await self.app(scope, receive, send)


# 2. ЧИСТЫЙ ASGI Middleware для аутентификации (ЗАМЕНА BaseHTTPMiddleware)
# Это критически важно для работы SSE, так как BaseHTTPMiddleware ломает потоки
class AuthASGIMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            # Преобразуем список кортежей заголовков в словарь для удобного поиска
            headers_dict = {k.decode('utf-8', errors='ignore').lower(): v.decode('utf-8', errors='ignore')
                            for k, v in scope.get("headers", [])}

            auth_header = headers_dict.get("authorization", "")
            if auth_header.lower().startswith("bearer "):
                token = auth_header[7:].strip()
                # Устанавливаем токен в контекст текущего запроса
                current_auth_token.set(token)
                logger.debug(f"Токен установлен из заголовка: {token[:8]}...")

        # Передаем управление следующему мидлвару или приложению
        await self.app(scope, receive, send)


# 3. Регистрация инструментов
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

    # ВАЖНО: Порядок имеет значение. HostValidation должен быть первым.
    starlette_app.add_middleware(HostValidationBypassMiddleware)
    # <-- Используем новый ASGI мидлвар
    starlette_app.add_middleware(AuthASGIMiddleware)

    uvicorn.run(starlette_app, host="0.0.0.0", port=8001, log_level="info")


if __name__ == "__main__":
    run_sse()
