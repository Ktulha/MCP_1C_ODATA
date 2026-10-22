import logging
import contextvars
from contextlib import asynccontextmanager
from pathlib import Path

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

# Глобальная переменная для хранения токена в контексте запроса
current_auth_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_auth_token", default=None
)

# === ЗАГРУЗКА ИНСТРУКЦИЙ ИЗ ФАЙЛА ===
# Путь формируется относительно текущего файла (src/mcp_1c_odata/server_sse.py)
PROMPT_PATH = Path(__file__).parent / "prompts" / "system_prompt.md"


def _load_instructions() -> str:
    """Безопасная загрузка системного промпта из файла."""
    if PROMPT_PATH.exists():
        try:
            return PROMPT_PATH.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Ошибка чтения файла промпта {PROMPT_PATH}: {e}")
            return "Ты — ассистент для работы с производственно-складским учетом 1С через MCP."

    logger.warning(
        f"Файл промпта не найден по пути: {PROMPT_PATH}. Используется резервная инструкция.")
    return "Ты — ассистент для работы с производственно-складским учетом 1С через MCP."
# ====================================


@asynccontextmanager
async def lifespan(app: FastMCP):
    init_logs()
    cache_scheduler.start()
    logger.info("MCP SSE сервер запущен на http://0.0.0.0:8001/sse")
    yield
    cache_scheduler.shutdown()
    logger.info("MCP SSE сервер остановлен")


# 1. Создаем экземпляр FastMCP с инструкциями из файла
app = FastMCP(
    name="mcp-1c-odata",
    instructions=_load_instructions(),  # <-- ЗДЕСЬ ТЕПЕРЬ ЧИТАЕТСЯ ИЗ ФАЙЛА
    lifespan=lifespan
)


# 2. НИЗКОУРОВНЕВЫЙ ASGI Middleware для обхода строгой проверки Host в MCP
class HostValidationBypassMiddleware:
    """
    Корректный обход строгой проверки Host в mcp.server.transport_security.
    Библиотека mcp доверяет 'localhost'. Мы подменяем IP-адрес на localhost 
    на уровне ASGI scope до того, как запрос дойдет до валидатора mcp.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = []
            for name, value in scope.get("headers", []):
                if name.lower() == b"host":
                    # Подменяем на валидное доменное имя, которому доверяет mcp
                    headers.append((b"host", b"localhost:8001"))
                else:
                    headers.append((name, value))
            scope["headers"] = headers

            # Также обновляем server tuple для корректной работы URL-билдеров Starlette
            scope["server"] = ("localhost", 8001)

        await self.app(scope, receive, send)


# 3. Middleware для извлечения токена из заголовка Authorization
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            current_auth_token.set(token)
            logger.debug(f"Токен установлен из заголовка: {token[:8]}...")
        return await call_next(request)


# 4. Регистрация инструментов
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
    # Получаем Starlette-приложение
    starlette_app = app.sse_app()

    # ВАЖНО: HostValidationBypassMiddleware должен быть ПЕРВЫМ (самым внешним),
    # чтобы он изменил scope ДО того, как его увидят другие мидлвары и библиотека mcp.
    starlette_app.add_middleware(HostValidationBypassMiddleware)
    starlette_app.add_middleware(AuthMiddleware)

    # Запускаем Uvicorn
    uvicorn.run(starlette_app, host="0.0.0.0", port=8001, log_level="info")


if __name__ == "__main__":
    run_sse()
