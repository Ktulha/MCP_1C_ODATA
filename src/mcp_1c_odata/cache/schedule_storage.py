import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger("mcp-1c-odata.schedule-storage")

DB_PATH = Path("data/schedule.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_schedule_db():
    """Инициализация таблицы заданий"""
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schedule_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                cron_expr TEXT NOT NULL,
                job_type TEXT NOT NULL CHECK(job_type IN ('full', 'incremental')),
                entities TEXT,  -- JSON-массив алиасов или null для "all"
                enabled INTEGER NOT NULL DEFAULT 1,
                last_run TEXT,
                last_status TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
        logger.info(f"✓ База расписания инициализирована: {DB_PATH}")
    finally:
        conn.close()


def list_jobs() -> list[dict]:
    """Получить все задания"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM schedule_jobs ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_job(job_id: int) -> Optional[dict]:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM schedule_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_job(name: str, cron_expr: str, job_type: str, entities: Optional[str], enabled: bool = True) -> int:
    """Создать задание. Возвращает ID."""
    now = datetime.now().isoformat()
    conn = _get_conn()
    try:
        cursor = conn.execute(
            """INSERT INTO schedule_jobs 
               (name, cron_expr, job_type, entities, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, cron_expr, job_type, entities, int(enabled), now, now)
        )
        conn.commit()

        # cursor.lastrowid имеет тип int | None, но после успешного INSERT всегда есть
        if cursor.lastrowid is None:
            raise RuntimeError("Не удалось получить ID созданного задания")

        return int(cursor.lastrowid)
    finally:
        conn.close()


def update_job(job_id: int, **kwargs) -> bool:
    """Обновить поля задания"""
    allowed = {"name", "cron_expr", "job_type", "entities", "enabled"}
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        return False

    fields["updated_at"] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
    values = list(fields.values()) + [job_id]

    conn = _get_conn()
    try:
        conn.execute(
            f"UPDATE schedule_jobs SET {set_clause} WHERE id = ?", values)
        conn.commit()
        return True
    finally:
        conn.close()


def delete_job(job_id: int) -> bool:
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "DELETE FROM schedule_jobs WHERE id = ?", (job_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def mark_run(job_id: int, status: str):
    """Отметить выполнение задания"""
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE schedule_jobs SET last_run = ?, last_status = ? WHERE id = ?",
            (datetime.now().isoformat(), status, job_id)
        )
        conn.commit()
    finally:
        conn.close()


def toggle_job(job_id: int, enabled: bool) -> bool:
    return update_job(job_id, enabled=enabled)
