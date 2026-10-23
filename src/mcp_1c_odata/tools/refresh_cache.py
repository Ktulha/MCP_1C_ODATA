import pandas as pd
from mcp_1c_odata.odata.client import odata_client
from mcp_1c_odata.cache.storage import cache_storage
from mcp_1c_odata.config import config
from mcp_1c_odata.auth.middleware import require_auth, AuthError
from mcp_1c_odata.logs.decorator import logged
from mcp_1c_odata.rate_limit.decorator import rate_limited


def _find_entity_config(entity: str) -> dict | None:
    for item in config.allowlist:
        if item.get("alias") == entity or item.get("name") == entity:
            return item
    return None


def _get_ref_key_field(full_name: str) -> str:
    mapping = config.field_mapping.get(full_name, {})
    for real_name, alias_name in mapping.items():
        if real_name == "Ref_Key":
            return alias_name
    return "ref_key"


@rate_limited("refresh_cache")
@logged("refresh_cache")
async def execute(arguments: dict) -> dict:
    auth_token = arguments.get("auth_token")
    entity = arguments.get("entity")
    mode = arguments.get("mode", "full")

    if not entity:
        return {"error": "Параметр 'entity' обязателен"}

    entity_config = _find_entity_config(entity)
    if not entity_config:
        return {"error": f"Сущность '{entity}' не найдена или недоступна"}

    full_name = entity_config.get("name")
    alias = entity_config.get("alias")
    is_view = entity_config.get("is_view", False)
    cache_mode = entity_config.get("cache_mode", "full")

    if not full_name or not alias:
        return {"error": f"У сущности '{entity}' отсутствует имя или алиас"}

    try:
        require_auth(auth_token, entity=alias, tool="refresh_cache")
    except AuthError as e:
        return {"error": str(e)}

    if is_view:
        return {"error": f"Сущность '{alias}' — это view. Она пересоздаётся автоматически после обновления базовых сущностей."}
    if cache_mode == "derived":
        return {"error": f"Сущность '{alias}' — производная витрина. Она пересчитывается автоматически."}
    if cache_mode == "filtered_only":
        return {"error": f"Сущность '{alias}' содержит слишком много данных. Используйте fetch_and_process с фильтром по периоду."}

    try:
        if mode == "incremental" and cache_mode == "full":
            params = {"$format": "json"}
            last_updated = cache_storage.get_last_updated(alias)
            if last_updated:
                params["$filter"] = f"DataVersion gt datetime'{last_updated.isoformat()}'"
            response = await odata_client.get(full_name, params=params)
            if "error" in response:
                return {"error": f"Ошибка OData: {response['error']}"}
            records = response.get("value", [])
            if not records:
                return {"status": "ok", "message": "Нет новых записей", "updated": 0}

            new_df = pd.DataFrame(records)
            existing_df = cache_storage.load_entity(alias)
            key_field = _get_ref_key_field(full_name)
            if existing_df is not None and len(existing_df) > 0 and key_field in existing_df.columns:
                combined_df = pd.concat([existing_df, new_df]).drop_duplicates(
                    subset=[key_field], keep='last')
                cache_storage.save_entity(alias, combined_df)
            else:
                cache_storage.save_entity(alias, new_df)

        elif cache_mode == "slice_last":
            records = await odata_client.get_slice_last_all(full_name, page_size=5000)
            if not records:
                return {"status": "ok", "message": "SliceLast вернул пустой результат", "updated": 0}
            cache_storage.save_entity(alias, pd.DataFrame(records))
        else:
            records = await odata_client.get_all_pages(full_name, page_size=5000)
            if not records:
                return {"status": "ok", "message": "1С вернула пустой результат", "updated": 0}
            cache_storage.save_entity(alias, pd.DataFrame(records))

        views_config = [e for e in config.allowlist if e.get("is_view")]
        if views_config:
            cache_storage.create_views(views_config)

        final_df = cache_storage.load_entity(alias)
        return {
            "status": "ok", "entity": alias, "mode": mode, "cache_mode": cache_mode,
            "updated": len(records) if 'records' in locals() else 0,
            "total": len(final_df) if final_df is not None else 0,
        }
    except Exception as e:
        return {"error": f"Ошибка обновления кэша: {str(e)}"}
