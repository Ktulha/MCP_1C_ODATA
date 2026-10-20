import sqlite3
import time
from pathlib import Path

from mcp_1c_odata.config import config


def _conn() -> sqlite3.Connection:
    db_path = Path(config.auth_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path, timeout=5)


def init_rate_limit():
    """Создание таблицы счётчиков (в той же БД, что и токены)"""
    conn = _conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rate_limit_counters (
                token_name TEXT NOT NULL,
                window_start INTEGER NOT NULL,
                request_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (token_name, window_start)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rl_window 
            ON rate_limit_counters(window_start)
        """)
        conn.commit()
    finally:
        conn.close()


def _current_window(window_sec: int) -> int:
    """Номер текущего окна (целое число секунд с эпохи, кратное window_sec)"""
    return int(time.time()) // window_sec * window_sec


def increment(token_name: str, window_sec: int) -> tuple[int, int]:
    """Инкремент счётчика запросов для токена.

    Returns:
        (current_count, window_remaining_sec)
    """
    window_start = _current_window(window_sec)
    conn = _conn()
    try:
        # Атомарный UPSERT
        conn.execute(
            """INSERT INTO rate_limit_counters (token_name, window_start, request_count)
               VALUES (?, ?, 1)
               ON CONFLICT(token_name, window_start) 
               DO UPDATE SET request_count = request_count + 1""",
            (token_name, window_start)
        )
        conn.commit()

        cur = conn.execute(
            "SELECT request_count FROM rate_limit_counters WHERE token_name = ? AND window_start = ?",
            (token_name, window_start)
        )
        row = cur.fetchone()
        count = row[0] if row else 1

        remaining = window_sec - (int(time.time()) - window_start)
        return count, max(1, remaining)
    finally:
        conn.close()


def get_counters(limit: int = 100) -> list[dict]:
    """Получение активных счётчиков (для админки)"""
    window_start = _current_window(60)  # текущее минутное окно
    conn = _conn()
    try:
        cur = conn.execute(
            """SELECT token_name, window_start, request_count 
               FROM rate_limit_counters 
               WHERE window_start = ?
               ORDER BY request_count DESC 
               LIMIT ?""",
            (window_start, limit)
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "token_name": r[0],
            "window_start": r[1],
            "request_count": r[2],
            "remaining_sec": max(1, 60 - (int(time.time()) - r[1]))
        }
        for r in rows
    ]


def reset(token_name: str) -> int:
    """Сброс счётчика для токена. Возвращает количество удалённых записей."""
    conn = _conn()
    try:
        cur = conn.execute(
            "DELETE FROM rate_limit_counters WHERE token_name = ?",
            (token_name,)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def cleanup(older_than_windows: int = 10) -> int:
    """Удаление старых записей счётчиков"""
    cutoff = _current_window(60) - older_than_windows * 60
    conn = _conn()
    try:
        cur = conn.execute(
            "DELETE FROM rate_limit_counters WHERE window_start < ?",
            (cutoff,)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
