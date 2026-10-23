import logging
from mcp_1c_odata.config import config

logger = logging.getLogger("mcp-1c-odata.roles")


class RoleRegistry:
    def __init__(self):
        self.roles = {}
        self.reload()

    def reload(self):
        self.roles = getattr(config, "roles", {})
        logger.info(f"Загружено ролей: {len(self.roles)}")

    def get(self, role_name: str) -> dict:
        return self.roles.get(role_name, {})

    def has_access(self, role_name: str, entity: str, tool: str) -> bool:
        if role_name == "admin":
            return True
        role_config = self.get(role_name)
        if not role_config:
            return False
        allowed_entities = role_config.get("entities", [])
        if "*" not in allowed_entities and entity not in allowed_entities:
            return False
        allowed_tools = role_config.get("tools", [])
        if "*" not in allowed_tools and tool not in allowed_tools:
            return False
        return True

    def effective_entities(self, role_name: str, scope: list[str] | None = None) -> list[str]:
        role_config = self.get(role_name)
        if not role_config:
            return []
        allowed = role_config.get("entities", [])

        if "*" in allowed:
            if scope:
                all_aliases = [e.get("alias")
                               for e in config.allowlist if e.get("alias")]
                return [alias for alias in all_aliases if alias in scope]
            else:
                return [e.get("alias") for e in config.allowlist if e.get("alias")]

        if scope:
            return [e for e in allowed if e in scope]
        return allowed

    def validate_scope(self, role_name: str, scope: list[str] | None = None) -> list[str]:
        role_config = self.get(role_name)
        if not role_config:
            raise ValueError(f"Роль '{role_name}' не найдена")
        allowed = role_config.get("entities", [])
        if "*" in allowed:
            return scope if scope else []
        if not scope:
            return allowed
        invalid_entities = [e for e in scope if e not in allowed]
        if invalid_entities:
            raise ValueError(
                f"Сущности {', '.join(invalid_entities)} недоступны для роли '{role_name}'")
        return scope


role_registry = RoleRegistry()
