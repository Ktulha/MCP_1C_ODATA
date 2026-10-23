from mcp_1c_odata.config import config
from mcp_1c_odata.auth.middleware import require_auth, AuthError
from mcp_1c_odata.auth.roles import role_registry
from mcp_1c_odata.logs.decorator import logged
from mcp_1c_odata.rate_limit.decorator import rate_limited


@rate_limited("list_entities")
@logged("list_entities")
async def execute(arguments: dict) -> dict:
    auth_token = arguments.get("auth_token")
    try:
        auth_info = require_auth(auth_token, tool="list_entities")
    except AuthError as e:
        return {"error": str(e)}

    role_name = auth_info.get("role", config.role)
    if not role_name:
        return {"error": "Не удалось определить роль пользователя"}

    allowed = role_registry.effective_entities(
        role_name, auth_info.get("scope"))

    entities = []
    for item in config.allowlist:
        alias = item.get("alias")
        if alias and alias in allowed:
            entities.append({
                "alias": alias,
                "description": f"{item.get('description')} (Внутреннее имя 1С: {item.get('name')})",
            })

    return {
        "instruction": "КРИТИЧЕСКИ ВАЖНО: При вызове инструментов ВСЕГДА используйте значение из поля 'alias'. НИКОГДА не используйте внутренние имена 1С.",
        "entities": entities
    }
