# MCP 1C OData — установка через Docker

## 🔍 Что это?

**MCP-сервер для работы с 1С (УНФ) через OData** — позволяет AI-ассистентам (Claude, Cherry Studio, Cursor и др.) читать справочники, документы, регистры и остатки из 1С в режиме **только чтение**.

### Возможности

✅ 5 инструментов для LLM:
- `list_entities` — список доступных сущностей
- `get_metadata` — структура полей сущности
- `fetch_and_process` — запрос данных с фильтрами и агрегациями
- `lookup` — текстовый поиск по справочникам
- `refresh_cache` — принудительное обновление кэша

✅ Ролевая модель: `guest`, `viewer`, `sales`, `purchases`, `warehouse`, `production`, `analyst`, `admin`

✅ Веб-админка (порт 8000): управление токенами, просмотр кэша, логи запросов от LLM, аудит

✅ Кэширование данных в Parquet + DuckDB

✅ Rate limiting и логирование всех запросов

---

## 📋 Требования

- [Docker](https://docs.docker.com/get-docker/) и [Docker Compose](https://docs.docker.com/compose/install/)
- Доступ к 1С через OData (проверить можно по ссылке: `http://ваш-сервер/база/odata/standard.odata`)
- Пользователь 1С с правами на чтение

---

## 🚀 Установка

### 1. Клонируем репозиторий

```bash
git clone https://github.com/Ktulha/mcp-1c-odata.git
cd mcp-1c-odata
```

### 2. Создаём `.env`

```bash
cp .env.example .env
```

Редактируем `.env`:

```env
# === Подключение к 1С ===
ODATA_BASE_URL=http://192.168.14.152/unf_14062023/odata/standard.odata
ODATA_USER=ваш_пользователь
ODATA_PASSWORD=ваш_пароль

# === Веб-админка ===
MCP_ADMIN_PASSWORD=пароль_для_входа_в_админку

# === Пути к БД (не менять!) ===
MCP_AUTH_DB=data/auth.db
MCP_LOGS_DB=data/logs.db
MCP_ADMIN_DB=data/admin.db
```

### 3. Создаём директории для данных

```bash
mkdir -p data cache
```

### 4. Проверяем `config.yaml`

Убедитесь, что в `config.yaml` корректный `allowlist` с сущностями из вашей базы 1С. По умолчанию включены:
- Справочники: `nomenclature`, `variants`, `specifications`, `warehouses`, `price_types` и др.
- Регистры: `prices`, `barcodes`, `stock_balance`, `stock_moves`, `purchases_reg`
- Документы: `sales_doc`, `purchase_doc`, `assembly`, `transfer` и др.

### 5. Собираем и запускаем

```bash
# Сборка образа
docker compose build

# Запуск в фоне
docker compose up -d

# Проверка статуса
docker compose ps
```

Должно быть **2 контейнера**:
- `mcp-1c-odata-server` (MCP-сервер, stdio)
- `mcp-1c-odata-admin` (веб-админка, порт 8000)

---

## 🌐 Веб-админка

Откройте браузер: **http://localhost:8000**

Введите пароль из `MCP_ADMIN_PASSWORD`.

**Разделы:**
- 🏠 Главная
- 🔑 Токены — создание/отзыв токенов для LLM
- 📦 Кэш — статус, обновление, экспорт в CSV
- 🎭 Роли — просмотр конфигурации ролей
- 📊 Логи LLM — все запросы от AI-клиентов
- ⚡ Rate Limits — лимиты и счётчики
- 📝 Аудит — журнал действий администратора

---

## 🤖 Подключение к LLM-клиенту

### Вариант 1: Локальный запуск через `uv` (без Docker)

Для Claude Desktop, Cherry Studio, Cursor удобнее использовать локальный запуск (не через Docker):

```bash
# Установка зависимостей
uv sync

# Проверка
uv run mcp-1c-odata
```

Конфигурация для **Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "1c-odata": {
      "command": "uv",
      "args": ["run", "mcp-1c-odata"],
      "cwd": "C:/путь/к/mcp-1c-odata",
      "env": {
        "MCP_CONFIG_PATH": "config.yaml",
        "MCP_AUTH_DB": "data/auth.db"
      }
    }
  }
}
```

Конфигурация для **Cherry Studio** (через MCP-настройки):

```json
{
  "command": "uv",
  "args": ["run", "mcp-1c-odata"],
  "cwd": "C:/путь/к/mcp-1c-odata"
}
```

### Вариант 2: Через Docker

```json
{
  "mcpServers": {
    "1c-odata": {
      "command": "docker",
      "args": ["run", "--rm", "-i",
        "-v", "${PWD}/config.yaml:/app/config.yaml:ro",
        "-v", "${PWD}/.env:/app/.env:ro",
        "-v", "${PWD}/data:/app/data",
        "-v", "${PWD}/cache:/app/cache",
        "mcp-1c-odata", "uv", "run", "mcp-1c-odata"]
    }
  }
}
```

⚠️ На Windows пути в volumes указывайте в формате: `"C:\\Users\\...\\mcp-1c-odata\\data:/app/data"`

---

## 🔑 Создание токена для LLM

```bash
# Через админку: раздел "Токены" → "Создать новый токен"

# Или через CLI в контейнере:
docker compose exec admin uv run mcp-auth create --name llm_token --role analyst --days 30
```

Токен передаётся в промпте или системных настройках клиента. **Показывает только один раз!**

---

## 📊 Проверка работоспособности

```bash
# Проверка списка сущностей
docker compose exec admin uv run python -c "
import asyncio
from mcp_1c_odata.tools import list_entities
from mcp_1c_odata.auth.db import init_db, create_token
init_db()
token = create_token('test', '', role='admin', days=1)
result = asyncio.run(list_entities.execute({'auth_token': token}))
for e in result.get('entities', []):
    print(f\"  {e['alias']:<20} {e['name']}\")
"

# Статус кэша
docker compose exec admin uv run mcp-cache status
```

---

## 🔄 Обновление проекта

```bash
cd mcp-1c-odata

# Получить последние изменения
git pull origin main

# Пересобрать и перезапустить
docker compose build --no-cache
docker compose up -d
```

Данные в `data/` и `cache/` сохраняются.

---

## 🗂 Структура проекта

```
mcp-1c-odata/
├── config.yaml          # Конфигурация (сущности, роли, лимиты)
├── .env                 # Секреты (пароли)
├── data/                # Базы данных (создаётся автоматически)
│   ├── auth.db          # Токены и счётчики
│   ├── logs.db          # Логи запросов от LLM
│   └── admin.db         # Аудит админки
├── cache/               # Кэш данных из 1С
│   ├── cache.duckdb     # Метаданные
│   └── *.parquet        # Данные сущностей
├── docker-compose.yml
├── Dockerfile
└── src/mcp_1c_odata/
```

---

## 🛠 Управление

```bash
# Логи в реальном времени
docker compose logs -f admin
docker compose logs -f mcp-server

# Остановка
docker compose down

# Полная очистка (⚠️ удалит данные!)
docker compose down -v
rm -rf data/ cache/

# Ручное обновление кэша
docker compose exec admin uv run mcp-cache refresh --mode full
docker compose exec admin uv run mcp-cache refresh --entity nomenclature --mode incremental
```

---

## 🚨 Траблшутинг

### «Cannot open file cache.duckdb»
DuckDB не поддерживает одновременный доступ из нескольких процессов. Убедитесь, что **не запущены одновременно** локальный сервер и контейнеры с доступом к одному каталогу `cache/`.

### «No such table: tokens»
Не выполнена инициализация БД. Перезапустите контейнеры:
```bash
docker compose restart
```

### Админка не открывается на порту 8000
Проверьте, что порт не занят другим приложением:
```bash
# Настройка другого порта в docker-compose.yml:
ports:
  - "9000:8000"
```

### Ошибка подключения к 1С
1. Проверьте `ODATA_BASE_URL` в `.env` — должен открываться в браузере
2. Проверьте логин/пароль пользователя 1С
3. Убедитесь, что у пользователя есть права на чтение нужных объектов

### В логах `Rate limit exceeded`
Увеличьте лимиты в `config.yaml` → `rate_limits.limits_by_role`.

---

## 📝 Лицензия

Проект создан для внутреннего использования.

**Автор:** Ktulha (9145140@gmail.com)

```bash
# 1. Клонировать
git clone https://github.com/Ktulha/mcp-1c-odata.git
cd mcp-1c-odata

# 2. Настроить окружение
cp .env.example .env
nano .env

# 3. Создать папки для данных
mkdir -p data cache

# 4. Запустить
docker compose up -d

# 5. Открыть админку
# http://localhost:8000