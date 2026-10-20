FROM python:3.14-slim

WORKDIR /app

# Системные зависимости (для сборки некоторых пакетов)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Копируем метаданные проекта и зависимости
COPY pyproject.toml uv.lock ./

# Устанавливаем зависимости через uv (быстрее pip)
RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev

# Копируем исходники
COPY src/ ./src/

# Директории для данных (будут перезаписаны volumes)
RUN mkdir -p /app/data /app/cache

EXPOSE 8000

CMD ["uv", "run", "mcp-admin", "--host", "0.0.0.0", "--port", "8000"]