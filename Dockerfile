# Используем официальный образ с уже предустановленным uv и Python 3.11
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

# Копируем файлы зависимостей первыми (для кэширования слоев Docker)
COPY pyproject.toml uv.lock ./
COPY src/ ./src/

# Синхронизируем зависимости. 
# uv уже есть в PATH, установка проходит намного быстрее и стабильнее
RUN uv sync --frozen --no-dev

# Создаём директории для данных, кэша и конфигов (страховка от проблем с volume)
RUN mkdir -p /app/data /app/cache /app/config

# Копируем пример конфига окружения
COPY .env.example ./

# Открываем порты для админки (8000) и MCP SSE (8001)
EXPOSE 8000 8001

# По умолчанию запускаем админку (для mcp-server в docker-compose мы это переопределим)
CMD ["uv", "run", "mcp-admin", "--host", "0.0.0.0", "--port", "8000"]