import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server import MCPServer
from mcp.types import PromptMessage, TextContent

from mcp_1c_odata.tools import list_entities, get_metadata, fetch_and_process, lookup, refresh_cache
from mcp_1c_odata.cache.scheduler import cache_scheduler
from mcp_1c_odata.logs.storage import init_logs
from mcp_1c_odata.config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("mcp-1c-odata")

PROMPT_PATH = Path(__file__).parent / "prompts" / "system_prompt.md"


@asynccontextmanager
async def lifespan(app: MCPServer):
    init_logs()
    cache_scheduler.start()
    logger.info("MCP сервер запущен (логирование + планировщик активны)")
    yield
    cache_scheduler.shutdown()
    logger.info("MCP сервер остановлен")


app = MCPServer(
    name="mcp-1c-odata",
    version="0.1.0",
    description="MCP сервер для работы с 1С через OData с кэшированием, ролями и логированием",
    lifespan=lifespan
)


# === MCP PROMPT: системная инструкция для LLM ===

@app.prompt(
    name="system_prompt",
    description="Системный промпт для LLM-клиента с инструкциями по работе с инструментами 1С"
)
async def system_prompt(role: str = "analyst") -> list[PromptMessage]:
    """Системный промпт для LLM-клиента.

    Args:
        role: Роль пользователя (viewer/analyst/admin) — влияет на описание доступных прав
    """
    if not PROMPT_PATH.exists():
        return [
            PromptMessage(
                role="user",
                content=TextContent(
                    text="Промпт не найден на сервере. Обратитесь к администратору.")
            )
        ]

    base_prompt = PROMPT_PATH.read_text(encoding="utf-8")

    role_info = {
        "viewer": "Твоя роль: **viewer**. Доступны только инструменты list_entities, get_metadata, lookup. Лимит 20 строк.",
        "analyst": "Твоя роль: **analyst**. Доступны list_entities, get_metadata, lookup, fetch_and_process. Лимит 100 строк.",
        "admin": "Твоя роль: **admin**. Полный доступ ко всем инструментам, включая refresh_cache. Лимит 1000 строк.",
    }

    role_hint = role_info.get(role, role_info["analyst"])
    full_prompt = f"{role_hint}\n\n{base_prompt}"

    return [
        PromptMessage(
            role="user",
            content=TextContent(text=full_prompt)
        )
    ]


# === Инструменты ===

@app.tool()
async def list_entities_tool(auth_token: str):
    """Список доступных сущностей 1С с алиасами"""
    return await list_entities.execute({"auth_token": auth_token})


@app.tool()
async def get_metadata_tool(auth_token: str, entity: str):
    """Структура полей сущности с алиасами"""
    return await get_metadata.execute({"auth_token": auth_token, "entity": entity})


@app.tool()
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


@app.tool()
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


@app.tool()
async def refresh_cache_tool(auth_token: str, entity: str, mode: str = "full"):
    """Принудительное обновление кэша сущности"""
    return await refresh_cache.execute({
        "auth_token": auth_token,
        "entity": entity,
        "mode": mode
    })


async def main():
    logger.info("Запуск MCP сервера mcp-1c-odata (stdio mode)...")
    await app.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())
