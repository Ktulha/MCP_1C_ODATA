import sqlite3
import bcrypt
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from mcp_1c_odata.config import config


def _get_conn() -> sqlite3.Connection:
    db_path = Path(config.auth_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path)


def init_db():
    conn = _get_conn()
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                token_hash TEXT NOT NULL,
                scope TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'viewer',
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        ''')
        # Миграция: добавляем колонку role, если её нет
        cursor = conn.execute("PRAGMA table_info(tokens)")
        columns = [row[1] for row in cursor.fetchall()]
        if "role" not in columns:
            conn.execute(
                "ALTER TABLE tokens ADD COLUMN role TEXT NOT NULL DEFAULT 'viewer'")
        conn.commit()
    finally:
        conn.close()


def create_token(name: str, scope: str, role: str = "viewer", days: int = 30) -> str:
    raw_token = secrets.token_urlsafe(32)
    token_hash = bcrypt.hashpw(raw_token.encode(
        'utf-8'), bcrypt.gensalt()).decode('utf-8')
    now = datetime.now()
    expires_at = (now + timedelta(days=days)).isoformat()
    created_at = now.isoformat()

    conn = _get_conn()
    try:
        conn.execute(
            'INSERT INTO tokens (name, token_hash, scope, role, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?)',
            (name, token_hash, scope, role, expires_at, created_at)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError(f"Token with name '{name}' already exists")
    finally:
        conn.close()

    return raw_token


def verify_token(token: str) -> dict | None:
    conn = _get_conn()
    try:
        cursor = conn.execute(
            'SELECT id, name, token_hash, scope, role, expires_at FROM tokens')
        rows = cursor.fetchall()
    finally:
        conn.close()

    now = datetime.now()
    for row in rows:
        token_id, name, token_hash, scope, role, expires_at = row
        if datetime.fromisoformat(expires_at) < now:
            continue
        if bcrypt.checkpw(token.encode('utf-8'), token_hash.encode('utf-8')):
            return {
                "id": token_id,
                "name": name,
                "role": role,
                "scope": [s.strip() for s in scope.split(",") if s.strip()],
                "expires_at": expires_at
            }
    return None


def list_tokens() -> list[dict]:
    conn = _get_conn()
    try:
        cursor = conn.execute(
            'SELECT id, name, scope, role, expires_at, created_at FROM tokens')
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r[0], "name": r[1], "scope": r[2], "role": r[3],
            "expires_at": r[4], "created_at": r[5],
            "active": datetime.fromisoformat(r[4]) > datetime.now()
        }
        for r in rows
    ]


def revoke_token(token_id: int) -> bool:
    conn = _get_conn()
    try:
        cursor = conn.execute('DELETE FROM tokens WHERE id = ?', (token_id,))
        conn.commit()
        deleted = cursor.rowcount > 0
    finally:
        conn.close()
    return deleted


def cleanup_expired() -> int:
    conn = _get_conn()
    try:
        now = datetime.now().isoformat()
        cursor = conn.execute(
            'DELETE FROM tokens WHERE expires_at < ?', (now,))
        conn.commit()
        deleted = cursor.rowcount
    finally:
        conn.close()
    return deleted
