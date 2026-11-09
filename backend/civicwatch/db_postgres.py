"""PostgreSQL backend for the Vercel/Supabase deployment.

Uses pg8000 — a pure-Python driver, so the serverless bundle needs no compiled
extensions and works on every Vercel Python runtime. Connections are kept per
thread (a warm Lambda reuses one), and every write commits immediately, which
keeps the behaviour of the service layer identical to SQLite.

Configuration comes from a standard URL:

    DATABASE_URL=postgresql://user:password@host:5432/dbname?sslmode=require

Supabase's pooled connection string (port 6543) works as-is; SSL is enabled
automatically unless ``sslmode=disable`` is passed, which is only needed for a
local PostgreSQL server.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urlsplit

from .db_common import migration_files, split_statements, translate_sql, utc_now

log = logging.getLogger("civicwatch.postgres")

_local = threading.local()
_lock = threading.RLock()
_config_lock = threading.Lock()


class PostgresUnavailable(RuntimeError):
    """Raised when the driver is missing or the URL cannot be parsed."""


def parse_url(url: str) -> dict[str, Any]:
    if not url:
        raise PostgresUnavailable("No database URL configured")
    parts = urlsplit(url)
    if parts.scheme not in {"postgres", "postgresql"}:
        raise PostgresUnavailable(f"Unsupported database scheme: {parts.scheme!r}")
    query = parse_qs(parts.query)
    host = parts.hostname or ""
    unix_sock = None
    if not host:
        socket_dir = (query.get("host") or [""])[0]
        if not socket_dir:
            raise PostgresUnavailable("Database URL has no host")
        import glob
        import os

        if os.path.basename(socket_dir).startswith(".s.PGSQL."):
            unix_sock = socket_dir
        else:
            candidates = sorted(glob.glob(os.path.join(socket_dir, ".s.PGSQL.*")))
            candidates = [c for c in candidates if not c.endswith(".lock")]
            if not candidates:
                raise PostgresUnavailable(f"No PostgreSQL socket in {socket_dir}")
            unix_sock = candidates[0]
        host = "localhost"
    database = (parts.path or "/").lstrip("/") or "postgres"
    sslmode = (query.get("sslmode") or ["disable" if unix_sock else "require"])[0].lower()
    return {
        "user": unquote(parts.username or "postgres"),
        "password": unquote(parts.password or ""),
        "host": host,
        "port": parts.port or 5432,
        "database": database,
        "unix_sock": unix_sock,
        "sslmode": sslmode,
    }


def describe_url(url: str) -> str:
    """Host/database only — never the password."""
    try:
        info = parse_url(url)
    except PostgresUnavailable:
        return "invalid database URL"
    if info["unix_sock"]:
        return f"postgres socket · {info['database']}"
    return f"postgres · {info['host']}:{info['port']}/{info['database']}"


def _connect(url: str):
    try:
        import pg8000.dbapi as pg
    except Exception as exc:  # pragma: no cover - dependency guard
        raise PostgresUnavailable(
            "pg8000 is not installed. Install the cloud requirements: "
            "pip install -r backend/requirements-cloud.txt"
        ) from exc

    info = parse_url(url)
    kwargs: dict[str, Any] = {
        "user": info["user"],
        "database": info["database"],
        "timeout": 15,
        "application_name": "civicwatch",
    }
    if info["password"]:
        kwargs["password"] = info["password"]
    if info["unix_sock"]:
        kwargs["unix_sock"] = info["unix_sock"]
    else:
        kwargs["host"] = info["host"]
        kwargs["port"] = int(info["port"])
        if info["sslmode"] not in {"disable", "allow", "prefer"}:
            import ssl

            context = ssl.create_default_context()
            if info["sslmode"] == "no-verify":
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            kwargs["ssl_context"] = context
    return pg.connect(**kwargs)


class PgCursor:
    """Cursor-like object backed by eagerly fetched dictionary rows."""

    def __init__(self, cursor, sql: str):
        self._sql = sql
        description = cursor.description
        self._rowcount = cursor.rowcount
        if not description:  # INSERT/UPDATE/DELETE without RETURNING
            self._rows = []
            return
        columns = [column[0] for column in description]
        fetched = cursor.fetchall() or []
        self._rows = [dict(zip(columns, row)) for row in fetched]

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)

    def __len__(self) -> int:
        return len(self._rows)

    @property
    def rowcount(self) -> int:
        return self._rowcount

    @property
    def lastrowid(self) -> None:
        return None  # sequences are managed by PostgreSQL


class PostgresBackend:
    name = "postgres"

    def __init__(self) -> None:
        self._url = None
        self._initialised = set()

    # -- connections -----------------------------------------------------
    def _url_now(self) -> str:
        from .db import database_url

        url = database_url()
        if not url:
            raise PostgresUnavailable(
                "CIVICWATCH_DB_BACKEND=postgres but DATABASE_URL is not set"
            )
        return url

    def connect(self, path=None):
        url = self._url_now()
        conn = getattr(_local, "conn", None)
        if conn is not None and getattr(_local, "url", None) == url:
            return conn
        if conn is not None:
            try:
                conn.close()
            except Exception:  # pragma: no cover
                pass
        conn = _connect(url)
        conn.autocommit = False
        _local.conn, _local.url = conn, url
        return conn

    def close(self) -> None:
        conn = getattr(_local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # pragma: no cover
                pass
        _local.conn, _local.url = None, None

    def _reset(self) -> None:
        """Drop a broken connection so the next call reconnects."""
        self.close()

    # -- migrations ------------------------------------------------------
    def init_db(self, path=None) -> list[int]:
        url = self._url_now()
        with _lock:
            conn = self.connect()
            try:
                cur = conn.cursor()
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations ("
                    "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                cur.execute("SELECT version FROM schema_migrations")
                applied = {row[0] for row in cur.fetchall() or []}
                conn.commit()
                newly: list[int] = []
                for version, file in migration_files("postgres"):
                    if version in applied:
                        continue
                    script = file.read_text(encoding="utf-8")
                    try:
                        for statement in split_statements(script):
                            cur.execute(statement)
                        cur.execute(
                            "INSERT INTO schema_migrations (version, applied_at) VALUES (%s, %s)",
                            (version, utc_now()),
                        )
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        log.exception("Migration %s failed", file.name)
                        raise
                    newly.append(version)
                if newly:
                    log.info("Applied PostgreSQL migrations: %s", newly)
                self._initialised.add(url)
                return newly
            finally:
                pass

    # -- helpers ---------------------------------------------------------
    def _run(self, sql: str, params: Iterable[Any] = (), *, commit: bool):
        translated = translate_sql(sql)
        if not translated.strip():
            return None
        with _lock:
            conn = self.connect()
            try:
                cur = conn.cursor()
                cur.execute(translated, tuple(params))
                wrapped = PgCursor(cur, sql)
                if commit:
                    conn.commit()
                return wrapped
            except Exception:
                try:
                    conn.rollback()
                except Exception:  # pragma: no cover
                    pass
                self._reset()
                raise

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        cur = self._run(sql, params, commit=True)  # commit also closes the txn
        return cur.fetchall() if cur else []

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        cur = self._run(sql, params, commit=True)
        return cur.fetchone() if cur else None

    def execute(self, sql: str, params: Iterable[Any] = ()) -> PgCursor | None:
        return self._run(sql, params, commit=True)

    def executescript(self, script: str) -> None:
        with _lock:
            conn = self.connect()
            cur = conn.cursor()
            try:
                for statement in split_statements(script):
                    cur.execute(statement)
                conn.commit()
            except Exception:
                conn.rollback()
                self._reset()
                raise

    # -- diagnostics -----------------------------------------------------
    def ping(self) -> dict[str, Any]:
        row = self.query_one("SELECT version() AS version, current_database() AS db")
        return {"ok": True, "database": (row or {}).get("db"), "version": (row or {}).get("version", "")[:80]}

    def table_names(self) -> list[str]:
        return [
            row["table_name"]
            for row in self.query(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        ]


def ensure_initialised() -> None:
    """Apply migrations once per process (used by the serverless entrypoint)."""
    with _config_lock:
        backend = PostgresBackend()
        backend.init_db()
