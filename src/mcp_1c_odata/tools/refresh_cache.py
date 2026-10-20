import pandas as pd
from mcp_1c_odata.odata.client import odata_client
from mcp_1c_odata.cache.storage import cache_storage
from mcp_1c_odata.config import config
from mcp_1c_odata.auth.middleware import require_auth, AuthError
from mcp_1c_odata.logs.decorator import logged
from mcp_1c_odata.rate_limit.decorator import rate_limited


@rate_limited("refresh_cache")
@logged("refresh_cache")
async def execute(arguments: dict) -> dict:
    auth_token = arguments.get("auth_token")
    entity = arguments.get("entity")
    mode = arguments.get("mode", "full")  # full | incremental

    # 1. Находим полное имя и алиас сущности
    full_name = None
    alias = None
    for item in config.allowlist:
        if item.get("alias") == entity or item.get("name") == entity:
            full_name = item.get("name")
            alias = item.get("alias")
            break

    if not full_name or not alias:
        return {"error": f"Сущность '{entity}' не найдена или недоступна"}

    # 2. Проверка аутентификации и прав (refresh_cache — только admin)
    try:
        auth_info = require_auth(
            auth_token, entity=alias, tool="refresh_cache")
    except AuthError as e:
        return {"error": str(e)}

    # 3. Формируем параметры запроса
    params = {"$format": "json"}

    if mode == "incremental":
        last_updated = cache_storage.get_last_updated(alias)
        if last_updated:
            params["$filter"] = f"DataVersion gt datetime'{last_updated.isoformat()}'"

    # 4. Загружаем данные
    try:
        if mode == "incremental":
            # Инкрементальное обновление — один запрос с фильтром
            response = await odata_client.get(full_name, params=params)

            if "error" in response:
                return {"error": f"Ошибка OData: {response['error']}"}

            records = response.get("value", [])
        else:
            # Полное обновление — пагинация, загружаем все записи
            try:
                records = await odata_client.get_all_pages(full_name, page_size=5000)
            except Exception as e:
                return {"error": f"Ошибка OData при полном обновлении: {str(e)}"}

        if not records:
            return {"status": "ok", "message": "Нет новых записей", "updated": 0}

        new_df = pd.DataFrame(records)

        # 5. Сохраняем в кэш
        if mode == "incremental":
            existing_df = cache_storage.load_entity(alias)
            if existing_df is not None and len(existing_df) > 0:
                # Обновляем существующие записи по Ref_Key
                combined_df = pd.concat([existing_df, new_df]).drop_duplicates(
                    subset=['Ref_Key'], keep='last'
                )
                cache_storage.save_entity(alias, combined_df)
            else:
                cache_storage.save_entity(alias, new_df)
        else:
            cache_storage.save_entity(alias, new_df)

        # 6. Возвращаем результат
        final_df = cache_storage.load_entity(alias)
        total = len(final_df) if final_df is not None else 0

        return {
            "status": "ok",
            "mode": mode,
            "updated": len(records),
            "total": total
        }
    except Exception as e:
        return {"error": f"Ошибка обновления кэша: {str(e)}"}
