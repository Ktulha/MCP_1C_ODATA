import pandas as pd
from mcp_1c_odata.odata.client import odata_client
from mcp_1c_odata.cache.storage import cache_storage
from mcp_1c_odata.config import config
from mcp_1c_odata.auth.middleware import require_auth, AuthError
from mcp_1c_odata.auth.roles import role_registry
from mcp_1c_odata.logs.decorator import logged
from mcp_1c_odata.rate_limit.decorator import rate_limited


def _find_entity_config(entity: str) -> tuple[str | None, str | None, bool, str]:
    for item in config.allowlist:
        if item.get("alias") == entity or item.get("name") == entity:
            return item.get("name"), item.get("alias"), item.get("is_view", False), item.get("cache_mode", "full")
    return None, None, False, "full"


@rate_limited("fetch_and_process")
@logged("fetch_and_process")
async def execute(arguments: dict) -> dict:
    auth_token = arguments.get("auth_token")
    entity = arguments.get("entity")
    if not entity:
        return {"error": "Параметр 'entity' обязателен"}

    select = arguments.get("select") or []
    where = arguments.get("where", [])
    group_by = arguments.get("group_by", [])
    measures = arguments.get("measures", [])
    order_by = arguments.get("order_by", [])
    limit = arguments.get("limit", config.response_rows)

    full_name, alias, is_view, cache_mode = _find_entity_config(entity)
    if not full_name or not alias:
        return {"error": f"Сущность '{entity}' не найдена или недоступна"}

    try:
        auth_info = require_auth(
            auth_token, entity=alias, tool="fetch_and_process")
    except AuthError as e:
        return {"error": str(e)}

    role_name = auth_info.get("role", config.role)
    role_config = role_registry.get(role_name)
    role_max_rows = role_config.get(
        "max_rows", config.response_rows) if role_config else config.response_rows
    effective_limit = min(limit, role_max_rows)

    if cache_mode == "derived":
        df = cache_storage.load_entity(alias)
        if df is None or len(df) == 0:
            return {"error": f"Производная витрина '{alias}' ещё не построена."}
    elif cache_mode == "filtered_only":
        has_date_filter = any(cond.get("field") in [
                              "period", "date", "Период", "Дата"] for cond in where)
        if not has_date_filter:
            return {"error": f"Сущность '{alias}' требует фильтр по периоду. Пример: where=[{{'field': 'period', 'op': '>=', 'value': '2026-10-01T00:00:00'}}]"}
        df = cache_storage.load_entity(alias)
        if df is None or len(df) == 0:
            try:
                records = await odata_client.get_all_pages(full_name, page_size=5000)
                if not records:
                    return {"error": f"1С вернула пустой результат для '{full_name}'."}
                df = pd.DataFrame(records)
                cache_storage.save_entity(alias, df)
            except Exception as e:
                return {"error": f"Ошибка загрузки из 1С: {str(e)}"}
    elif is_view:
        df = cache_storage.load_entity(alias)
        if df is None or len(df) == 0:
            return {"error": f"Скомпилированное представление '{alias}' пусто. Сначала загрузите базовые сущности."}
    else:
        df = cache_storage.load_entity(alias)
        if df is None or len(df) == 0:
            try:
                records = await odata_client.get_all_pages(full_name, page_size=5000)
                if not records:
                    return {"error": f"1С вернула пустой результат для '{full_name}'."}
                df = pd.DataFrame(records)
                cache_storage.save_entity(alias, df)
            except Exception as e:
                return {"error": f"Ошибка загрузки из 1С: {str(e)}"}

    if where:
        for condition in where:
            field, op, value = condition.get("field"), condition.get(
                "op", "="), condition.get("value")
            if field and field in df.columns:
                try:
                    if op == "=":
                        df = df[df[field] == value]
                    elif op == "!=":
                        df = df[df[field] != value]
                    elif op == ">":
                        df = df[df[field] > value]
                    elif op == ">=":
                        df = df[df[field] >= value]
                    elif op == "<":
                        df = df[df[field] < value]
                    elif op == "<=":
                        df = df[df[field] <= value]
                    elif op == "contains":
                        df = df[df[field].astype(str).str.contains(
                            str(value), case=False, na=False)]
                except Exception as e:
                    return {"error": f"Ошибка фильтра по полю '{field}': {str(e)}"}

    if group_by and measures:
        try:
            agg_dict = {m.get("field"): m.get("agg", "sum")
                        for m in measures if m.get("field") in df.columns}
            if agg_dict:
                df = df.groupby(group_by, as_index=False).agg(agg_dict)
        except Exception as e:
            return {"error": f"Ошибка группировки: {str(e)}"}

    if order_by:
        try:
            sort_cols, ascending = [], []
            for order in order_by:
                if order.get("field") in df.columns:
                    sort_cols.append(order.get("field"))
                    ascending.append(not order.get("desc", False))
            if sort_cols:
                df = df.sort_values(by=sort_cols, ascending=ascending)
        except Exception as e:
            return {"error": f"Ошибка сортировки: {str(e)}"}

    total_count = len(df)
    if select:
        valid_select = [f for f in select if f in df.columns]
        if valid_select:
            df = df[valid_select]

    truncated = total_count > effective_limit
    df = df.head(effective_limit)

    return {
        "entity": alias, "data": df.to_dict(orient="records"),
        "total_count": total_count, "returned": len(df),
        "truncated": truncated, "is_view": is_view, "cache_mode": cache_mode
    }
