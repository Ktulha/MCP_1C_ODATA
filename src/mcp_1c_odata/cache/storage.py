import duckdb
import logging
import pandas as pd
from datetime import datetime
from pathlib import Path
from mcp_1c_odata.config import config

logger = logging.getLogger("mcp-1c-odata.cache")


class CacheStorage:
    def __init__(self):
        self.db_path = config.cache_path / "cache.duckdb"
        self._init_db()

    def _get_conn(self, read_only: bool = False):
        """Короткоживущее соединение — открывается и закрывается на каждую операцию"""
        return duckdb.connect(str(self.db_path), read_only=read_only)

    def _init_db(self):
        """Инициализация БД и миграция схемы (добавление is_view, если его нет)"""
        conn = self._get_conn()
        try:
            # 1. Создаем базовую таблицу, если её нет
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache_metadata (
                    entity_alias TEXT PRIMARY KEY,
                    last_updated TIMESTAMP,
                    row_count INTEGER
                )
            """)

            # 2. Миграция: добавляем колонку is_view, если она отсутствует
            try:
                conn.execute("""
                    ALTER TABLE cache_metadata 
                    ADD COLUMN is_view BOOLEAN DEFAULT FALSE
                """)
                logger.info("Добавлена колонка 'is_view' в cache_metadata")
            except Exception:
                # Если колонка уже есть, DuckDB выбросит ошибку, которую мы безопасно игнорируем
                pass
        finally:
            conn.close()

    # ============================================================
    # БАЗОВЫЕ ОПЕРАЦИИ С PARQUET
    # ============================================================

    def save_entity(self, alias: str, df: pd.DataFrame, is_view: bool = False):
        """Сохранение DataFrame в кэш (или метаданные для view)"""
        if not is_view:
            parquet_path = self._get_parquet_path(alias)
            parquet_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(parquet_path, engine='pyarrow', compression='snappy')

        # Обновляем метаданные
        conn = self._get_conn()
        try:
            now = datetime.now()
            conn.execute(
                "INSERT OR REPLACE INTO cache_metadata (entity_alias, last_updated, row_count, is_view) "
                "VALUES (?, ?, ?, ?)",
                (alias, now, len(df), is_view)
            )
        finally:
            conn.close()

    def load_entity(self, alias: str) -> pd.DataFrame | None:
        """Загрузка DataFrame из кэша.

        Для обычных сущностей читает parquet-файл.
        Для view выполняет SELECT * FROM view_name.
        """
        # Проверяем, является ли сущность view
        conn = self._get_conn(read_only=True)
        try:
            result = conn.execute(
                "SELECT is_view FROM cache_metadata WHERE entity_alias = ?",
                (alias,)
            ).fetchone()
            is_view = bool(result[0]) if result is not None else False
        except Exception:
            is_view = False
        finally:
            conn.close()

        if is_view:
            return self._load_view(alias)
        return self._load_parquet(alias)

    def _load_parquet(self, alias: str) -> pd.DataFrame | None:
        """Загрузка из parquet-файла"""
        parquet_path = self._get_parquet_path(alias)
        if not parquet_path.exists():
            return None
        try:
            return pd.read_parquet(parquet_path)
        except Exception as e:
            logger.error(f"Ошибка загрузки parquet {alias}: {e}")
            return None

    def _load_view(self, alias: str) -> pd.DataFrame | None:
        """Загрузка из DuckDB view"""
        conn = self._get_conn(read_only=True)
        try:
            # Проверяем существование view
            result = conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_name = ? AND table_type = 'VIEW'",
                (alias,)
            ).fetchone()
            if result is None or result[0] == 0:
                logger.warning(f"View '{alias}' не существует в DuckDB")
                return None
            df = conn.execute(f"SELECT * FROM {alias}").fetchdf()
            return df
        except Exception as e:
            logger.error(f"Ошибка загрузки view {alias}: {e}")
            return None
        finally:
            conn.close()

    def _get_parquet_path(self, alias: str) -> Path:
        return config.cache_path / f"{alias}.parquet"

    # ============================================================
    # СОЗДАНИЕ VIEW (скомпилированные таблицы)
    # ============================================================

    def create_views(self, views_config: list[dict]) -> dict[str, bool]:
        """Создание/обновление DuckDB views на основе конфигурации."""
        results: dict[str, bool] = {}
        cache_path_abs = str(config.cache_path.resolve())

        for view in views_config:
            alias = view.get("alias")
            view_sql = view.get("view_sql")

            if not alias or not view_sql:
                logger.warning(
                    f"View '{alias}' пропущен: отсутствует alias или view_sql")
                results[alias or "?"] = False
                continue

            # Подставляем абсолютный путь к кэшу вместо относительного 'cache/'
            normalized_sql = view_sql.replace("{CACHE_PATH}", cache_path_abs)

            try:
                conn = self._get_conn()
                try:
                    # Выполняем SQL
                    conn.execute(normalized_sql)

                    # Проверяем, что view создан и возвращает данные
                    count_result = conn.execute(
                        f"SELECT COUNT(*) FROM {alias}").fetchone()
                    row_count = count_result[0] if count_result is not None else 0

                    # Сохраняем метаданные view
                    self.save_entity(alias, pd.DataFrame(), is_view=True)
                    # Обновляем row_count вручную (save_entity записал 0 для пустого df)
                    conn.execute(
                        "UPDATE cache_metadata SET row_count = ? WHERE entity_alias = ?",
                        (row_count, alias)
                    )
                    conn.commit()

                    logger.info(
                        f"✓ View '{alias}' создан успешно, строк: {row_count}")
                    results[alias] = True
                finally:
                    conn.close()
            except Exception as e:
                logger.error(f"✗ Ошибка создания view '{alias}': {e}")
                results[alias] = False

        return results

    def list_views(self) -> list[dict]:
        """Список всех созданных view"""
        conn = self._get_conn(read_only=True)
        try:
            results = conn.execute(
                "SELECT entity_alias, last_updated, row_count "
                "FROM cache_metadata WHERE is_view = TRUE"
            ).fetchall()
            return [
                {"alias": r[0], "last_updated": r[1], "row_count": r[2]}
                for r in results
            ]
        except Exception:
            return []
        finally:
            conn.close()

    # ============================================================
    # МЕТАДАННЫЕ И СТАТУС
    # ============================================================

    def get_last_updated(self, alias: str) -> datetime | None:
        """Получение времени последнего обновления"""
        conn = self._get_conn(read_only=True)
        try:
            result = conn.execute(
                "SELECT last_updated FROM cache_metadata WHERE entity_alias = ?",
                (alias,)
            ).fetchone()
            if result is None:
                return None
            return result[0]
        except Exception:
            return None
        finally:
            conn.close()

    def get_row_count(self, alias: str) -> int:
        """Получение количества записей"""
        conn = self._get_conn(read_only=True)
        try:
            result = conn.execute(
                "SELECT row_count FROM cache_metadata WHERE entity_alias = ?",
                (alias,)
            ).fetchone()
            if result is None:
                return 0
            return result[0]
        except Exception:
            return 0
        finally:
            conn.close()

    def list_cached_entities(self) -> list[dict]:
        """Список всех закэшированных сущностей (включая view)"""
        conn = self._get_conn(read_only=True)
        try:
            results = conn.execute(
                "SELECT entity_alias, last_updated, row_count, is_view FROM cache_metadata"
            ).fetchall()
            return [
                {
                    "alias": r[0],
                    "last_updated": r[1],
                    "row_count": r[2],
                    "is_view": bool(r[3])
                }
                for r in results
            ]
        except Exception:
            return []
        finally:
            conn.close()


cache_storage = CacheStorage()
