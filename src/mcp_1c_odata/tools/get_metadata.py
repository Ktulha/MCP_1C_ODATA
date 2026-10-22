import duckdb
from mcp_1c_odata.odata.metadata import metadata_parser
from mcp_1c_odata.config import config
from mcp_1c_odata.cache.storage import cache_storage
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


def _get_view_metadata(alias: str) -> list[dict]:
    """Получение метаданных view из DuckDB через DESCRIBE.

    Returns:
        Список полей: [{"alias": "field_name", "name": "field_name", "type": "VARCHAR"}]
    """
    try:
        conn = cache_storage._get_conn(read_only=True)
        try:
            # DESCRIBE возвращает колонки: column_name, column_type, null, key, default, extra
            result = conn.execute(f"DESCRIBE {alias}").fetchall()
            fields = []
            for row in result:
                column_name = row[0]
                column_type = row[1]
                fields.append({
                    "alias": column_name,
                    "name": column_name,
                    "type": column_type
                })
            return fields
        finally:
            conn.close()
    except Exception as e:
        raise RuntimeError(f"Ошибка получения метаданных view '{alias}': {e}")


@rate_limited("get_metadata")
@logged("get_metadata")
async def execute(arguments: dict) -> dict:
    auth_token = arguments.get("auth_token")
    entity = arguments.get("entity")

    if not entity:
        return {"error": "Параметр 'entity' обязателен"}

    # 1. Находим конфигурацию сущности
    full_name, alias, is_view = _find_entity_config(entity)
    if not full_name or not alias:
        return {"error": f"Сущность '{entity}' не найдена или недоступна"}

    # 2. Проверка аутентификации и прав
    try:
        require_auth(auth_token, entity=alias, tool="get_metadata")
    except AuthError as e:
        return {"error": str(e)}

    # 3. Получаем метаданные
    if is_view:
        # Для view — получаем метаданные из DuckDB
        try:
            result_fields = _get_view_metadata(alias)
        except RuntimeError as e:
            return {"error": str(e)}
    else:
        # Для обычных сущностей — получаем метаданные из 1С
        metadata = await metadata_parser.get_metadata()

        # Добавлена проверка на пустой результат
        if not metadata:
            return {
                "error": (
                    f"Не удалось получить метаданные из 1С. "
                    f"Проверьте подключение к OData и доступность '{full_name}'."
                )
            }

        entity_meta = metadata.get(full_name, {})
        if not entity_meta:
            return {
                "error": (
                    f"Сущность '{full_name}' не найдена в метаданных 1С. "
                    f"Возможно, она отсутствует в базе или имеет другое имя."
                )
            }

        fields = entity_meta.get("properties", [])
        if not fields:
            return {
                "error": f"Сущность '{full_name}' не содержит полей (properties пуст)."
            }

        mapping = config.field_mapping.get(full_name, {})
        result_fields = []
        for field in fields:
            field_name = field.get("name")
            field_alias = mapping.get(field_name, field_name)
            result_fields.append({
                "alias": field_alias,
                "name": field_name,
                "type": field.get("type")
            })

    return {"entity": alias, "fields": result_fields, "is_view": is_view}
