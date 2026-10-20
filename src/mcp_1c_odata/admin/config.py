import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

# Загружаем .env из корня проекта (рядом с pyproject.toml)
_env_file = Path.cwd() / ".env"
if _env_file.exists():
    load_dotenv(_env_file, override=False)

ADMIN_DB_PATH = os.getenv("MCP_ADMIN_DB", str(Path("data") / "admin.db"))
ADMIN_SECRET = os.getenv("MCP_ADMIN_SECRET") or secrets.token_hex(32)
ADMIN_PASSWORD = os.getenv("MCP_ADMIN_PASSWORD")
JWT_ALGORITHM = "HS256"
JWT_TTL_HOURS = 8
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

Path(ADMIN_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
