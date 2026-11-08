"""Test fixtures.

The test suite always uses an isolated temporary data directory, so it never
touches the operator's real incidents, footage or settings.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

_TEMP_ROOT = Path(tempfile.mkdtemp(prefix="civicwatch-tests-"))
os.environ["CIVICWATCH_DATA_DIR"] = str(_TEMP_ROOT / "data")
os.environ.setdefault("CIVICWATCH_ALLOW_REMOTE", "0")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from civicwatch import db as db_module  # noqa: E402
from civicwatch import settings_store  # noqa: E402
from civicwatch.config import get_paths  # noqa: E402


def _copy_repo_weights() -> None:
    """Reuse weights already downloaded in the checkout so model tests can run."""
    repo_models = BACKEND_ROOT.parent / "data" / "models"
    if not repo_models.exists():
        return
    target = get_paths().models
    target.mkdir(parents=True, exist_ok=True)
    for weights in repo_models.glob("*.pt"):
        destination = target / weights.name
        if not destination.exists():
            shutil.copy2(weights, destination)


@pytest.fixture(scope="session", autouse=True)
def _session_init():
    get_paths().ensure()
    db_module.init_db()
    _copy_repo_weights()
    yield
    try:
        db_module.db().close()
    except Exception:
        pass
    shutil.rmtree(_TEMP_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_state():
    """Wipe operational tables between tests, keeping the schema."""
    db_module.init_db()
    for table in (
        "incident_events",
        "incidents",
        "custom_models",
        "jobs",
        "media",
        "zones",
        "cameras",
        "settings",
        "audit_log",
    ):
        db_module.execute(f"DELETE FROM {table}")
    for directory in (get_paths().media, get_paths().evidence, get_paths().processed):
        for entry in directory.glob("*"):
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
    settings_store._cache = None
    yield


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from civicwatch.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def sample_video() -> Path:
    """20-frame synthetic video with a stationary rectangle (fast and licence-free)."""
    import cv2
    import numpy as np

    path = get_paths().media / "synthetic.avi"
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (320, 240))
    for index in range(30):
        frame = np.full((240, 320, 3), 40, dtype=np.uint8)
        cv2.rectangle(frame, (120, 120), (200, 180), (200, 180, 60), -1)
        cv2.circle(frame, (40 + index, 60), 8, (60, 200, 240), -1)
        writer.write(frame)
    writer.release()
    assert path.exists()
    yield path
    path.unlink(missing_ok=True)
