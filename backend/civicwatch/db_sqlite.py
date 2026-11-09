"""SQLite backend — the standalone macOS installation.

Behaviour is identical to the original single-backend implementation: one
process-wide connection in WAL mode, a lock around writes, and forward-only
migrations read from ``migrations/sqlite``.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from .config import get_paths
from .db_common import migration_files, utc_now

_lock = threading.RLock()
_connection: sqlite3.Connection | None = None
_db_path: Path | None = None


class SqliteBackend:
    name = "sqlite"

    def connect(self, path: Path | None = None) -> sqlite3.Connection:
        global _connection, _db_path
        target = Path(path) if path is not None else get_paths().db
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

    def close(self) -> None:
        global _connection, _db_path
        with _lock:
            if _connection is not None:
                _connection.close()
            _connection, _db_path = None, None

    def init_db(self, path: Path | None = None) -> list[int]:
        conn = self.connect(path)
        with _lock:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            conn.commit()
            applied = {
                row["version"]
                for row in conn.execute("SELECT version FROM schema_migrations")
            }
            newly: list[int] = []
            for version, file in migration_files("sqlite"):
                if version in applied:
                    continue
                conn.executescript(file.read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, utc_now()),
                )
                conn.commit()
                newly.append(version)
            return newly

    # -- helpers ---------------------------------------------------------
    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        # Plain dicts keep the two backends interchangeable: callers may rely on
        # ``row.get(...)``, which sqlite3.Row does not implement.
        with _lock:
            return [dict(row) for row in self.connect().execute(sql, tuple(params))]

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        with _lock:
            row = self.connect().execute(sql, tuple(params)).fetchone()
        return dict(row) if row is not None else None

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with _lock:
            conn = self.connect()
            cur = conn.execute(sql, tuple(params))
            conn.commit()
            return cur

    def executescript(self, script: str) -> None:
        with _lock:
            conn = self.connect()
            conn.executescript(script)
            conn.commit()
