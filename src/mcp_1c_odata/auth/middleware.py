from mcp_1c_odata.auth.db import verify_token
from mcp_1c_odata.auth.roles import role_registry
from mcp_1c_odata.config import config


class AuthError(Exception):
    pass


def require_auth(token: str | None, entity: str | None = None, tool: str | None = None) -> dict:
    if not token:
        raise AuthError(
            "Токен не предоставлен. Используйте Authorization: Bearer <token>")

    token_info = verify_token(token)
    if not token_info:
        raise AuthError("Невалидный или просроченный токен")

    role_name = token_info.get("role", config.role)

    if tool and not role_registry.has_access(role_name, entity or "", tool):
        raise AuthError(
            f"Роль '{role_name}' не имеет доступа к инструменту '{tool}'")

    if entity:
        effective = role_registry.effective_entities(
            role_name, token_info.get("scope"))
        if entity not in effective:
            raise AuthError(
                f"Нет доступа к сущности '{entity}' (роль: {role_name})")

    role_config = role_registry.get(role_name)
    max_rows = role_config.get(
        "max_rows", config.response_rows) if role_config else config.response_rows

    return {
        **token_info,
        "role": role_name,
        "max_rows": max_rows
    }
