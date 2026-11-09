"""Database facade: SQLite for standalone installs, PostgreSQL for cloud.

The rest of the application talks to this module only. It exposes the same
small surface it always has (``query``, ``query_one``, ``execute``,
``executescript``, ``audit``, ``init_db``) and selects a backend from the
environment:

* ``CIVICWATCH_DB_BACKEND=sqlite|postgres`` — explicit choice, or
* ``DATABASE_URL`` / ``SUPABASE_DB_URL`` starting with ``postgres://`` or
  ``postgresql://`` — cloud mode, or
* otherwise SQLite at ``<data>/civicwatch.db`` (the macOS launchers never set
  either variable, so local behaviour is unchanged).

Rows come back as plain ``dict`` for both SQLite and PostgreSQL; both support
``row["column"]`` and ``row.get(...)``, which is all the service
layer uses.
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable

from .config import get_paths
from .db_common import utc_now

BACKENDS = ("sqlite", "postgres")


def database_url() -> str | None:
    for key in ("DATABASE_URL", "SUPABASE_DB_URL", "POSTGRES_URL", "CIVICWATCH_DATABASE_URL"):
        value = os.environ.get(key)
        if value and value.strip():
            return value.strip()
    return None


def backend_name() -> str:
    """Resolve the active backend without opening a connection."""
    explicit = (os.environ.get("CIVICWATCH_DB_BACKEND") or "").strip().lower()
    if explicit:
        if explicit not in BACKENDS:
            raise ValueError(
                f"CIVICWATCH_DB_BACKEND must be one of {', '.join(BACKENDS)} (got {explicit!r})"
            )
        return explicit
    url = database_url()
    if url and url.split(":", 1)[0] in {"postgres", "postgresql"}:
        return "postgres"
    return "sqlite"


_backend = None
_backend_key: tuple[str, str | None] | None = None


def _get_backend():
    """Return the active backend object, rebuilding it if configuration changed."""
    global _backend, _backend_key
    name = backend_name()
    key = (name, database_url() if name == "postgres" else None)
    if _backend is None or _backend_key != key:
        if _backend is not None:
            try:
                _backend.close()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
        if name == "postgres":
            from .db_postgres import PostgresBackend

            _backend = PostgresBackend()
        else:
            from .db_sqlite import SqliteBackend

            _backend = SqliteBackend()
        _backend_key = key
    return _backend


def reset_connections() -> None:
    """Close pooled connections (used by tests and after config changes)."""
    global _backend, _backend_key
    if _backend is not None:
        try:
            _backend.close()
        except Exception:  # pragma: no cover
            pass
    _backend = None
    _backend_key = None


# --- connection / migration ------------------------------------------------

def connect(path=None):
    """Open (or reuse) a connection. ``path`` only applies to SQLite."""
    return _get_backend().connect(path)


def db():
    return connect()


def init_db(path=None) -> list[int]:
    """Apply pending migrations for the active backend. Idempotent."""
    return _get_backend().init_db(path)


def executescript(script: str) -> None:
    _get_backend().executescript(script)


# --- helpers ---------------------------------------------------------------

def query(sql: str, params: Iterable[Any] = ()) -> list[Any]:
    return _get_backend().query(sql, params)


def query_one(sql: str, params: Iterable[Any] = ()):
    return _get_backend().query_one(sql, params)


def execute(sql: str, params: Iterable[Any] = ()):
    return _get_backend().execute(sql, params)


def audit(action: str, detail: dict[str, Any] | str | None = None, actor: str = "local") -> None:
    if isinstance(detail, dict):
        detail = json.dumps(detail, default=str)
    execute(
        "INSERT INTO audit_log (created_at, actor, action, detail) VALUES (?, ?, ?, ?)",
        (utc_now(), actor, action, detail),
    )


def row_to_dict(row) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def describe() -> dict[str, Any]:
    """Non-secret description of where data is stored (for diagnostics)."""
    name = backend_name()
    if name == "postgres":
        from .db_postgres import describe_url

        return {"backend": "postgres", "target": describe_url(database_url() or "")}
    paths = get_paths()
    return {"backend": "sqlite", "target": str(paths.db)}
