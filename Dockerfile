FROM python:3.11-slim

WORKDIR /app

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Копируем файлы проекта
COPY pyproject.toml uv.lock ./
COPY src/ ./src/

# Устанавливаем uv и зависимости
RUN pip install --no-cache-dir uv
RUN uv sync --frozen

# Копируем конфигурационные файлы
COPY config.yaml .env.example ./

# Создаём директории для данных
RUN mkdir -p /app/data /app/cache

# Открываем порты для админки (8000) и MCP SSE (8001)
EXPOSE 8000 8001

# По умолчанию запускаем админку (можно переопределить в docker-compose)
CMD ["uv", "run", "mcp-admin", "--host", "0.0.0.0", "--port", "8000"]