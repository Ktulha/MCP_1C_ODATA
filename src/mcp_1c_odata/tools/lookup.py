import pandas as pd
from mcp_1c_odata.cache.storage import cache_storage
from mcp_1c_odata.config import config
from mcp_1c_odata.auth.middleware import require_auth, AuthError
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

    full_name = None
    alias = None
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

    limit = min(limit, auth_info["max_rows"])

    df = cache_storage.load_entity(alias)
    if df is None:
        return {"error": f"Кэш для сущности '{entity}' не найден. Сначала выполните fetch_and_process или refresh_cache."}

    if not query:
        return {"error": "Параметр 'query' обязателен для поиска"}

    mapping = config.field_mapping.get(full_name, {})
    rename_dict = {real_name: alias_name for real_name,
                   alias_name in mapping.items() if real_name in df.columns}
    df = df.rename(columns=rename_dict)

    if not fields:
        fields = [
            col for col in df.columns
            if pd.api.types.is_string_dtype(df[col]) or df[col].dtype == 'object'
        ]

    mask = pd.Series([False] * len(df), index=df.index)
    for field in fields:
        if field in df.columns:
            mask = mask | df[field].astype(str).str.contains(
                str(query), case=False, na=False)

    result_df = df[mask].head(limit)

    return {
        "data": result_df.to_dict(orient="records"),
        "total_found": int(mask.sum()),
        "returned": len(result_df)
    }
