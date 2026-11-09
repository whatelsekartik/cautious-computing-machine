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
        "connector_models",
        "connector_outbox",
        "connectors",
    ):
        db_module.execute(f"DELETE FROM {table}")
    for directory in (get_paths().media, get_paths().evidence, get_paths().processed):
        for entry in directory.glob("*"):
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
    settings_store._cache = None
    from civicwatch.storage import reset_storage

    reset_storage()
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


# ---------------------------------------------------------------------------
# Cloud fixtures: S3-compatible storage (moto) and the cloud API
# ---------------------------------------------------------------------------

ADMIN_TOKEN = "test-dashboard-token"
CONNECTOR_HEADERS = {"X-CivicWatch-CSRF": "1"}


@pytest.fixture
def s3_bucket(monkeypatch):
    """A real S3 endpoint (moto server) with a private bucket."""
    pytest.importorskip("moto.server", reason="moto is required for storage tests")
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0)
    server.start()
    host, port = server.get_host_and_port()
    endpoint = f"http://{host}:{port}"
    bucket = "civicwatch-test"
    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id="test-key",
        aws_secret_access_key="test-secret",
        region_name="us-east-1",
    )
    client.create_bucket(Bucket=bucket)
    monkeypatch.setenv("CIVICWATCH_S3_BUCKET", bucket)
    monkeypatch.setenv("CIVICWATCH_S3_REGION", "us-east-1")
    monkeypatch.setenv("CIVICWATCH_S3_ENDPOINT", endpoint)
    monkeypatch.setenv("CIVICWATCH_S3_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("CIVICWATCH_S3_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("CIVICWATCH_S3_FORCE_PATH_STYLE", "1")
    monkeypatch.setenv("CIVICWATCH_STORAGE", "s3")
    from civicwatch.storage import reset_storage

    reset_storage()
    yield {"endpoint": endpoint, "bucket": bucket, "client": client}
    server.stop()
    reset_storage()


@pytest.fixture
def cloud_env(monkeypatch, s3_bucket):
    monkeypatch.setenv("CIVICWATCH_MODE", "cloud")
    monkeypatch.setenv("CIVICWATCH_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("CIVICWATCH_DB_BACKEND", "sqlite")
    from civicwatch import db

    db.reset_connections()
    yield {"token": ADMIN_TOKEN, "s3": s3_bucket}
    db.reset_connections()


@pytest.fixture
def admin_headers():
    return {"Authorization": f"Bearer {ADMIN_TOKEN}", "X-CivicWatch-CSRF": "1"}


@pytest.fixture
def cloud_client(cloud_env):
    from fastapi.testclient import TestClient

    from civicwatch.cloud.app import create_cloud_app

    app = create_cloud_app()
    with TestClient(app) as client:
        yield client


@pytest.fixture
def cloud_server(cloud_env):
    """A real HTTP server so the connector can be tested over the network."""
    import socket
    import threading
    import time

    import uvicorn

    from civicwatch.cloud.app import create_cloud_app

    app = create_cloud_app()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    import httpx

    base_url = f"http://127.0.0.1:{port}"
    for _ in range(200):
        try:
            if httpx.get(base_url + "/api/health", timeout=0.5).status_code == 200:
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.05)
    else:  # pragma: no cover
        server.should_exit = True
        raise RuntimeError("cloud test server did not start")
    yield {"base_url": base_url, "port": port}
    server.should_exit = True
    thread.join(timeout=10)
