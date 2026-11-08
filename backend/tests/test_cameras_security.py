"""Camera credential handling, authorisation gates and security primitives."""

import pytest

from civicwatch.config import get_paths
from civicwatch.security import (
    LocalhostOnlyMiddleware,
    _is_local,
    decrypt,
    encrypt,
    redact,
)
from civicwatch.services.cameras import (
    CameraError,
    build_rtsp_url,
    camera_service,
    display_url,
)


def test_encryption_roundtrip_and_key_permissions():
    token = encrypt("rtsp://user:secret@10.0.0.4/live")
    assert token != "rtsp://user:secret@10.0.0.4/live"
    assert decrypt(token) == "rtsp://user:secret@10.0.0.4/live"
    key = get_paths().secrets / "credentials.key"
    assert key.exists()
    assert oct(key.stat().st_mode)[-3:] == "600"


def test_redaction_hides_credentials_everywhere():
    assert redact("rtsp://admin:pw@10.0.0.1:554/stream") == "rtsp://***:***@10.0.0.1:554/stream"
    assert "secret" not in redact("http://host/cam?password=secret&x=1")
    assert "***" in redact("http://host/cam?password=secret&x=1")
    assert _is_local("127.0.0.1") and _is_local("::1") and _is_local("testclient")
    assert not _is_local("192.168.1.5")


def test_build_url_inserts_and_escapes_credentials():
    url = build_rtsp_url("rtsp://10.0.0.9:554/live", "operator", "p@ss word")
    assert url == "rtsp://operator:p%40ss%20word@10.0.0.9:554/live"
    with pytest.raises(CameraError):
        build_rtsp_url("ftp://nope", None, None)
    with pytest.raises(CameraError):
        build_rtsp_url("", None, None)


def test_display_url_never_contains_credentials():
    token = encrypt("rtsp://admin:hunter2@10.0.0.9:554/stream1")
    shown = display_url(token)
    assert "admin" not in shown and "hunter2" not in shown
    assert "10.0.0.9" in shown


def test_create_requires_authorisation_and_masks_credentials(client):
    with pytest.raises(CameraError):
        camera_service.create({"name": "Nope", "source_type": "rtsp", "source": "rtsp://h/s"})
    response = client.post(
        "/api/cameras",
        json={
            "name": "Ward 12 junction",
            "source_type": "rtsp",
            "source": "rtsp://10.1.2.3:554/Streaming/Channels/101",
            "username": "viewer",
            "password": "s3cret",
            "authorised": True,
            "location": "Ward 12",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "s3cret" not in response.text
    assert "viewer@" not in response.text
    assert body["display_source"] == "rtsp://10.1.2.3:554/***"
    assert body["has_credentials"] is True

    # The encrypted URL is stored, never the plaintext.
    from civicwatch.db import query_one

    row = query_one("SELECT source_value FROM cameras WHERE id = ?", (body["id"],))
    assert "s3cret" not in row["source_value"]
    assert decrypt(row["source_value"]).endswith("/Streaming/Channels/101")


def test_start_requires_authorised_flag(client):
    created = camera_service.create(
        {
            "name": "Unauthorised",
            "source_type": "webcam",
            "device_index": 0,
            "authorised": True,
        }
    )
    camera_service.update(created["id"], {"authorised": False})
    response = client.post(f"/api/cameras/{created['id']}/start")
    assert response.status_code == 400
    assert "authorised" in response.json()["detail"].lower()


def test_webcam_camera_can_be_listed_without_secrets(client):
    created = camera_service.create(
        {"name": "Built-in", "source_type": "webcam", "device_index": 0, "authorised": True}
    )
    listing = client.get("/api/cameras").json()
    assert listing["items"][0]["id"] == created["id"]
    assert "device_index" not in listing["items"][0]


def test_unreachable_camera_reports_error_not_crash(client):
    created = camera_service.create(
        {
            "name": "Offline stream",
            "source_type": "http",
            "source": "http://127.0.0.1:9/nothing",
            "authorised": True,
        }
    )
    result = camera_service.test(created["id"])
    assert result["ok"] is False
    assert "Could not open" in result["message"] or "no frames" in result["message"].lower()


def test_camera_delete_removes_row(client):
    created = camera_service.create(
        {"name": "Temp", "source_type": "webcam", "device_index": 1, "authorised": True}
    )
    assert client.delete(f"/api/cameras/{created['id']}").status_code == 200
    assert client.get("/api/cameras").json()["items"] == []


def test_localhost_middleware_is_wired_into_the_app(client):
    middleware_classes = [m.cls for m in client.app.user_middleware]  # type: ignore[attr-defined]
    assert LocalhostOnlyMiddleware in middleware_classes
