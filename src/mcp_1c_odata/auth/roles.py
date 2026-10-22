import logging
from mcp_1c_odata.config import config

logger = logging.getLogger("mcp-1c-odata.roles")


class RoleRegistry:
    def __init__(self):
        self.roles = {}
        self.reload()

    def reload(self):
        """Перезагрузка ролей из центрального объекта config"""
        self.roles = getattr(config, "roles", {})
        logger.info(f"Загружено ролей: {len(self.roles)}")

    def get(self, role_name: str) -> dict:
        """Получить конфигурацию роли"""
        return self.roles.get(role_name, {})

    def has_access(self, role_name: str, entity: str, tool: str) -> bool:
        """Проверка прав доступа роли к сущности и инструменту"""
        if role_name == "admin":
            return True

        role_config = self.get(role_name)
        if not role_config:
            return False

        # Проверка сущности
        allowed_entities = role_config.get("entities", [])
        if "*" not in allowed_entities and entity not in allowed_entities:
            return False

        # Проверка инструмента
        allowed_tools = role_config.get("tools", [])
        if "*" not in allowed_tools and tool not in allowed_tools:
            return False

        return True

    def effective_entities(self, role_name: str, scope: list[str] | None = None) -> list[str]:
        """Возвращает список доступных сущностей для роли с учетом скоупа токена"""
        role_config = self.get(role_name)
        if not role_config:
            return []

        allowed = role_config.get("entities", [])
        if "*" in allowed:
            return scope if scope else []

        if scope:
            return [e for e in allowed if e in scope]
        return allowed


role_registry = RoleRegistry()
