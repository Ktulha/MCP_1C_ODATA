import sqlite3
import json
from datetime import datetime
from pathlib import Path
from mcp_1c_odata.config import config


def _conn() -> sqlite3.Connection:
    db_path = Path(config.logs_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path)


def init_logs():
    conn = _conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                token_name TEXT,
                role TEXT,
                tool TEXT NOT NULL,
                entity TEXT,
                arguments TEXT,
                status TEXT NOT NULL,
                result_summary TEXT,
                duration_ms INTEGER,
                error TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_llm_logs_ts ON llm_logs(ts DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_llm_logs_tool ON llm_logs(tool)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_llm_logs_token ON llm_logs(token_name)
        """)
        conn.commit()
    finally:
        conn.close()


def log_request(
    token_name: str | None,
    role: str | None,
    tool: str,
    entity: str | None,
    arguments: dict,
    status: str,  # 'ok' | 'error'
    result_summary: str | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
):
    conn = _conn()
    try:
        conn.execute(
            """INSERT INTO llm_logs 
               (ts, token_name, role, tool, entity, arguments, status, 
                result_summary, duration_ms, error) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                token_name,
                role,
                tool,
                entity,
                json.dumps(arguments, ensure_ascii=False, default=str),
                status,
                result_summary,
                duration_ms,
                error,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def recent_logs(limit: int = 100, tool: str | None = None, token_name: str | None = None) -> list[dict]:
    conn = _conn()
    try:
        query = "SELECT id, ts, token_name, role, tool, entity, arguments, status, result_summary, duration_ms, error FROM llm_logs WHERE 1=1"
        params: list = []
        if tool:
            query += " AND tool = ?"
            params.append(tool)
        if token_name:
            query += " AND token_name = ?"
            params.append(token_name)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        cur = conn.execute(query, params)
        rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "id": r[0],
            "ts": r[1],
            "token_name": r[2],
            "role": r[3],
            "tool": r[4],
            "entity": r[5],
            "arguments": r[6],
            "status": r[7],
            "result_summary": r[8],
            "duration_ms": r[9],
            "error": r[10],
        }
        for r in rows
    ]


def stats(days: int = 7) -> dict:
    """Статистика за последние N дней"""
    conn = _conn()
    try:
        cur = conn.execute(
            """SELECT tool, status, COUNT(*) as cnt, AVG(duration_ms) as avg_ms
               FROM llm_logs
               WHERE ts >= datetime('now', ?)
               GROUP BY tool, status""",
            (f"-{days} days",),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    by_tool: dict[str, dict] = {}
    for tool, status, cnt, avg_ms in rows:
        if tool not in by_tool:
            by_tool[tool] = {"ok": 0, "error": 0, "total": 0, "avg_ms": 0}
        by_tool[tool][status] = cnt
        by_tool[tool]["total"] += cnt
        by_tool[tool]["avg_ms"] = round(avg_ms or 0)
    return {"days": days, "by_tool": by_tool}


def cleanup(older_than_days: int = 30) -> int:
    conn = _conn()
    try:
        cur = conn.execute(
            "DELETE FROM llm_logs WHERE ts < datetime('now', ?)",
            (f"-{older_than_days} days",),
        )
        conn.commit()
        deleted = cur.rowcount
    finally:
        conn.close()
    return deleted
