from mcp_1c_odata.odata.metadata import metadata_parser
from mcp_1c_odata.config import config
from mcp_1c_odata.auth.middleware import require_auth, AuthError
from mcp_1c_odata.logs.decorator import logged
from mcp_1c_odata.rate_limit.decorator import rate_limited


@rate_limited("get_metadata")
@logged("get_metadata")
async def execute(arguments: dict) -> dict:
    auth_token = arguments.get("auth_token")
    entity = arguments.get("entity")

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
        require_auth(auth_token, entity=alias, tool="get_metadata")
    except AuthError as e:
        return {"error": str(e)}

    metadata = await metadata_parser.get_metadata()
    entity_meta = metadata.get(full_name, {})
    fields = entity_meta.get("properties", [])

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

    return {"entity": entity, "fields": result_fields}
