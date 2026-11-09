#!/usr/bin/env python3
"""Apply CivicWatch's PostgreSQL migrations to a Supabase/managed database.

    DATABASE_URL='postgresql://...' python tools/cloud_migrate.py --status
    DATABASE_URL='postgresql://...' python tools/cloud_migrate.py
    DATABASE_URL='postgresql://...' python tools/cloud_migrate.py --create-connector "Yard 1 Mac"

The same migrations also run automatically on the first Vercel request, so this
tool is for pre-provisioning, CI and headless setups. It never touches SQLite.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    parser = argparse.ArgumentParser(description="CivicWatch PostgreSQL migration helper")
    parser.add_argument("--status", action="store_true", help="show applied migrations and row counts")
    parser.add_argument("--create-connector", metavar="NAME", help="create a connector and print its token once")
    args = parser.parse_args()

    if not (os.environ.get("DATABASE_URL") or "").startswith(("postgres://", "postgresql://")):
        print("This tool manages the hosted PostgreSQL database.")
        print("Set DATABASE_URL to a PostgreSQL URL, e.g.")
        print("  postgresql://user:pass@host:5432/db?sslmode=require")
        print("Standalone installs use SQLite in data/civicwatch.db and do not need it.")
        return 2

    from civicwatch import db
    from civicwatch.db import describe

    if db.backend_name() != "postgres":
        print("Refusing to run: CIVICWATCH_DB_BACKEND resolved to SQLite.")
        return 2

    print(f"Database: {describe()['target']}")
    applied = db.init_db()
    print(f"Migrations applied this run: {applied or 'none (already current)'}")

    rows = db.query("SELECT version, applied_at FROM schema_migrations ORDER BY version")
    for row in rows:
        print(f"  v{row['version']:<3} {row['applied_at']}")

    if args.status:
        for table in ("incidents", "media", "jobs", "connectors", "incident_events"):
            try:
                count = db.query_one(f"SELECT COUNT(*) AS n FROM {table}")["n"]
                print(f"  {table:<16} {count}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {table:<16} unavailable ({exc})")

    if args.create_connector:
        from civicwatch.cloud.service import create_connector

        created = create_connector(args.create_connector, notes="created by tools/cloud_migrate.py")
        print("\nConnector created. Copy this token now — it is not stored anywhere:")
        print(f"  id:    {created['id']}")
        print(f"  token: {created['token']}")
        print("\nOn the Mac that owns the cameras:")
        print("  CIVICWATCH_CLOUD_URL=https://<your-app>.vercel.app \\")
        print(f"  CIVICWATCH_CONNECTOR_TOKEN={created['token']} \\")
        print("  ./Start\\ Cloud\\ Connector.command")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
