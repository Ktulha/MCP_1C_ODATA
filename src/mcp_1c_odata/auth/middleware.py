from mcp_1c_odata.auth.db import verify_token
from mcp_1c_odata.auth.roles import role_registry


class AuthError(Exception):
    pass


def require_auth(token: str | None, entity: str | None = None, tool: str | None = None) -> dict:
    """Полная проверка: токен → роль → инструмент → сущность.

    Возвращает: {id, name, role, scope, expires_at, max_rows}
    """
    if not token:
        raise AuthError(
            "Токен не предоставлен. Используйте Authorization: Bearer <token>")

    token_info = verify_token(token)
    if not token_info:
        raise AuthError("Невалидный или просроченный токен")

    role = role_registry.get(token_info["role"])

    # Проверка инструмента
    if tool and not role.allows_tool(tool):
        raise AuthError(
            f"Роль '{role.name}' не имеет доступа к инструменту '{tool}'")

    # Проверка сущности
    if entity:
        effective = role_registry.effective_entities(role, token_info["scope"])
        if entity not in effective:
            raise AuthError(
                f"Нет доступа к сущности '{entity}' (роль: {role.name})")

    return {
        **token_info,
        "role": role.name,
        "max_rows": role.max_rows
    }
