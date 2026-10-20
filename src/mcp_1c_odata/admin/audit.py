import sqlite3
from datetime import datetime
from pathlib import Path
from mcp_1c_odata.admin.config import ADMIN_DB_PATH


def _conn() -> sqlite3.Connection:
    Path(ADMIN_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(ADMIN_DB_PATH)


def init_audit():
    conn = _conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT,
                details TEXT,
                ip TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()


def log(actor: str, action: str, target: str | None = None,
        details: str | None = None, ip: str | None = None):
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO audit_log (ts, actor, action, target, details, ip) VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.now().isoformat(), actor, action, target, details, ip)
        )
        conn.commit()
    finally:
        conn.close()


def recent(limit: int = 100) -> list[dict]:
    conn = _conn()
    try:
        cur = conn.execute(
            "SELECT id, ts, actor, action, target, details, ip "
            "FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,)
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {"id": r[0], "ts": r[1], "actor": r[2], "action": r[3],
         "target": r[4], "details": r[5], "ip": r[6]}
        for r in rows
    ]
