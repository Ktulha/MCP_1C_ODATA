import duckdb
import pandas as pd
from datetime import datetime
from pathlib import Path
from mcp_1c_odata.config import config


class CacheStorage:
    def __init__(self):
        self.db_path = config.cache_path / "cache.duckdb"
        self._init_db()

    def _get_conn(self, read_only: bool = False):
        """Короткоживущее соединение — открывается и закрывается на каждую операцию"""
        return duckdb.connect(str(self.db_path), read_only=read_only)

    def _init_db(self):
        """Инициализация БД (только при первом запуске)"""
        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache_metadata (
                    entity_alias TEXT PRIMARY KEY,
                    last_updated TIMESTAMP,
                    row_count INTEGER
                )
            """)
        finally:
            conn.close()

    def save_entity(self, alias: str, df: pd.DataFrame):
        """Сохранение DataFrame в кэш"""
        parquet_path = self._get_parquet_path(alias)
        parquet_path.parent.mkdir(parents=True, exist_ok=True)

        # Сохраняем в Parquet
        df.to_parquet(parquet_path, engine='pyarrow', compression='snappy')

        # Обновляем метаданные
        conn = self._get_conn()
        try:
            now = datetime.now()
            conn.execute(
                "INSERT OR REPLACE INTO cache_metadata (entity_alias, last_updated, row_count) VALUES (?, ?, ?)",
                (alias, now, len(df))
            )
        finally:
            conn.close()

    def load_entity(self, alias: str) -> pd.DataFrame | None:
        """Загрузка DataFrame из кэша"""
        parquet_path = self._get_parquet_path(alias)
        if not parquet_path.exists():
            return None

        try:
            return pd.read_parquet(parquet_path)
        except Exception as e:
            print(f"Ошибка загрузки кэша {alias}: {e}")
            return None

    def get_last_updated(self, alias: str) -> datetime | None:
        """Получение времени последнего обновления"""
        conn = self._get_conn(read_only=True)
        try:
            result = conn.execute(
                "SELECT last_updated FROM cache_metadata WHERE entity_alias = ?",
                (alias,)
            ).fetchone()
            return result[0] if result else None
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
            return result[0] if result else 0
        except Exception:
            return 0
        finally:
            conn.close()

    def _get_parquet_path(self, alias: str) -> Path:
        return config.cache_path / f"{alias}.parquet"

    def list_cached_entities(self) -> list[dict]:
        """Список всех закэшированных сущностей"""
        conn = self._get_conn(read_only=True)
        try:
            results = conn.execute(
                "SELECT entity_alias, last_updated, row_count FROM cache_metadata"
            ).fetchall()
            return [
                {
                    "alias": r[0],
                    "last_updated": r[1],
                    "row_count": r[2]
                }
                for r in results
            ]
        except Exception:
            return []
        finally:
            conn.close()


cache_storage = CacheStorage()
