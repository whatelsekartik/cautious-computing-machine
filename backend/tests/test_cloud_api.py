"""Cloud dashboard API: auth, incidents, evidence, uploads and connectors."""

from __future__ import annotations

import io

import httpx
import pytest
from PIL import Image

from civicwatch.cloud import service

INCIDENT = {
    "client_incident_id": "INC-20261108-5001",
    "category": "illegal_parking",
    "confidence": 0.82,
    "camera": "Ward 6 · Bus stand",
    "location": "Opposite platform 2",
    "captured_at": "2026-11-08T12:30:00+00:00",
    "track_id": 9,
    "zone_name": "No parking",
    "dwell_seconds": 21.4,
    "class_name": "car",
    "severity": "high",
    "source_type": "video",
    "source_ref": "media-77",
}


def _connector(client, admin_headers, name="Yard Mac") -> tuple[dict, dict]:
    created = client.post("/api/connectors", json={"name": name}, headers=admin_headers)
    assert created.status_code == 200, created.text
    record = created.json()
    return record, {"Authorization": f"Bearer {record['token']}"}


def _jpeg_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (90, 110, 130)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _put_evidence(url: str, content: bytes, content_type: str) -> None:
    response = httpx.put(url, content=content, headers={"Content-Type": content_type})
    assert response.status_code == 200, response.text


def test_public_endpoints_describe_the_deployment(cloud_client):
    health = cloud_client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["mode"] == "cloud"
    assert health.json()["database"] == "reachable"

    config = cloud_client.get("/api/cloud/config").json()
    assert config["mode"] == "cloud"
    assert config["auth_required"] is True
    assert config["capabilities"]["local_inference"] is False
    assert config["capabilities"]["video_upload"] is True
    assert config["limits"]["max_json_request_bytes"] == 4 * 1024 * 1024


def test_dashboard_requires_authentication(cloud_client, admin_headers):
    assert cloud_client.get("/api/incidents").status_code == 401
    assert cloud_client.get("/api/overview").status_code == 401
    bad = {"Authorization": "Bearer definitely-not-the-token"}
    assert cloud_client.get("/api/incidents", headers=bad).status_code == 401
    assert cloud_client.get("/api/incidents", headers=admin_headers).status_code == 200


def test_session_cookie_login_and_csrf_protection(cloud_client, cloud_env, admin_headers):
    assert cloud_client.post("/api/auth/session", json={"token": "nope"}).status_code == 401

    login = cloud_client.post("/api/auth/session", json={"token": cloud_env["token"]})
    assert login.status_code == 200
    assert login.json()["ok"] is True
    assert "cw_session" in cloud_client.cookies

    # The cookie alone is enough for reads (needed by <img> and file downloads).
    assert cloud_client.get("/api/incidents").status_code == 200

    # Writes require the custom header, which cross-site forms cannot set.
    blocked = cloud_client.post("/api/auth/logout-test", content=b"", headers={})
    assert blocked.status_code in {403, 404}
    csrf = cloud_client.patch("/api/settings", json={"confidence": 0.5}, headers={"X-CivicWatch-CSRF": "1"})
    assert csrf.status_code == 200, csrf.text
    assert csrf.json()["values"]["confidence"] == 0.5

    missing_csrf = cloud_client.patch("/api/settings", json={"confidence": 0.4})
    assert missing_csrf.status_code == 403

    # Bearer auth does not need the CSRF header (no ambient cookie involved).
    bearer_only = cloud_client.patch(
        "/api/settings", json={"confidence": 0.45}, headers={"Authorization": admin_headers["Authorization"]}
    )
    assert bearer_only.status_code == 200
    cloud_client.cookies.clear()


def test_connector_lifecycle_and_model_reporting(cloud_client, admin_headers):
    record, headers = _connector(cloud_client, admin_headers, "Ward office iMac")
    assert record["token"].startswith("cwconn_")
    assert record["online"] is False

    listing = cloud_client.get("/api/connectors", headers=admin_headers).json()["items"]
    assert len(listing) == 1
    assert "token" not in listing[0]  # shown exactly once, never listed
    assert "token_hash" not in listing[0]

    heartbeat = cloud_client.post(
        "/api/connector/heartbeat",
        headers=headers,
        json={
            "version": "1.0.0",
            "device": "mps",
            "capabilities": {"inference": True, "rtsp": True},
            "models": {"selected": "yolov8n"},
        },
    )
    assert heartbeat.status_code == 200, heartbeat.text
    assert heartbeat.json()["config"]["heartbeat_seconds"] == 30
    assert cloud_client.get("/api/connectors", headers=admin_headers).json()["items"][0]["online"] is True

    # Potholes stay "not installed" until the connector reports real weights.
    before = cloud_client.get("/api/models", headers=admin_headers).json()
    assert all(not category["installed"] for category in before["categories"] if category["category"] == "pothole")

    reported = cloud_client.post(
        "/api/connector/models",
        headers=headers,
        json=[
            {"id": "yolov8n", "name": "YOLOv8 nano", "family": "vehicle_pedestrian", "license": "AGPL-3.0"},
            {
                "id": "pothole-v1",
                "name": "Pothole weights",
                "family": "pothole",
                "classes": ["pothole"],
                "license": "internal",
            },
        ],
    )
    assert reported.status_code == 200
    after = cloud_client.get("/api/models", headers=admin_headers).json()
    pothole = next(c for c in after["categories"] if c["category"] == "pothole")
    assert pothole["installed"] is True
    assert pothole["model"]["classes"] == ["pothole"]
    assert after["management"] == "connector"
    assert any(model["installed"] for model in after["builtin"])

    revoked = cloud_client.delete(f"/api/connectors/{record['id']}", headers=admin_headers)
    assert revoked.status_code == 200
    assert cloud_client.post("/api/connector/heartbeat", headers=headers, json={}).status_code == 401


def test_direct_upload_queue_and_connector_assignment(cloud_client, admin_headers, s3_bucket):
    _, connector_headers = _connector(cloud_client, admin_headers)

    upload = cloud_client.post(
        "/api/media/upload-url",
        headers=admin_headers,
        json={"filename": "street.mp4", "content_type": "video/mp4", "size_bytes": 2048},
    )
    assert upload.status_code == 200, upload.text
    payload = upload.json()
    assert payload["key"].startswith("media/")
    assert "X-Amz-Signature" in payload["upload"]["url"]

    # The browser uploads straight to object storage — not through the function.
    _put_evidence(payload["upload"]["url"], b"\x00" * 2048, "video/mp4")
    completed = cloud_client.post(f"/api/media/{payload['media_id']}/complete", headers=admin_headers)
    assert completed.status_code == 200, completed.text

    job = cloud_client.post("/api/jobs", headers=admin_headers, json={"media_id": payload["media_id"]})
    assert job.status_code == 200, job.text
    job_id = job.json()["id"]
    assert job.json()["status"] == "queued"

    assignments = cloud_client.get("/api/connector/assignments", headers=connector_headers)
    assert assignments.status_code == 200
    items = assignments.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == job_id
    assert items[0]["download"]["url"].startswith(s3_bucket["endpoint"]) or "X-Amz-Signature" in items[0]["download"]["url"]
    assert items[0]["settings"]["sample_every"] >= 1

    # A second poll must not hand the same assignment to another connector.
    assert cloud_client.get("/api/connector/assignments", headers=connector_headers).json()["items"] == []

    assert cloud_client.post(
        f"/api/connector/assignments/{job_id}",
        headers=connector_headers,
        json={"status": "processing", "progress": 0.2, "device": "mps"},
    ).status_code == 200
    done = cloud_client.post(
        f"/api/connector/assignments/{job_id}",
        headers=connector_headers,
        json={
            "status": "completed",
            "frames_total": 300,
            "frames_processed": 60,
            "detections": 140,
            "incidents": 2,
            "runtime_s": 12.5,
            "device": "mps",
        },
    )
    assert done.status_code == 200
    assert done.json()["status"] == "completed"
    assert done.json()["frames_processed"] == 60
    assert done.json()["origin"] == "cloud"
    assert cloud_client.get("/api/jobs", headers=admin_headers).json()["items"][0]["status"] == "completed"


def test_incident_ingestion_evidence_and_exports(cloud_client, admin_headers):
    _, connector_headers = _connector(cloud_client, admin_headers)

    issued = cloud_client.post(
        "/api/connector/evidence/upload-urls",
        headers=connector_headers,
        json={
            "client_incident_id": INCIDENT["client_incident_id"],
            "files": [
                {"kind": "snapshot", "content_type": "image/jpeg", "bytes": 11},
                {"kind": "clip", "content_type": "video/mp4", "bytes": 13},
            ],
        },
    )
    assert issued.status_code == 200, issued.text
    urls = {item["kind"]: item for item in issued.json()["files"]}
    jpeg = _jpeg_bytes()
    _put_evidence(urls["snapshot"]["url"], jpeg, "image/jpeg")
    _put_evidence(urls["clip"]["url"], b"mp4-clip-data", "video/mp4")

    payload = {
        **INCIDENT,
        "evidence": {
            "snapshot_key": urls["snapshot"]["key"],
            "clip_key": urls["clip"]["key"],
        },
    }
    ingested = cloud_client.post("/api/connector/incidents", headers=connector_headers, json={"incidents": [payload]})
    assert ingested.status_code == 200, ingested.text
    body = ingested.json()
    assert body["rejected"] == []
    cloud_id = body["accepted"][0]["incident_id"]

    listing = cloud_client.get("/api/incidents", headers=admin_headers).json()
    assert listing["total"] == 1
    item = listing["items"][0]
    assert item["id"] == cloud_id
    assert item["origin"] == "connector"
    assert item["has_snapshot"] is True and item["has_clip"] is True
    assert item["evidence_refs"]["snapshot"] == urls["snapshot"]["key"]
    assert item["evidence_refs"]["clip"] == urls["clip"]["key"]

    # Duplicate prevention: the same client id is accepted once.
    again = cloud_client.post("/api/connector/incidents", headers=connector_headers, json={"incidents": [payload]})
    assert again.json()["accepted"] == []
    assert again.json()["duplicates"][0]["incident_id"] == cloud_id
    assert cloud_client.get("/api/incidents", headers=admin_headers).json()["total"] == 1

    # Private evidence access: the API redirects to a signed, expiring URL.
    redirect = cloud_client.get(
        f"/api/incidents/{cloud_id}/snapshot", headers=admin_headers, follow_redirects=False
    )
    assert redirect.status_code == 302
    location = redirect.headers["location"]
    assert "X-Amz-Signature" in location
    fetched = httpx.get(location)
    assert fetched.status_code == 200 and fetched.content == jpeg

    clip = cloud_client.get(
        f"/api/incidents/{cloud_id}/clip?download=true", headers=admin_headers, follow_redirects=False
    )
    assert clip.status_code == 302
    assert "attachment" in clip.headers["location"]

    # Review workflow is a dashboard action and is audited.
    reviewed = cloud_client.patch(
        f"/api/incidents/{cloud_id}",
        headers=admin_headers,
        json={"status": "verified", "note": "Confirmed on site"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "verified"
    assert any(
        event["status"] == "verified" for event in reviewed.json()["events"]
    )

    csv_export = cloud_client.get("/api/incidents/export.csv", headers=admin_headers)
    assert csv_export.status_code == 200
    assert cloud_id.encode() in csv_export.content
    assert "text/csv" in csv_export.headers["content-type"]

    pdf_export = cloud_client.get("/api/incidents/export.pdf", headers=admin_headers)
    assert pdf_export.status_code == 200
    assert pdf_export.content.startswith(b"%PDF")
    assert pdf_export.headers["content-type"].startswith("application/pdf")


@pytest.mark.parametrize(
    "mutation",
    [
        {"password": "hunter2"},
        {"rtsp_url": "rtsp://10.0.0.9/live"},
        {"metadata": {"credentials": "user:pass"}},
    ],
)
def test_connector_payloads_cannot_smuggle_credentials(cloud_client, admin_headers, mutation):
    _, connector_headers = _connector(cloud_client, admin_headers)
    payload = {**INCIDENT, **mutation}
    response = cloud_client.post("/api/connector/incidents", headers=connector_headers, json={"incidents": [payload]})
    if "password" in mutation or "rtsp_url" in mutation:
        # Unknown top-level fields are rejected by the request model itself.
        assert response.status_code == 422, response.text
    else:
        # Nested smuggled credentials are rejected per incident, so one bad row
        # never fails an entire batch.
        assert response.status_code == 200, response.text
        assert response.json()["accepted"] == []
        assert "credentials" in response.json()["rejected"][0]["reason"]
    assert cloud_client.get("/api/incidents", headers=admin_headers).json()["total"] == 0


def test_credentials_in_camera_labels_are_rejected(cloud_client, admin_headers):
    _, connector_headers = _connector(cloud_client, admin_headers)
    payload = {
        **INCIDENT,
        "client_incident_id": "INC-20261108-5003",
        "camera": "rtsp://operator:secret@10.0.0.4/stream1",
    }
    result = cloud_client.post(
        "/api/connector/incidents", headers=connector_headers, json={"incidents": [payload]}
    ).json()
    assert result["accepted"] == []
    assert result["rejected"] and "credentials" in result["rejected"][0]["reason"]
    assert cloud_client.get("/api/incidents", headers=admin_headers).json()["total"] == 0


def test_cloud_diagnostics_and_storage_summary(cloud_client, admin_headers):
    diagnostics = cloud_client.get("/api/system/diagnostics", headers=admin_headers).json()
    names = {check["name"] for check in diagnostics["checks"]}
    assert {"Database", "Evidence storage", "Dashboard token"} <= names
    assert diagnostics["ok"] is True
    assert diagnostics["mode"] == "cloud"

    info = cloud_client.get("/api/system/info", headers=admin_headers).json()
    assert info["deployment"]["mode"] == "cloud"
    assert info["deployment"]["capabilities"]["local_inference"] is False
    assert info["privacy"]["facial_recognition"] is False

    storage = cloud_client.get("/api/storage", headers=admin_headers).json()
    assert storage["backend"]["kind"] == "s3"
    assert storage["counts"]["videos"] >= 0

    assert cloud_client.post("/api/storage/cleanup", headers=admin_headers).status_code == 501


def test_service_level_overview_matches_frontend_contract(cloud_client, admin_headers):
    overview = service.overview()
    for key in ("app", "monitoring", "incidents", "recent", "performance", "storage", "timeline", "hardware", "connectors"):
        assert key in overview
    assert overview["monitoring"]["device"] in {"connector", "offline"}
    assert overview["deployment"]["inference"] == "local_connector"
    assert len(overview["timeline"]) == 7


def test_corrupt_evidence_does_not_break_the_pdf(cloud_client, admin_headers):
    _, connector_headers = _connector(cloud_client, admin_headers)
    incident = {**INCIDENT, "client_incident_id": "INC-20261108-5009"}
    issued = cloud_client.post(
        "/api/connector/evidence/upload-urls",
        headers=connector_headers,
        json={
            "client_incident_id": incident["client_incident_id"],
            "files": [{"kind": "snapshot", "content_type": "image/jpeg", "bytes": 9}],
        },
    ).json()
    snapshot = issued["files"][0]
    _put_evidence(snapshot["url"], b"not-a-jpeg", "image/jpeg")
    ingested = cloud_client.post(
        "/api/connector/incidents",
        headers=connector_headers,
        json={"incidents": [{**incident, "evidence": {"snapshot_key": snapshot["key"]}}]},
    )
    assert ingested.status_code == 200
    pdf = cloud_client.get("/api/incidents/export.pdf", headers=admin_headers)
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")


def test_oversized_request_bodies_are_rejected_with_413(cloud_client, admin_headers):
    body = b"x" * (4 * 1024 * 1024 + 1)
    response = cloud_client.post(
        "/api/connector/incidents", content=body, headers={**admin_headers, "Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert "upload-url" in response.json()["detail"]


def test_security_headers_are_set(cloud_client):
    response = cloud_client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
