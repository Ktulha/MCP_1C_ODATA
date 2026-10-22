import os
from pathlib import Path
import yaml
from dotenv import load_dotenv

# 1. Явно указываем пути к .env: сначала ищем в /app/.env (для Docker), затем в текущей папке (для локальной разработки)
_env_file = Path(
    "/app/.env") if Path("/app/.env").exists() else Path.cwd() / ".env"

if _env_file.exists():
    # override=True гарантирует, что переменные из .env перезапишут системные, если они есть
    load_dotenv(_env_file, override=True)
else:
    print(f"WARNING: Файл .env не найден по пути {_env_file}")


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
        # Определяем базовую директорию конфига (из .env или по умолчанию 'config')
        config_dir_str = os.getenv("MCP_CONFIG_DIR", "config")
        # Если мы в Docker, путь абсолютный, иначе относительный
        config_dir = Path(config_dir_str) if Path(
            config_dir_str).is_absolute() else Path.cwd() / config_dir_str

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
        field_mapping = {}
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

        # Прогнозирование и расчет потребностей (BOM)
        # ДОБАВЛЕНО: чтобы исправить AttributeError в bom_builder.py
        self.forecast = data.get("forecast", {})

        # БД токенов и логов
        self.auth_db_path = os.getenv("MCP_AUTH_DB", "data/auth.db")
        self.logs_db_path = os.getenv("MCP_LOGS_DB", "data/logs.db")
        Path(self.logs_db_path).parent.mkdir(parents=True, exist_ok=True)


config = Config()
