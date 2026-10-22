import pandas as pd
from mcp_1c_odata.cache.storage import cache_storage
from mcp_1c_odata.config import config
from mcp_1c_odata.auth.middleware import require_auth, AuthError
from mcp_1c_odata.auth.roles import role_registry
from mcp_1c_odata.logs.decorator import logged
from mcp_1c_odata.rate_limit.decorator import rate_limited


@rate_limited("lookup")
@logged("lookup")
async def execute(arguments: dict) -> dict:
    auth_token = arguments.get("auth_token")
    entity = arguments.get("entity")
    query = arguments.get("query", "")
    fields = arguments.get("fields", [])
    limit = arguments.get("limit", 10)

    # 1. Поиск конфигурации сущности
    full_name = None
    alias = None
    for item in config.allowlist:
        if item.get("alias") == entity or item.get("name") == entity:
            full_name = item.get("name")
            alias = item.get("alias")
            break

    if not full_name or not alias:
        return {"error": f"Сущность '{entity}' не найдена или недоступна"}

    # 2. Проверка аутентификации и прав
    try:
        auth_info = require_auth(auth_token, entity=alias, tool="lookup")
    except AuthError as e:
        return {"error": str(e)}

    # 3. ИСПРАВЛЕНО: max_rows берём из role_registry, а не из auth_info
    role_name = auth_info.get("role", config.role)
    role_config = role_registry.get(role_name)
    role_max_rows = role_config.get(
        "max_rows", config.response_rows) if role_config else config.response_rows
    limit = min(limit, role_max_rows)

    # 4. Загрузка данных из кэша
    df = cache_storage.load_entity(alias)
    if df is None or len(df) == 0:
        return {
            "error": (
                f"Кэш для сущности '{entity}' пуст или не найден. "
                f"Сначала выполните refresh_cache для этой сущности."
            )
        }

    if not query:
        return {"error": "Параметр 'query' обязателен для поиска"}

    # 5. Применяем маппинг полей (реальные имена 1С → алиасы)
    mapping = config.field_mapping.get(full_name, {})
    rename_dict = {
        real_name: alias_name
        for real_name, alias_name in mapping.items()
        if real_name in df.columns
    }
    if rename_dict:
        df = df.rename(columns=rename_dict)

    # 6. Если поля для поиска не указаны — ищем по всем строковым полям
    if not fields:
        fields = [
            col for col in df.columns
            if pd.api.types.is_string_dtype(df[col]) or df[col].dtype == 'object'
        ]

    # 7. Формируем маску поиска (OR по всем полям)
    mask = pd.Series([False] * len(df), index=df.index)
    for field in fields:
        if field in df.columns:
            mask = mask | df[field].astype(str).str.contains(
                str(query), case=False, na=False
            )

    # 8. Возвращаем результат
    result_df = df[mask].head(limit)

    return {
        "entity": alias,
        "query": query,
        "search_fields": fields,
        "data": result_df.to_dict(orient="records"),
        "total_found": int(mask.sum()),
        "returned": len(result_df),
    }
