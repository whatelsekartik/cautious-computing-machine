"""Connector ↔ cloud integration tests over real HTTP.

The cloud app runs in a background uvicorn thread and object storage is a real
S3 endpoint (moto), so the connector exercises the same code paths it will use
against Vercel: bearer auth, presigned uploads without the token, retry/backoff,
duplicate handling and local job processing for cloud assignments.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from civicwatch.cloud import service as cloud_service
from civicwatch.config import get_paths
from civicwatch.connector import outbox
from civicwatch.connector.client import CloudClient, CloudError, CloudRejected, CloudUnavailable
from civicwatch.connector.worker import ConnectorWorker
from civicwatch.db import execute, query, query_one
from civicwatch.security import decrypt
from civicwatch.services.cameras import CameraService
from civicwatch.services.incidents import IncidentService, IncidentDraft

ADMIN_TOKEN = "test-dashboard-token"
ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}", "X-CivicWatch-CSRF": "1"}


@pytest.fixture
def connector(cloud_server):
    response = httpx.post(
        cloud_server["base_url"] + "/api/connectors", json={"name": "Test Mac"}, headers=ADMIN
    )
    assert response.status_code == 200, response.text
    return response.json()


def make_client(cloud_server, token: str) -> CloudClient:
    return CloudClient(cloud_server["base_url"], token, timeout=15.0)


@pytest.fixture
def worker(cloud_server, connector):
    client = make_client(cloud_server, connector["token"])
    instance = ConnectorWorker(client, process_assignments=False, poll_seconds=5)
    yield instance
    client.close()


def local_incident(index: int = 1, *, with_evidence: bool = True, camera: str = "Ward 1 · Gate") -> dict:
    incident_id = IncidentService.reserve_id()
    snapshot = clip = None
    if with_evidence:
        folder = get_paths().evidence / incident_id
        folder.mkdir(parents=True, exist_ok=True)
        snapshot_path = folder / "snapshot.jpg"
        clip_path = folder / "clip.mp4"
        snapshot_path.write_bytes(b"\xff\xd8connector-snapshot")
        clip_path.write_bytes(b"connector-clip-bytes")
        snapshot, clip = str(snapshot_path), str(clip_path)
    return IncidentService.create(
        IncidentDraft(
            id=incident_id,
            category="illegal_parking",
            confidence=0.71,
            camera=camera,
            location="Outside the school gate",
            captured_at="2026-11-08T08:30:00+00:00",
            source_type="video",
            source_ref=f"media-{index}",
            track_id=4,
            zone_name="No parking",
            dwell_seconds=19.2,
            class_name="car",
            snapshot_path=snapshot,
            clip_path=clip,
        )
    )


def cloud_incidents(cloud_server) -> list[dict]:
    response = httpx.get(cloud_server["base_url"] + "/api/incidents?limit=100", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_heartbeat_only_claims_installed_models(worker, connector, cloud_server):
    result = worker.heartbeat()
    assert result["ok"] is True
    assert result["connector_id"] == connector["id"]
    config = result["config"]
    assert config["heartbeat_seconds"] == 30
    assert config["assignment_poll_seconds"] == 20
    assert config["upload_expiry_seconds"] == 900
    assert config["model_id"]

    listing = httpx.get(cloud_server["base_url"] + "/api/connectors", headers=ADMIN).json()["items"]
    assert listing[0]["name"] == "Test Mac"
    assert listing[0]["online"] is True

    models = httpx.get(cloud_server["base_url"] + "/api/models", headers=ADMIN).json()
    installed = {model["id"] for model in models["builtin"] if model["installed"]}
    # The test fixture copies the checkout's real weights, so yolov8n is present.
    assert "yolov8n" in installed
    # Potholes/waste have no weights, so they must not be advertised as installed.
    for category in models["categories"]:
        if category["category"] in {"pothole", "garbage"}:
            assert category["installed"] is False


def test_incident_is_uploaded_once_with_evidence(worker, cloud_server):
    incident = local_incident()
    summary = worker.flush()
    assert summary["sent"] == 1 and summary["failed"] == 0
    assert outbox.pending_count() == 0

    items = [item for item in cloud_incidents(cloud_server) if item["origin"] == "connector"]
    assert len(items) == 1
    pushed = items[0]
    assert pushed["has_snapshot"] is True and pushed["has_clip"] is True
    assert pushed["camera"] == incident["camera"]
    assert pushed["status"] == "detected"

    # Evidence is stored privately in object storage and served via a signed URL.
    redirect = httpx.get(
        f"{cloud_server['base_url']}/api/incidents/{pushed['id']}/snapshot",
        headers=ADMIN,
        follow_redirects=False,
    )
    assert redirect.status_code == 302
    assert "X-Amz-Signature" in redirect.headers["location"]

    # Nothing is sent twice, and running the loop again is a no-op.
    second = worker.flush()
    assert second["attempted"] == 0
    assert len([i for i in cloud_incidents(cloud_server) if i["origin"] == "connector"]) == 1

    # A lost "sent" acknowledgement is handled by the cloud's own dedupe.
    execute(
        "UPDATE connector_outbox SET status = 'pending', next_attempt_at = ? WHERE incident_id = ?",
        ("2000-01-01T00:00:00+00:00", incident["id"]),
    )
    replay = worker.flush()
    assert replay["duplicates"] == 1
    assert len([i for i in cloud_incidents(cloud_server) if i["origin"] == "connector"]) == 1


def test_offline_connector_buffers_with_backoff_then_recovers(cloud_server, connector, monkeypatch):
    incident = local_incident()
    offline = CloudClient("http://127.0.0.1:9", connector["token"], timeout=2.0)
    try:
        worker = ConnectorWorker(offline, process_assignments=False)
        summary = worker.flush()
    finally:
        offline.close()
    assert summary["failed"] == 1
    assert summary["pending"] == 1
    row = query_one("SELECT * FROM connector_outbox WHERE incident_id = ?", (incident["id"],))
    assert row["status"] == "failed" and row["attempts"] == 1
    assert row["next_attempt_at"] > row["updated_at"]  # scheduled in the future
    assert outbox.due() == []  # backoff prevents hammering the endpoint

    # Offline ticks are reported, not raised, and keep the queue intact.
    offline_again = CloudClient("http://127.0.0.1:9", connector["token"], timeout=2.0)
    try:
        tick = ConnectorWorker(offline_again, process_assignments=False).tick()
    finally:
        offline_again.close()
    assert tick["ok"] is False and tick["offline"] is True
    assert tick["pending"] == 1

    # Reconnect: the backlog drains as soon as the schedule allows.
    execute(
        "UPDATE connector_outbox SET next_attempt_at = ? WHERE incident_id = ?",
        ("2000-01-01T00:00:00+00:00", incident["id"]),
    )
    online = make_client(cloud_server, connector["token"])
    try:
        recovered = ConnectorWorker(online, process_assignments=False).flush()
    finally:
        online.close()
    assert recovered["sent"] == 1 and recovered["pending"] == 0
    assert any(item["origin"] == "connector" for item in cloud_incidents(cloud_server))


def test_backoff_schedule_and_stale_flag():
    incident = local_incident(with_evidence=False)
    outbox.enqueue_incident(incident)
    row = query_one("SELECT id FROM connector_outbox WHERE incident_id = ?", (incident["id"],))
    delays = []
    for attempt in range(1, 9):
        next_at = outbox.mark_failed(row["id"], f"attempt {attempt}", attempt)
        delays.append(next_at)
    assert delays == sorted(delays)  # every retry waits at least as long as the last
    assert len(set(delays[:6])) == 6  # 5s, 15s, 45s, 120s, 300s, 900s
    assert delays[6] == delays[7] == max(delays)  # ...then capped at 15 minutes
    stats = outbox.stats()
    assert stats["stale"] is True and stats["failed"] == 1
    execute("DELETE FROM connector_outbox WHERE incident_id = ?", (incident["id"],))


def test_rejected_token_reports_instead_of_losing_incidents(cloud_server, connector):
    incident = local_incident(with_evidence=False)
    bad = CloudClient(cloud_server["base_url"], "cwconn_" + "0" * 40, timeout=10.0)
    try:
        summary = ConnectorWorker(bad, process_assignments=False).tick()
    finally:
        bad.close()
    assert summary["ok"] is False
    assert "401" in summary["error"] or "rejected" in summary["error"].lower()
    # Nothing was sent, and nothing was dropped: the incident still exists and
    # syncs as soon as valid credentials are used.
    assert query_one("SELECT id FROM incidents WHERE id = ?", (incident["id"],))
    good = make_client(cloud_server, connector["token"])
    try:
        recovered = ConnectorWorker(good, process_assignments=False).flush()
    finally:
        good.close()
    assert recovered["sent"] == 1 and outbox.pending_count() == 0


def test_rtsp_credentials_and_tokens_never_leave_the_mac(worker, cloud_server, connector):
    camera = CameraService().create(
        {
            "name": "Gate camera",
            "source_type": "rtsp",
            "source": "rtsp://10.1.2.3/stream1",
            "username": "operator",
            "password": "hunter2-secret",
            "authorised": True,
        }
    )
    stored = query_one("SELECT source_value FROM cameras WHERE id = ?", (camera["id"],))["source_value"]
    assert "hunter2-secret" not in stored
    assert decrypt(stored).startswith("rtsp://")

    incident = local_incident(camera="Gate camera", with_evidence=False)
    worker.flush()

    payloads = [row["payload_json"] for row in query("SELECT payload_json FROM connector_outbox")]
    combined = "\n".join(payloads)
    for secret in ("hunter2-secret", "operator:", "rtsp://10.1.2.3", connector["token"]):
        assert secret not in combined
    assert incident["id"] in combined

    # The dashboard copy is sanitised too: no stream URL, no credentials.
    pushed = [item for item in cloud_incidents(cloud_server) if item["origin"] == "connector"]
    assert pushed and pushed[0]["camera"] == "Gate camera"
    serialized = json.dumps(pushed)
    assert "10.1.2.3" not in serialized and "hunter2" not in serialized


def test_connector_client_rejects_plain_http_to_the_internet(cloud_server, connector):
    with pytest.raises(CloudError):
        CloudClient("http://civicwatch.example.com", "token")
    # localhost stays possible for development and tests.
    offline = CloudClient("http://127.0.0.1:9", "token", timeout=2.0)
    try:
        with pytest.raises(CloudUnavailable):
            offline.heartbeat({})
    finally:
        offline.close()
    online = make_client(cloud_server, connector["token"])
    try:
        with pytest.raises(CloudRejected):
            online._request("GET", "/api/nope")  # 404 → permanent rejection
    finally:
        online.close()


def test_cloud_assignment_is_processed_by_the_local_connector(
    cloud_server, connector, sample_video, s3_bucket
):
    """The only place inference runs is here, on the "operator's Mac"."""
    video_bytes = sample_video.read_bytes()

    upload = httpx.post(
        cloud_server["base_url"] + "/api/media/upload-url",
        headers=ADMIN,
        json={"filename": "synthetic.avi", "content_type": "video/x-msvideo", "size_bytes": len(video_bytes)},
    )
    assert upload.status_code == 200, upload.text
    issued = upload.json()
    put = httpx.put(issued["upload"]["url"], content=video_bytes, headers={"Content-Type": "video/x-msvideo"})
    assert put.status_code == 200, put.text
    completed = httpx.post(
        f"{cloud_server['base_url']}/api/media/{issued['media_id']}/complete", headers=ADMIN
    )
    assert completed.status_code == 200, completed.text

    queued = httpx.post(
        cloud_server["base_url"] + "/api/jobs",
        headers=ADMIN,
        json={"media_id": issued["media_id"]},
    )
    assert queued.status_code == 200, queued.text
    job_id = queued.json()["id"]

    client = make_client(cloud_server, connector["token"])
    try:
        worker = ConnectorWorker(client, process_assignments=True, poll_seconds=5)
        outcome = worker.process_assignments()
    finally:
        client.close()

    assert outcome["claimed"] == 1
    result = outcome["processed"][0]
    assert result["status"] == "completed", result
    assert result["id"] == job_id

    job = httpx.get(cloud_server["base_url"] + "/api/jobs", headers=ADMIN).json()["items"][0]
    assert job["id"] == job_id
    assert job["status"] == "completed"
    assert job["origin"] == "cloud"
    assert job["frames_processed"] > 0
    assert job["frames_total"] >= job["frames_processed"]
    # The synthetic video contains no vehicles: the pipeline must not invent
    # incidents (the demo weights were not asked to hallucinate).
    assert job["incidents"] == result["incidents"]

    # Media registered by the connector is marked as such locally.
    rows = query("SELECT origin, upload_status FROM media")
    connector_rows = [row for row in rows if row["origin"] == "connector"]
    assert connector_rows and connector_rows[0]["upload_status"] == "ready"
