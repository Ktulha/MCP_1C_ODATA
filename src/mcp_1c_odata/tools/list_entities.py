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

    role = role_registry.get(auth_info["role"])
    allowed = role_registry.effective_entities(role, auth_info["scope"])

    entities = []
    for item in config.allowlist:
        if item.get("alias") in allowed:
            entities.append({
                "alias": item.get("alias"),
                "name": item.get("name"),
                "description": item.get("description", "")
            })
    return {"entities": entities}
