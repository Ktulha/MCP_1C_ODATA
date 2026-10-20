import logging
from dataclasses import dataclass

from mcp_1c_odata.config import config
from mcp_1c_odata.auth.db import verify_token
from mcp_1c_odata.rate_limit.storage import increment, init_rate_limit

logger = logging.getLogger("mcp-1c-odata.rate_limit")


@dataclass
class RateLimitResult:
    allowed: bool
    token_name: str | None
    role: str | None
    limit: int | None
    count: int | None
    remaining_sec: int | None
    message: str | None = None


class RateLimiter:
    def __init__(self):
        self.enabled = False
        self.limits_by_role: dict[str, int] = {}
        self.window_sec: int = 60
        self.reload()

    def reload(self):
        """Перезагрузка конфигурации из config.yaml"""
        rl_config = config.rate_limits if hasattr(
            config, "rate_limits") else {}
        self.enabled = rl_config.get("enabled", False)
        self.window_sec = rl_config.get("window_sec", 60)
        self.limits_by_role = rl_config.get("limits_by_role", {})

        if self.enabled:
            init_rate_limit()
            logger.info(
                f"Rate limit включён: окно={self.window_sec}с, лимиты={self.limits_by_role}")

    def check(self, auth_token: str | None) -> RateLimitResult:
        """Проверка лимита для токена."""
        if not self.enabled:
            return RateLimitResult(
                allowed=True,
                token_name=None, role=None,
                limit=None, count=None, remaining_sec=None
            )

        if not auth_token:
            return RateLimitResult(
                allowed=False,
                token_name=None, role=None,
                limit=None, count=None, remaining_sec=None,
                message="Токен не предоставлен"
            )

        token_info = verify_token(auth_token)
        if not token_info:
            return RateLimitResult(
                allowed=False,
                token_name=None, role=None,
                limit=None, count=None, remaining_sec=None,
                message="Невалидный или просроченный токен"
            )

        token_name = token_info.get("name")
        role = token_info.get("role")

        # Если имя токена неизвестно — отказываем
        if not token_name:
            return RateLimitResult(
                allowed=False,
                token_name=None, role=role,
                limit=None, count=None, remaining_sec=None,
                message="У токена не определено имя"
            )

        # Если роль неизвестна — отказываем
        if not role:
            return RateLimitResult(
                allowed=False,
                token_name=token_name, role=None,
                limit=None, count=None, remaining_sec=None,
                message="У токена не определена роль"
            )

        # Получаем лимит для роли
        limit = self.limits_by_role.get(role)
        if limit is None:
            # Если для роли нет лимита — разрешаем
            logger.warning(
                f"Для роли '{role}' не задан лимит — запрос разрешён")
            return RateLimitResult(
                allowed=True,
                token_name=token_name, role=role,
                limit=None, count=None, remaining_sec=None
            )

        # Инкремент счётчика (теперь token_name гарантированно str)
        count, remaining_sec = increment(token_name, self.window_sec)

        if count > limit:
            logger.warning(
                f"Rate limit превышен: token={token_name}, role={role}, "
                f"count={count}, limit={limit}, window={self.window_sec}с"
            )
            return RateLimitResult(
                allowed=False,
                token_name=token_name, role=role,
                limit=limit, count=count, remaining_sec=remaining_sec,
                message=f"Превышен лимит запросов ({limit} за {self.window_sec}с). Попробуйте через {remaining_sec}с."
            )

        return RateLimitResult(
            allowed=True,
            token_name=token_name, role=role,
            limit=limit, count=count, remaining_sec=remaining_sec
        )

    def get_limits_info(self) -> dict:
        """Информация о лимитах (для админки)"""
        return {
            "enabled": self.enabled,
            "window_sec": self.window_sec,
            "limits_by_role": self.limits_by_role,
        }


rate_limiter = RateLimiter()
