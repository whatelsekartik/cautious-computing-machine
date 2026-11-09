#!/usr/bin/env python3
"""Thin wrapper so the connector can be started without PYTHONPATH gymnastics."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from civicwatch.connector.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
