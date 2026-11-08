"""Runtime configuration and filesystem layout for CivicWatch."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "CivicWatch AI Studio"
APP_VERSION = "1.0.0"

# Categories that need a dedicated, trained model. They are never inferred
# from a generic COCO detector — the UI shows them as "model not installed".
CUSTOM_CATEGORIES = ("pothole", "garbage")

SAFETY_DISCLAIMER = (
    "Automated output is a potential violation signal for human review only. "
    "It is not a legally established offence and CivicWatch never issues "
    "challans or penalties."
)


def _default_data_dir() -> Path:
    env = os.environ.get("CIVICWATCH_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    # backend/civicwatch/config.py -> repository root / data
    return (Path(__file__).resolve().parents[2] / "data").resolve()


@dataclass(frozen=True)
class Paths:
    root: Path
    data: Path
    media: Path
    processed: Path
    evidence: Path
    models: Path
    db: Path
    secrets: Path
    logs: Path
    frontend_dist: Path

    def ensure(self) -> "Paths":
        for directory in (
            self.data,
            self.media,
            self.processed,
            self.evidence,
            self.models,
            self.secrets,
            self.logs,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return self


def get_paths() -> Paths:
    data = _default_data_dir()
    root = data.parent
    return Paths(
        root=root,
        data=data,
        media=data / "media",
        processed=data / "processed",
        evidence=data / "evidence",
        models=data / "models",
        db=data / "civicwatch.db",
        secrets=data / "secrets",
        logs=data / "logs",
        frontend_dist=root / "frontend" / "dist",
    ).ensure()


def host() -> str:
    """Bind address. Localhost by default; overridable only for packaging/dev."""
    return os.environ.get("CIVICWATCH_HOST", "127.0.0.1")


def port() -> int:
    return int(os.environ.get("CIVICWATCH_PORT", "8000"))


def allow_remote() -> bool:
    """Escape hatch used by container previews. Never enabled by the launchers."""
    return os.environ.get("CIVICWATCH_ALLOW_REMOTE", "0") == "1"
