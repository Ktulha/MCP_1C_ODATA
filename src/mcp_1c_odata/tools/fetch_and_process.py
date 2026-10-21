import pandas as pd
from mcp_1c_odata.odata.client import odata_client
from mcp_1c_odata.cache.storage import cache_storage
from mcp_1c_odata.config import config
from mcp_1c_odata.auth.middleware import require_auth, AuthError
from mcp_1c_odata.logs.decorator import logged
from mcp_1c_odata.rate_limit.decorator import rate_limited


def _find_entity_config(entity: str) -> tuple[str | None, str | None, bool]:
    """Поиск сущности в allowlist.

    Returns:
        (full_name, alias, is_view)
    """
    for item in config.allowlist:
        if item.get("alias") == entity or item.get("name") == entity:
            return item.get("name"), item.get("alias"), item.get("is_view", False)
    return None, None, False


@rate_limited("fetch_and_process")
@logged("fetch_and_process")
async def execute(arguments: dict) -> dict:
    auth_token = arguments.get("auth_token")
    entity = arguments.get("entity")

    # Явная проверка обязательного параметра (нужна для типизатора)
    if not entity:
        return {"error": "Параметр 'entity' обязателен"}

    select = arguments.get("select", [])
    where = arguments.get("where", [])
    group_by = arguments.get("group_by", [])
    measures = arguments.get("measures", [])
    order_by = arguments.get("order_by", [])
    limit = arguments.get("limit", config.response_rows)

    # 1. Находим конфигурацию сущности
    full_name, alias, is_view = _find_entity_config(entity)
    if not full_name or not alias:
        return {"error": f"Сущность '{entity}' не найдена или недоступна"}

    # 2. Проверка аутентификации и прав
    try:
        auth_info = require_auth(
            auth_token, entity=alias, tool="fetch_and_process")
    except AuthError as e:
        return {"error": str(e)}

    # 3. Применяем лимит роли
    role_max_rows = auth_info.get("max_rows", config.response_rows)
    effective_limit = min(limit, role_max_rows)

    # 4. Загружаем данные из кэша
    df = cache_storage.load_entity(alias)

    # 4.1. Для view — если данных нет, НЕ идём в 1С (view не существует в OData)
    if is_view:
        if df is None or len(df) == 0:
            return {
                "error": (
                    f"Скомпилированное представление '{alias}' пусто. "
                    f"Сначала загрузите базовые сущности через refresh_cache "
                    f"(номенклатура, контрагенты, документы и т.д.), "
                    f"затем выполните пересоздание view."
                )
            }
    else:
        # 4.2. Для обычных сущностей — автозагрузка из 1С, если кэш пуст
        if df is None or len(df) == 0:
            try:
                records = await odata_client.get_all_pages(full_name, page_size=5000)
                if not records:
                    return {"error": f"1С вернула пустой результат для '{full_name}'."}
                df = pd.DataFrame(records)
                cache_storage.save_entity(alias, df)
            except Exception as e:
                return {"error": f"Ошибка загрузки из 1С: {str(e)}"}

    # 5. Применяем фильтры (where)
    if where:
        for condition in where:
            field = condition.get("field")
            op = condition.get("op", "=")
            value = condition.get("value")
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
                    return {"error": f"Ошибка применения фильтра по полю '{field}': {str(e)}"}

    # 6. Применяем группировку и агрегации
    if group_by and measures:
        try:
            agg_dict = {}
            for measure in measures:
                field = measure.get("field")
                agg = measure.get("agg", "sum")
                if field and field in df.columns:
                    agg_dict[field] = agg
            if agg_dict:
                df = df.groupby(group_by, as_index=False).agg(agg_dict)
        except Exception as e:
            return {"error": f"Ошибка группировки: {str(e)}"}

    # 7. Применяем сортировку
    if order_by:
        try:
            sort_cols = []
            ascending = []
            for order in order_by:
                field = order.get("field")
                desc = order.get("desc", False)
                if field and field in df.columns:
                    sort_cols.append(field)
                    ascending.append(not desc)
            if sort_cols:
                df = df.sort_values(by=sort_cols, ascending=ascending)
        except Exception as e:
            return {"error": f"Ошибка сортировки: {str(e)}"}

    # 8. Выбираем поля (select)
    total_count = len(df)
    if select:
        valid_select = [f for f in select if f in df.columns]
        if valid_select:
            df = df[valid_select]

    # 9. Обрезаем по лимиту
    truncated = total_count > effective_limit
    df = df.head(effective_limit)

    # 10. Преобразуем в список словарей
    data = df.to_dict(orient="records")

    return {
        "entity": alias,
        "data": data,
        "total_count": total_count,
        "returned": len(data),
        "truncated": truncated,
        "is_view": is_view,
    }
