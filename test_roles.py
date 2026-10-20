import asyncio
import os
from mcp_1c_odata.auth.db import init_db, create_token
from mcp_1c_odata.tools import list_entities, get_metadata, fetch_and_process, lookup, refresh_cache


async def test_roles():
    init_db()

    print("=== Создание токенов с разными ролями ===\n")

    # Admin токен
    admin_token = create_token(
        "test_locadmin", "nomenclature,variants", role="locadmin", days=1)
    print(f"✓ Admin токен: {admin_token}\n")

    # Analyst токен
    analyst_token = create_token(
        "test_analyst", "nomenclature", role="analyst", days=1)
    print(f"✓ Analyst токен: {analyst_token}\n")

    # Viewer токен
    viewer_token = create_token(
        "test_viewer", "nomenclature", role="viewer", days=1)
    print(f"✓ Viewer токен: {viewer_token}\n")

    print("=" * 60)
    print("Тест 1: list_entities (все роли должны видеть только свои сущности)")
    print("=" * 60)

    print("\n[Admin]:")
    result = await list_entities.execute({"auth_token": admin_token})
    print(f"  Видит сущностей: {len(result.get('entities', []))}")

    print("\n[Analyst]:")
    result = await list_entities.execute({"auth_token": analyst_token})
    print(f"  Видит сущностей: {len(result.get('entities', []))}")

    print("\n[Viewer]:")
    result = await list_entities.execute({"auth_token": viewer_token})
    print(f"  Видит сущностей: {len(result.get('entities', []))}")

    print("\n" + "=" * 60)
    print("Тест 2: fetch_and_process (лимиты по ролям)")
    print("=" * 60)

    print("\n[Admin] limit=2000 (должен обрезать до 1000):")
    result = await fetch_and_process.execute({
        "auth_token": admin_token,
        "entity": "nomenclature",
        "select": ["name", "code"],
        "limit": 2000
    })
    if "error" in result:
        print(f"  Ошибка: {result['error']}")
    else:
        print(f"  Вернулось записей: {len(result.get('data', []))}")
        if result.get('message'):
            print(f"  Сообщение: {result['message']}")

    print("\n[Analyst] limit=200 (должен обрезать до 100):")
    result = await fetch_and_process.execute({
        "auth_token": analyst_token,
        "entity": "nomenclature",
        "select": ["name", "code"],
        "limit": 200
    })
    if "error" in result:
        print(f"  Ошибка: {result['error']}")
    else:
        print(f"  Вернулось записей: {len(result.get('data', []))}")
        if result.get('message'):
            print(f"  Сообщение: {result['message']}")

    print("\n[Viewer] (должен отказать - нет доступа к fetch_and_process):")
    result = await fetch_and_process.execute({
        "auth_token": viewer_token,
        "entity": "nomenclature",
        "limit": 10
    })
    print(f"  Результат: {result.get('error', 'УСПЕХ (не должно быть!)')}")

    print("\n" + "=" * 60)
    print("Тест 3: refresh_cache (только admin)")
    print("=" * 60)

    print("\n[Admin]:")
    result = await refresh_cache.execute({
        "auth_token": admin_token,
        "entity": "nomenclature",
        "mode": "full"
    })
    print(f"  Результат: {result.get('status', result.get('error'))}")

    print("\n[Analyst] (должен отказать):")
    result = await refresh_cache.execute({
        "auth_token": analyst_token,
        "entity": "nomenclature",
        "mode": "full"
    })
    print(f"  Результат: {result.get('error', 'УСПЕХ (не должно быть!)')}")

    print("\n" + "=" * 60)
    print("Тест 4: Доступ к чужой сущности")
    print("=" * 60)

    print("\n[Analyst] пытается получить variants (нет в scope):")
    result = await get_metadata.execute({
        "auth_token": analyst_token,
        "entity": "variants"
    })
    print(f"  Результат: {result.get('error', 'УСПЕХ (не должно быть!)')}")

if __name__ == "__main__":
    asyncio.run(test_roles())
