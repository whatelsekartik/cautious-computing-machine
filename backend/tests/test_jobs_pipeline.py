"""End-to-end job pipeline tests with a deterministic stub detector.

Using a stub keeps the suite fast and independent of downloaded weights while
still exercising the real tracker, rule engine, evidence writer, SQLite writes
and HTTP surface. A separate test verifies the real YOLO model loads.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from civicwatch.detect.base import Detection
from civicwatch.detect import registry


class StubDetector:
    """Reports a stationary car inside the no-parking zone for every frame."""

    model_id = "stub-model"
    device = "cpu"
    names = {2: "car"}

    def __init__(self) -> None:
        self.calls = 0

    def predict(self, frame):
        self.calls += 1
        height, width = frame.shape[:2]
        # Place the box in the right-hand no-parking area of the frame.
        x1, y1 = width * 0.62, height * 0.15
        x2, y2 = width * 0.78, height * 0.35
        return [Detection((x1, y1, x2, y2), 0.82, 2, "car")]

    def describe(self):
        return {"model_id": self.model_id}


@pytest.fixture()
def stub_model(monkeypatch):
    detector = StubDetector()
    monkeypatch.setattr(registry, "get_detector", lambda **_: detector)
    monkeypatch.setattr(registry, "active_custom_detectors", lambda *a, **k: [])
    # create_job validates that weights exist; the stub does not need them.
    model_path = registry.weights_path("yolov8n")
    created = not model_path.exists()
    if created:
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_bytes(b"\x00" * 200_000)
    yield detector
    if created:
        model_path.unlink(missing_ok=True)


def upload(client, path: Path, name: str = "synthetic.avi") -> dict:
    with open(path, "rb") as handle:
        response = client.post(
            "/api/media", files={"file": (name, handle, "video/x-msvideo")}
        )
    assert response.status_code == 200, response.text
    return response.json()


def make_zone(client) -> dict:
    response = client.post(
        "/api/zones",
        json={
            "name": "Service road no parking",
            "kind": "no_parking",
            "polygon": [[0.55, 0.05], [0.98, 0.05], [0.98, 0.5], [0.55, 0.5]],
            "rules": {"dwell_seconds": 1, "classes": ["car"], "double_parking": False},
            "scope_type": "all",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def wait_for_job(client, job_id: str, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"completed", "failed", "stopped"}:
            return job
        time.sleep(0.4)
    raise AssertionError("job did not finish in time")


def test_upload_probe_and_delete(client, sample_video):
    media = upload(client, sample_video)
    assert media["width"] == 320
    assert media["height"] == 240
    assert media["frame_count"] == 30
    assert media["fps"] == pytest.approx(10.0)
    frame = client.get(f"/api/media/{media['id']}/frame?t=1")
    assert frame.status_code == 200
    assert frame.headers["content-type"] == "image/jpeg"
    assert client.delete(f"/api/media/{media['id']}").status_code == 200
    assert client.get(f"/api/media/{media['id']}").status_code == 404


def test_upload_rejects_non_video_files(client):
    response = client.post(
        "/api/media", files={"file": ("notes.txt", b"hello world", "text/plain")}
    )
    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]


def test_upload_rejects_undecodable_video(client):
    response = client.post(
        "/api/media", files={"file": ("broken.mp4", b"\x00" * 5000, "video/mp4")}
    )
    assert response.status_code == 400


def test_job_creates_incidents_with_evidence(client, sample_video, stub_model):
    media = upload(client, sample_video)
    zone = make_zone(client)

    response = client.post(
        "/api/jobs",
        json={"media_id": media["id"], "sample_every": 2, "imgsz": 320},
    )
    assert response.status_code == 200, response.text
    job_id = response.json()["id"]
    job = wait_for_job(client, job_id)

    assert job["status"] == "completed", job
    assert job["frames_processed"] >= 10
    assert job["incidents"] >= 1
    assert job["device"] == "cpu"

    incidents = client.get("/api/incidents").json()
    assert incidents["total"] >= 1
    first = incidents["items"][0]
    assert first["category"] == "illegal_parking"
    assert first["zone_id"] == zone["id"]
    assert first["source_ref"] == media["id"]
    assert first["class_name"] == "car"
    assert first["track_id"] is not None
    assert first["has_snapshot"] is True
    assert first["has_clip"] is True

    snapshot = client.get(f"/api/incidents/{first['id']}/snapshot")
    assert snapshot.status_code == 200
    preview = client.get(f"/api/incidents/{first['id']}/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/gif"

    frame = client.get(f"/api/jobs/{job_id}/frame?t=1")
    assert frame.status_code == 200
    video = client.get(f"/api/jobs/{job_id}/video")
    assert video.status_code == 200
    assert video.headers["content-type"] == "video/mp4"

    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["annotated_duration_s"] and detail["annotated_duration_s"] > 0

    # Cleanup removes generated artefacts.
    assert client.delete(f"/api/jobs/{job_id}").status_code == 200
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_zones_without_matching_scope_do_not_fire(client, sample_video, stub_model):
    media = upload(client, sample_video)
    response = client.post(
        "/api/zones",
        json={
            "name": "Other camera only",
            "kind": "no_parking",
            "polygon": [[0.55, 0.05], [0.98, 0.05], [0.98, 0.5], [0.55, 0.5]],
            "rules": {"dwell_seconds": 1, "classes": ["car"]},
            "scope_type": "camera",
            "scope_id": "cam-does-not-exist",
        },
    )
    assert response.status_code == 200
    job_id = client.post(
        "/api/jobs", json={"media_id": media["id"], "sample_every": 3, "imgsz": 320}
    ).json()["id"]
    job = wait_for_job(client, job_id)
    assert job["incidents"] == 0


def test_job_requires_uploaded_media(client):
    response = client.post("/api/jobs", json={"media_id": "missing", "sample_every": 5})
    assert response.status_code == 404


def test_job_rejects_missing_model(client, sample_video):
    media = upload(client, sample_video)
    model_path = registry.weights_path("yolov8n")
    backup = model_path.read_bytes() if model_path.exists() else None
    if backup is not None:
        model_path.unlink()
    try:
        response = client.post("/api/jobs", json={"media_id": media["id"]})
        assert response.status_code == 400
        assert "not installed" in response.json()["detail"]
    finally:
        if backup is not None:
            model_path.write_bytes(backup)


def test_pause_resume_and_stop_controls(client, sample_video, stub_model):
    media = upload(client, sample_video)
    make_zone(client)
    job_id = client.post(
        "/api/jobs", json={"media_id": media["id"], "sample_every": 1, "imgsz": 320}
    ).json()["id"]

    # Wait until processing starts, then pause.
    for _ in range(50):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"processing", "paused", "completed"}:
            break
        time.sleep(0.1)
    paused = client.post(f"/api/jobs/{job_id}/pause")
    assert paused.status_code == 200
    time.sleep(0.2)
    resumed = client.post(f"/api/jobs/{job_id}/resume")
    assert resumed.status_code == 200
    stopped = client.post(f"/api/jobs/{job_id}/stop")
    assert stopped.status_code == 200
    final = wait_for_job(client, job_id)
    assert final["status"] in {"stopped", "completed"}


def test_real_yolo_model_loads_and_runs(tmp_path):
    """Integration check for the real detector (skipped when weights are absent)."""
    path = registry.weights_path("yolov8n")
    if not path.exists():
        pytest.skip("yolov8n weights are not installed in this environment")
    import numpy as np

    from civicwatch.detect.yolo import YOLODetector

    detector = YOLODetector(path, "yolov8n", device="cpu", imgsz=320)
    assert len(detector.names) == 80
    blank = np.full((240, 320, 3), 128, dtype=np.uint8)
    detections = detector.predict(blank)
    assert isinstance(detections, list)
    assert all(det.score >= detector.confidence for det in detections)
