import logging
from pathlib import Path
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from mcp_1c_odata.tools import list_entities, get_metadata, fetch_and_process, lookup, refresh_cache
from mcp_1c_odata.cache.scheduler import cache_scheduler
from mcp_1c_odata.logs.storage import init_logs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("mcp-1c-odata")

# === ЗАГРУЗКА ИНСТРУКЦИЙ ИЗ ФАЙЛА (как в server_sse.py) ===
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
# ==========================================================


@asynccontextmanager
async def lifespan(app: FastMCP):
    init_logs()
    cache_scheduler.start()
    logger.info("MCP stdio сервер запущен")
    yield
    cache_scheduler.shutdown()
    logger.info("MCP stdio сервер остановлен")


# Создаем экземпляр FastMCP для stdio-транспорта с инструкциями
app = FastMCP(
    name="mcp-1c-odata",
    instructions=_load_instructions(),  # <-- ДОБАВЛЕНО
    # <-- ДОБАВЛЕНО для корректного управления жизненным циклом
    lifespan=lifespan
)


@app.tool()
async def list_entities_tool(auth_token: str | None = None):
    return await list_entities.execute({"auth_token": auth_token})


@app.tool()
async def get_metadata_tool(auth_token: str | None = None, entity: str = ""):
    return await get_metadata.execute({
        "auth_token": auth_token,
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
        "auth_token": auth_token,
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
        "auth_token": auth_token,
        "entity": entity,
        "query": query,
        "fields": fields or [],
        "limit": limit
    })


@app.tool()
async def refresh_cache_tool(auth_token: str | None = None, entity: str = "", mode: str = "full"):
    return await refresh_cache.execute({
        "auth_token": auth_token,
        "entity": entity,
        "mode": mode
    })


def main():
    """Точка входа для stdio-транспорта (используется по умолчанию в MCP)"""
    # В FastMCP v1 метод run() автоматически использует stdio и обрабатывает lifespan
    app.run()


if __name__ == "__main__":
    main()
