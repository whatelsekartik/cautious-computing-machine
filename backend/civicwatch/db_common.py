"""Dialect-neutral helpers shared by the SQLite and PostgreSQL backends."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

MIGRATIONS_ROOT = Path(__file__).resolve().parent / "migrations"


def migration_files(dialect: str) -> list[tuple[int, Path]]:
    """Ordered ``(version, path)`` pairs for a dialect.

    ``migrations/<dialect>/*.sql`` is the layout since cloud support was added;
    a flat ``migrations/*.sql`` directory is still honoured for older checkouts.
    """
    directory = MIGRATIONS_ROOT / dialect
    files = sorted(directory.glob("*.sql")) if directory.is_dir() else []
    if not files and dialect == "sqlite":
        files = sorted(MIGRATIONS_ROOT.glob("*.sql"))
    out: list[tuple[int, Path]] = []
    for file in files:
        try:
            version = int(file.name.split("_", 1)[0])
        except ValueError:  # pragma: no cover - malformed file name
            continue
        out.append((version, file))
    return out


def split_statements(script: str) -> Iterator[str]:
    """Split a SQL script on ``;`` while respecting quotes, comments and $$ blocks.

    Good enough for our DDL; avoids shipping a full SQL parser into Lambda.
    """
    buffer: list[str] = []
    i, length = 0, len(script)
    in_single = in_double = in_line_comment = in_block_comment = in_dollar = False
    dollar_tag = ""
    while i < length:
        char = script[i]
        pair = script[i : i + 2]
        if in_line_comment:
            buffer.append(char)
            if char == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            buffer.append(char)
            if pair == "*/":
                buffer.append("/")
                i += 2
                in_block_comment = False
                continue
            i += 1
            continue
        if in_dollar:
            if script.startswith(dollar_tag, i):
                buffer.append(dollar_tag)
                i += len(dollar_tag)
                in_dollar = False
                continue
            buffer.append(char)
            i += 1
            continue
        if in_single:
            buffer.append(char)
            if char == "'":
                if pair == "''":
                    buffer.append("'")
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        if in_double:
            buffer.append(char)
            if char == '"':
                in_double = False
            i += 1
            continue
        if pair == "--":
            in_line_comment = True
            buffer.append(pair)
            i += 2
            continue
        if pair == "/*":
            in_block_comment = True
            buffer.append(pair)
            i += 2
            continue
        if char == "'":
            in_single = True
            buffer.append(char)
            i += 1
            continue
        if char == '"':
            in_double = True
            buffer.append(char)
            i += 1
            continue
        if char == "$":
            # Dollar-quoted string: $tag$ ... $tag$
            end = script.find("$", i + 1)
            if end != -1:
                candidate = script[i : end + 1]
                if candidate.count("$") == 2 and all(
                    c.isalnum() or c == "_" or c == "$" for c in candidate
                ):
                    in_dollar = True
                    dollar_tag = candidate
                    buffer.append(candidate)
                    i = end + 1
                    continue
        if char == ";":
            statement = "".join(buffer).strip()
            if statement:
                yield statement
            buffer = []
            i += 1
            continue
        buffer.append(char)
        i += 1
    tail = "".join(buffer).strip()
    if tail:
        yield tail


def translate_sql(sql: str) -> str:
    """Convert SQLite-style SQL to PostgreSQL while respecting quoted text.

    * ``?`` placeholders become ``%s`` (pg8000's format paramstyle).
    * ``LIKE`` becomes ``ILIKE`` so incident search stays case-insensitive,
      matching SQLite's default behaviour for ASCII text.
    * SQLite-only ``PRAGMA`` statements are dropped.
    """
    if sql.lstrip().upper().startswith("PRAGMA"):
        return ""
    out: list[str] = []
    i, length = 0, len(sql)
    in_single = False
    while i < length:
        char = sql[i]
        if in_single:
            out.append(char)
            if char == "'":
                if sql[i : i + 2] == "''":
                    out.append("'")
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        if char == "'":
            in_single = True
            out.append(char)
            i += 1
            continue
        if char == "?":
            out.append("%s")
            i += 1
            continue
        if char in " \t\n(" and sql[i + 1 : i + 5].upper() == "LIKE" and sql[i + 5 : i + 6] in {" ", "\t", "\n", "("}:
            out.append(" ILIKE")
            i += 5
            continue
        out.append(char)
        i += 1
    return "".join(out)
