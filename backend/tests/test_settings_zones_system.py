"""Settings validation, zone persistence/scoping and system diagnostics."""

import pytest

from civicwatch.detect.registry import validate_weights
from civicwatch.settings_store import DEFAULT_SETTINGS, all_settings, update_settings


def test_defaults_are_local_first():
    settings = all_settings(refresh=True)
    assert settings["device_preference"] in {"auto", "mps", "cpu"}
    assert settings["evidence_retention_days"] >= 1
    assert settings["store_snapshots"] is True
    assert settings["blur_persons"] is False


def test_settings_update_and_coercion(client):
    response = client.patch(
        "/api/settings", json={"confidence": 0.55, "sample_every": "7", "device_preference": "cpu"}
    )
    assert response.status_code == 200
    values = response.json()["values"]
    assert values["confidence"] == pytest.approx(0.55)
    assert values["sample_every"] == 7
    assert values["device_preference"] == "cpu"
    assert client.get("/api/settings").json()["values"]["confidence"] == pytest.approx(0.55)


def test_settings_validation_errors(client):
    assert client.patch("/api/settings", json={"confidence": 1.5}).status_code == 400
    assert client.patch("/api/settings", json={"device_preference": "gpu"}).status_code == 400
    assert client.patch("/api/settings", json={"sample_every": 0}).status_code == 400
    assert client.patch("/api/settings", json={"unknown_key": 1}).status_code == 400
    assert client.post("/api/settings/reset/confidence").status_code == 200
    assert all_settings(refresh=True)["confidence"] == DEFAULT_SETTINGS["confidence"]


def test_zone_crud_and_validation(client):
    kinds = client.get("/api/zones/kinds").json()["items"]
    assert {kind["value"] for kind in kinds} == {"no_parking", "footpath", "road", "restricted"}

    created = client.post(
        "/api/zones",
        json={
            "name": "Footpath north",
            "kind": "footpath",
            "polygon": [[0.1, 0.6], [0.5, 0.6], [0.5, 0.9], [0.1, 0.9]],
            "rules": {"dwell_seconds": 4, "classes": ["car", "motorcycle"]},
        },
    )
    assert created.status_code == 200, created.text
    zone = created.json()
    assert zone["kind"] == "footpath"
    assert zone["rules"]["dwell_seconds"] == 4.0
    assert zone["scope_type"] == "all"
    assert zone["enabled"] is True

    updated = client.patch(f"/api/zones/{zone['id']}", json={"name": "Footpath south", "enabled": False})
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False

    assert client.patch(f"/api/zones/{zone['id']}", json={"kind": "volcano"}).status_code == 400
    assert client.post(
        "/api/zones",
        json={"name": "Bad", "kind": "no_parking", "polygon": [[0.1, 0.1], [0.2, 0.2]]},
    ).status_code == 400
    assert client.delete(f"/api/zones/{zone['id']}").status_code == 200
    assert client.get("/api/zones").json()["items"] == []


def test_zone_scope_filtering(client):
    client.post(
        "/api/zones",
        json={
            "name": "Camera only",
            "kind": "no_parking",
            "polygon": [[0.1, 0.1], [0.6, 0.1], [0.6, 0.5], [0.1, 0.5]],
            "rules": {"dwell_seconds": 10, "classes": ["car"]},
            "scope_type": "camera",
            "scope_id": "cam-1",
        },
    )
    client.post(
        "/api/zones",
        json={
            "name": "Global",
            "kind": "footpath",
            "polygon": [[0.1, 0.5], [0.6, 0.5], [0.6, 0.9], [0.1, 0.9]],
            "rules": {"dwell_seconds": 5, "classes": ["car"]},
            "scope_type": "all",
        },
    )
    from civicwatch.pipeline.rules import ZoneSpec
    from civicwatch.services.zones import ZoneService

    camera_zones = ZoneService.for_source("camera", "cam-1")
    other_camera = ZoneService.for_source("camera", "cam-2")
    video_zones = ZoneService.for_source("media", "media-1")
    assert {zone.name for zone in camera_zones} == {"Camera only", "Global"}
    assert {zone.name for zone in other_camera} == {"Global"}
    assert {zone.name for zone in video_zones} == {"Global"}
    assert all(isinstance(zone, ZoneSpec) for zone in camera_zones)
    assert camera_zones[0].classes == {"car"}


def test_system_and_overview_endpoints(client):
    info = client.get("/api/system/info").json()
    assert info["app"]["local_only"] is True
    assert info["privacy"]["facial_recognition"] is False
    assert "device" in info

    overview = client.get("/api/overview").json()
    assert overview["incidents"]["total"] == 0
    assert overview["monitoring"]["model_id"]
    assert {category["category"] for category in overview["categories"]} == {"pothole", "garbage"}
    assert all(not category["installed"] for category in overview["categories"])

    storage = client.get("/api/storage").json()
    assert storage["bytes"]["total"] >= 0
    assert "free_gb" in storage


def test_diagnostics_reports_required_checks(client):
    diagnostics = client.get("/api/system/diagnostics").json()
    names = {check["name"] for check in diagnostics["checks"]}
    assert {"Python runtime", "OpenCV", "PyTorch", "Detection model", "_placeholder"} - {
        "_placeholder"
    } <= names
    assert isinstance(diagnostics["ok"], bool)
    assert diagnostics["device"]["device"] in {"cpu", "mps", "cuda:0"}


def test_weight_validation_rejects_garbage(tmp_path):
    bogus = tmp_path / "bad.pt"
    bogus.write_bytes(b"not a checkpoint")
    with pytest.raises(ValueError):
        validate_weights(bogus)
    with pytest.raises(ValueError):
        validate_weights(tmp_path / "missing.pt")


def test_audit_log_records_changes(client):
    client.patch("/api/settings", json={"confidence": 0.4})
    entries = client.get("/api/audit").json()["items"]
    assert any(entry["action"] == "settings.updated" for entry in entries)


def test_force_redownload_replaces_weights_atomically(monkeypatch):
    """An update must not break a working install until the new file validates."""
    import time

    from civicwatch.detect import registry

    target = registry.weights_path("yolov8n")
    target.parent.mkdir(parents=True, exist_ok=True)
    original = target.read_bytes() if target.exists() else b"installed"
    payload = b"freshly-downloaded-weights"
    target.write_bytes(b"installed")

    class FakeResponse:
        headers = {"Content-Length": str(len(payload))}

        def __init__(self) -> None:
            self._sent = False

        def read(self, _size: int = -1) -> bytes:
            if self._sent:
                return b""
            self._sent = True
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(registry.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    monkeypatch.setattr(
        registry,
        "validate_weights",
        lambda path: {"classes": {0: "car"}, "sha256": "x", "bytes": path.stat().st_size},
    )
    try:
        state = registry.download_model("yolov8n", force=True)
        assert state.status in {"downloading", "verifying"}
        for _ in range(100):
            if state.status not in {"downloading", "verifying"}:
                break
            time.sleep(0.05)
        assert state.status == "ready", state.error
        assert target.read_bytes() == payload

        # Without force, an installed model is reported ready and is not rewritten.
        target.write_bytes(b"untouched")
        again = registry.download_model("yolov8n")
        assert again.status == "ready"
        assert target.read_bytes() == b"untouched"
    finally:
        target.write_bytes(original)
        registry._downloads.pop("yolov8n", None)
