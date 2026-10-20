from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from mcp_1c_odata.config import config


@dataclass(frozen=True)
class Role:
    name: str
    entities: tuple[str, ...]
    tools: tuple[str, ...]
    max_rows: int
    description: str = ""

    def allows_entity(self, alias: str) -> bool:
        return "*" in self.entities or alias in self.entities

    def allows_tool(self, tool: str) -> bool:
        return "*" in self.tools or tool in self.tools


class RoleRegistry:
    """Реестр ролей из config.yaml. '*' означает весь allowlist / все инструменты."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("MCP_CONFIG_PATH", "config.yaml"))
        self.default_role = "viewer"
        self.roles: dict[str, Role] = {}
        self.reload()

    def reload(self) -> None:
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        defaults = data.get("defaults") or {}
        self.default_role = defaults.get("role", "viewer")

        self.roles = {}
        for name, raw in (data.get("roles") or {}).items():
            raw = raw or {}
            self.roles[name] = Role(
                name=name,
                entities=tuple(raw.get("entities") or ()),
                tools=tuple(raw.get("tools") or ()),
                max_rows=max(1, int(raw.get("max_rows", 50))),
                description=raw.get("description", ""),
            )

    def get(self, name: str | None) -> Role:
        role = self.roles.get(name or self.default_role)
        if role is None:
            raise ValueError(f"Неизвестная роль: {name}")
        return role

    def all_entity_aliases(self) -> set[str]:
        return {
            item["alias"]
            for item in config.allowlist
            if item.get("alias")
        }

    def allowed_entities(self, role: Role) -> set[str]:
        if "*" in role.entities:
            return self.all_entity_aliases()
        return set(role.entities) & self.all_entity_aliases()

    def validate_scope(self, role_name: str, scope: list[str] | None) -> tuple[str, ...]:
        """Проверка при создании токена: scope не может расширять роль."""
        role = self.get(role_name)
        allowed = self.allowed_entities(role)

        if not scope:
            return tuple(sorted(allowed))

        unknown = set(scope) - allowed
        if unknown:
            raise ValueError(
                f"Роль '{role.name}' не даёт доступ к: {', '.join(sorted(unknown))}"
            )
        return tuple(scope)

    def effective_entities(self, role: Role, token_scope: list[str] | None) -> set[str]:
        """Фактические права токена = роль ∩ scope."""
        allowed = self.allowed_entities(role)
        if token_scope:
            allowed &= set(token_scope)
        return allowed


role_registry = RoleRegistry()
