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

    full_name, alias = None, None
    for item in config.allowlist:
        if item.get("alias") == entity or item.get("name") == entity:
            full_name = item.get("name")
            alias = item.get("alias")
            break

    if not full_name or not alias:
        return {"error": f"Сущность '{entity}' не найдена или недоступна"}

    try:
        auth_info = require_auth(auth_token, entity=alias, tool="lookup")
    except AuthError as e:
        return {"error": str(e)}

    role_name = auth_info.get("role", config.role)
    role_config = role_registry.get(role_name)
    role_max_rows = role_config.get(
        "max_rows", config.response_rows) if role_config else config.response_rows
    limit = min(limit, role_max_rows)

    df = cache_storage.load_entity(alias)
    if df is None or len(df) == 0:
        return {"error": f"Кэш для сущности '{entity}' пуст. Сначала выполните refresh_cache."}

    if not query:
        return {"error": "Параметр 'query' обязателен для поиска"}

    mapping = config.field_mapping.get(full_name, {})
    rename_dict = {real: alias_name for real,
                   alias_name in mapping.items() if real in df.columns}
    if rename_dict:
        df = df.rename(columns=rename_dict)

    if not fields:
        fields = [col for col in df.columns if pd.api.types.is_string_dtype(
            df[col]) or df[col].dtype == 'object']

    mask = pd.Series([False] * len(df), index=df.index)
    for field in fields:
        if field in df.columns:
            mask = mask | df[field].astype(str).str.contains(
                str(query), case=False, na=False)

    result_df = df[mask].head(limit)
    return {
        "entity": alias, "query": query, "search_fields": fields,
        "data": result_df.to_dict(orient="records"),
        "total_found": int(mask.sum()), "returned": len(result_df)
    }
