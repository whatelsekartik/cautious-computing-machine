"""SQLite access layer with a tiny forward-only migration runner."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import get_paths

_lock = threading.RLock()
_connection: sqlite3.Connection | None = None
_db_path: Path | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Return a process-wide SQLite connection (FastAPI is single-process)."""
    global _connection, _db_path
    target = path or get_paths().db
    with _lock:
        if _connection is not None and _db_path == target:
            return _connection
        if _connection is not None:
            _connection.close()
        conn = sqlite3.connect(target, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        _connection, _db_path = conn, target
        return conn


def db() -> sqlite3.Connection:
    return connect()


def init_db(path: Path | None = None) -> list[int]:
    """Apply pending migrations. Idempotent and safe to call repeatedly."""
    conn = connect(path)
    migrations_dir = Path(__file__).resolve().parent / "migrations"
    with _lock:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        conn.commit()
        applied = {
            row["version"] for row in conn.execute("SELECT version FROM schema_migrations")
        }
        newly: list[int] = []
        for file in sorted(migrations_dir.glob("*.sql")):
            try:
                version = int(file.name.split("_", 1)[0])
            except ValueError:
                continue
            if version in applied:
                continue
            script = file.read_text(encoding="utf-8")
            conn.executescript(script)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, utc_now()),
            )
            conn.commit()
            newly.append(version)
        return newly


# --- small helpers -------------------------------------------------------


def query(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    with _lock:
        return list(db().execute(sql, tuple(params)))


def query_one(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    with _lock:
        return db().execute(sql, tuple(params)).fetchone()


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    with _lock:
        cur = db().execute(sql, tuple(params))
        db().commit()
        return cur


def executescript(script: str) -> None:
    with _lock:
        db().executescript(script)
        db().commit()


def audit(action: str, detail: dict[str, Any] | str | None = None, actor: str = "local") -> None:
    if isinstance(detail, dict):
        detail = json.dumps(detail, default=str)
    execute(
        "INSERT INTO audit_log (created_at, actor, action, detail) VALUES (?, ?, ?, ?)",
        (utc_now(), actor, action, detail),
    )


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None
