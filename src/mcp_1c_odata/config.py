import os
from pathlib import Path
import yaml
from dotenv import load_dotenv

# Загружаем .env из корня проекта
_env_file = Path.cwd() / ".env"
if _env_file.exists():
    load_dotenv(_env_file, override=False)


def _load_yaml_file(file_path: Path) -> dict:
    """Загрузка одного YAML-файла"""
    if not file_path.exists():
        return {}
    with open(file_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def _merge_dicts(base: dict, override: dict) -> dict:
    """Рекурсивное слияние словарей"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_dicts(result[key], value)
        elif key in result and isinstance(result[key], list) and isinstance(value, list):
            result[key] = result[key] + value
        else:
            result[key] = value
    return result


class Config:
    def __init__(self):
        # Определяем базовую директорию конфига
        config_dir = Path(os.getenv("MCP_CONFIG_DIR", "config"))

        # Загружаем основной конфиг
        main_config = _load_yaml_file(config_dir / "config.yaml")

        # Загружаем сущности из entities/
        entities_dir = config_dir / "entities"
        entities = {}
        if entities_dir.exists():
            for yaml_file in entities_dir.glob("*.yaml"):
                file_data = _load_yaml_file(yaml_file)
                entities = _merge_dicts(entities, file_data)

        # Загружаем маппинг полей из field_mapping/
        mapping_dir = config_dir / "field_mapping"
        field_mapping: dict = {}
        if mapping_dir.exists():
            for yaml_file in mapping_dir.glob("*.yaml"):
                file_data = _load_yaml_file(yaml_file)
                field_mapping = _merge_dicts(field_mapping, file_data)

        # Объединяем всё
        data = _merge_dicts(main_config, {
            "odata": {
                "allowlist": entities.get("allowlist", []),
                "field_mapping": field_mapping
            }
        })

        # OData настройки
        odata = data.get("odata", {})
        self.odata_base_url: str = os.getenv(
            "ODATA_BASE_URL", odata.get("base_url", ""))
        self.odata_user: str = os.getenv("ODATA_USER", odata.get("user", ""))
        self.odata_password: str = os.getenv(
            "ODATA_PASSWORD", odata.get("password", ""))
        self.allowlist: list[dict] = odata.get("allowlist", [])
        self.field_mapping: dict = odata.get("field_mapping", {})

        # Лимиты
        limits = data.get("limits", {})
        self.response_rows: int = limits.get("response_rows", 50)
        self.odata_timeout_sec: int = limits.get("odata_timeout_sec", 30)

        # Дефолты
        defaults = data.get("defaults", {})
        self.filter_deletion_mark: bool = defaults.get(
            "filter_deletion_mark", True)
        self.role: str = defaults.get("role", "viewer")

        # Роли
        self.roles: dict = data.get("roles", {})

        # Кэш
        cache = data.get("cache", {})
        self.cache_path: Path = Path(cache.get("path", "cache"))
        self.cache_path.mkdir(parents=True, exist_ok=True)

        # Планировщик
        self.scheduler: dict = data.get("scheduler", {"enabled": False})

        # Rate limiting
        self.rate_limits: dict = data.get("rate_limits", {"enabled": False})

        # Прогнозирование и планирование
        self.forecast: dict = data.get("forecast", {
            "horizons_days": [7, 14, 30, 90],
            "default_horizon": 30,
            "history_days": 90,
            "bom": {
                "max_depth": 10,
                "include_operations": True,
                "save_tree": True,
                "save_flat": True,
            }
        })

        # SSE-транспорт
        self.sse: dict = data.get("sse", {
            "enabled": False,
            "host": "0.0.0.0",
            "port": 9000,
        })

        # БД токенов
        self.auth_db_path: str = os.getenv("MCP_AUTH_DB", "data/auth.db")

        # БД логов LLM
        self.logs_db_path: str = os.getenv("MCP_LOGS_DB", "data/logs.db")
        Path(self.logs_db_path).parent.mkdir(parents=True, exist_ok=True)

    def get_entity_config(self, entity: str) -> dict | None:
        """Получить конфигурацию сущности по alias или name"""
        for item in self.allowlist:
            if item.get("alias") == entity or item.get("name") == entity:
                return item
        return None

    def get_default_fields(self, entity: str) -> list[str]:
        """Получить список полей по умолчанию для сущности.

        Используется в fetch_and_process, если LLM не указал select.
        """
        entity_config = self.get_entity_config(entity)
        if entity_config is None:
            return []
        return entity_config.get("default_fields", [])


config = Config()
