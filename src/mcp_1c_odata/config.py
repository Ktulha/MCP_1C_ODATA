import os
from pathlib import Path
import yaml
from dotenv import load_dotenv

# Загружаем .env из корня проекта
_env_file = Path.cwd() / ".env"
if _env_file.exists():
    load_dotenv(_env_file, override=False)


class Config:
    def __init__(self):
        config_path = os.getenv("MCP_CONFIG_PATH", "config.yaml")
        with open(config_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}

        # OData настройки
        odata = data.get("odata", {})
        self.odata_base_url = os.getenv(
            "ODATA_BASE_URL", odata.get("base_url", ""))
        self.odata_user = os.getenv("ODATA_USER", odata.get("user", ""))
        self.odata_password = os.getenv(
            "ODATA_PASSWORD", odata.get("password", ""))
        self.allowlist = odata.get("allowlist", [])
        self.field_mapping = odata.get("field_mapping", {})

        # Лимиты
        limits = data.get("limits", {})
        self.response_rows = limits.get("response_rows", 50)
        self.odata_timeout_sec = limits.get("odata_timeout_sec", 30)

        # Дефолты
        defaults = data.get("defaults", {})
        self.filter_deletion_mark = defaults.get("filter_deletion_mark", True)
        self.role = defaults.get("role", "viewer")

        # Роли
        self.roles = data.get("roles", {})

        # Кэш
        cache = data.get("cache", {})
        self.cache_path = Path(cache.get("path", "cache"))
        self.cache_path.mkdir(parents=True, exist_ok=True)

        # Планировщик
        self.scheduler = data.get("scheduler", {"enabled": False})

        # Rate limiting
        self.rate_limits = data.get("rate_limits", {"enabled": False})

        # БД токенов
        self.auth_db_path = os.getenv("MCP_AUTH_DB", "data/auth.db")

        # БД логов LLM
        self.logs_db_path = os.getenv("MCP_LOGS_DB", "data/logs.db")
        Path(self.logs_db_path).parent.mkdir(parents=True, exist_ok=True)


config = Config()
