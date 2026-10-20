import pandas as pd
from mcp_1c_odata.odata.client import odata_client
from mcp_1c_odata.cache.storage import cache_storage
from mcp_1c_odata.config import config
from mcp_1c_odata.auth.middleware import require_auth, AuthError
from mcp_1c_odata.logs.decorator import logged
from mcp_1c_odata.rate_limit.decorator import rate_limited


@rate_limited("fetch_and_process")
@logged("fetch_and_process")
async def execute(arguments: dict) -> dict:
    auth_token = arguments.get("auth_token")
    entity = arguments.get("entity")
    select = arguments.get("select", [])
    where = arguments.get("where", [])
    group_by = arguments.get("group_by", [])
    measures = arguments.get("measures", [])
    order_by = arguments.get("order_by", [])
    limit = arguments.get("limit", config.response_rows)

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
        auth_info = require_auth(
            auth_token, entity=alias, tool="fetch_and_process")
    except AuthError as e:
        return {"error": str(e)}

    # Жёсткий лимит по роли
    limit = min(limit, auth_info["max_rows"])

    df = cache_storage.load_entity(alias)

    if df is None or len(df) == 0:
        try:
            # Загружаем все записи с пагинацией
            records = await odata_client.get_all_pages(full_name, page_size=5000)

            if not records:
                return {"error": f"1С вернула пустой результат для '{full_name}'. Проверьте права пользователя OData или имя сущности."}

            df = pd.DataFrame(records)
            if len(df) > 0:
                cache_storage.save_entity(alias, df)
        except Exception as e:
            return {"error": f"Ошибка загрузки из 1С: {str(e)}"}

    assert df is not None and len(df) > 0

    mapping = config.field_mapping.get(full_name, {})
    rename_dict = {real_name: alias_name for real_name,
                   alias_name in mapping.items() if real_name in df.columns}
    df = df.rename(columns=rename_dict)

    if where:
        for condition in where:
            field = condition.get("field")
            op = condition.get("op")
            value = condition.get("value")

            if field not in df.columns:
                return {"error": f"Поле '{field}' не найдено. Доступные поля: {list(df.columns)}"}

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

    if group_by and measures:
        agg_dict = {}
        for measure in measures:
            field = measure.get("field")
            agg = measure.get("agg", "sum")
            if field in df.columns:
                agg_dict[field] = agg

        if agg_dict:
            df = df.groupby(group_by, as_index=False).agg(agg_dict)

    if order_by:
        sort_cols = []
        ascending = []
        for order in order_by:
            field = order.get("field")
            desc = order.get("desc", False)
            if field in df.columns:
                sort_cols.append(field)
                ascending.append(not desc)

        if sort_cols:
            df = df.sort_values(by=sort_cols, ascending=ascending)

    if select:
        available_cols = [col for col in select if col in df.columns]
        if available_cols:
            df = df[available_cols]

    total_count = len(df)
    truncated = False
    message = None

    if len(df) > limit:
        df = df.head(limit)
        truncated = True
        message = f"Результат обрезан до {limit} (лимит роли '{auth_info['role']}'). Всего записей: {total_count}."

    data = df.to_dict(orient="records")

    result = {
        "data": data,
        "total_count": total_count,
        "truncated": truncated
    }

    if message:
        result["message"] = message

    return result
