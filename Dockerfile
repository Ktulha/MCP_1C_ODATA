FROM python:3.11-slim

WORKDIR /app

# Устанавливаем системные зависимости для сборки (например, для pyarrow/duckdb)
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Копируем файлы зависимостей и исходный код
COPY pyproject.toml uv.lock ./
COPY src/ ./src/

# Создаем минимальный README.md, так как он указан в pyproject.toml и нужен для сборки пакета
RUN echo "# MCP 1C OData Server" > README.md

# Устанавливаем uv и синхронизируем зависимости
RUN pip install --no-cache-dir uv
RUN uv sync --frozen

# Копируем конфигурационные файлы (будут перезаписаны volumes при запуске)
COPY config.yaml ./

# Создаём директории для данных внутри контейнера
RUN mkdir -p /app/data /app/cache

# Открываем порты для админки (8000) и MCP SSE (8001)
EXPOSE 8000 8001

# Команда по умолчанию (переопределяется в docker-compose.yml)
CMD ["uv", "run", "mcp-admin", "--host", "0.0.0.0", "--port", "8000"]