from mcp_1c_odata.tools import list_entities, get_metadata, fetch_and_process, lookup
from mcp_1c_odata.auth.db import init_db, create_token
import os
import asyncio
from rich import print

os.environ.setdefault(
    "ODATA_BASE_URL", "http://192.168.14.152/unf_14062023/odata/standard.odata")
# Убедись, что в .env или здесь прописаны корректные ODATA_USER и ODATA_PASSWORD


async def test():
    init_db()

    token = create_token("test_llm_final", "nomenclature,sales", days=1)
    print(f"✓ Токен создан: {token}\n")

    print("=== Тест 1: list_entities ===")
    result = await list_entities.execute({"auth_token": token})
    print(f"Найдено сущностей: {len(result.get('entities', []))}")
    print()

    print("=== Тест 2: get_metadata ===")
    result = await get_metadata.execute({"auth_token": token, "entity": "nomenclature"})
    print(f"Полей в метаданных: {len(result.get('fields', []))}")
    print()

    print("=== Тест 3: fetch_and_process ===")
    result = await fetch_and_process.execute({
        "auth_token": token,
        "entity": "nomenclature",
        "select": ["name", "code"],
        "where": [{"field": "code", "op": "contains", "value": "00"}],
        "limit": 5
    })
    print(
        f"Всего найдено: {result.get('total_count')}, обрезано: {result.get('truncated')}")
    print(f"Первая запись: {result.get('data', [{}])[0]}")
    print()

    print("=== Тест 4: lookup ===")
    result = await lookup.execute({
        "auth_token": token,
        "entity": "nomenclature",
        "query": "Набивка",  # Ищем то, что точно есть в базе
        "limit": 3
    })
    print(
        f"Найдено совпадений: {result.get('total_found')}, возвращено: {result.get('returned')}")
    if result.get('data'):
        print("Первая запись: {result['data'][0]}")
    print()

if __name__ == "__main__":
    asyncio.run(test())
