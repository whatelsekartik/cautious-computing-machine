"""Incident service + HTTP workflow, exports and evidence access control."""

from pathlib import Path

import pytest

from civicwatch.config import get_paths
from civicwatch.pipeline.rules import Violation
from civicwatch.services.incidents import IncidentService, IncidentDraft


def make_incident(category: str = "illegal_parking", camera: str = "Demo camera") -> dict:
    violation = Violation(
        category=category,
        zone_id="zone-1",
        zone_name="Market Road no parking",
        track_id=12,
        class_name="car",
        confidence=0.78,
        dwell_seconds=21.4,
        severity="medium",
        box=(10, 20, 110, 140),
    )
    incident_id = IncidentService.reserve_id()
    return IncidentService.create_from_violation(
        violation,
        incident_id=incident_id,
        camera=camera,
        location="Market Road",
        captured_at="2026-03-04T09:15:00+00:00",
        source_type="video",
        source_ref="media-1",
        snapshot_path=None,
        clip_path=None,
    )


def test_reserve_id_is_sequential_and_unique():
    first = IncidentService.reserve_id()
    IncidentService.create(
        IncidentDraft(
            id=first,
            category="illegal_parking",
            confidence=0.5,
            camera="cam",
            location=None,
            captured_at="2026-03-04T09:00:00+00:00",
            source_type="video",
            source_ref="m1",
        )
    )
    second = IncidentService.reserve_id()
    assert first != second
    assert second.endswith("0002")


def test_status_workflow_records_events():
    incident = make_incident()
    assert incident["status"] == "detected"
    updated = IncidentService.update_status(incident["id"], "pending_review", "Queued")
    assert updated["status"] == "pending_review"
    verified = IncidentService.update_status(incident["id"], "verified", "Plate visible, obstruction clear")
    assert verified["status"] == "verified"
    notes = [event["note"] for event in verified["events"]]
    assert "Plate visible, obstruction clear" in notes
    with pytest.raises(ValueError):
        IncidentService.update_status(incident["id"], "bogus")


def test_filters_search_and_stats():
    make_incident("illegal_parking", "North junction")
    make_incident("footpath_obstruction", "South junction")
    make_incident("double_parking", "North junction")

    listing = IncidentService.list(status="detected")
    assert listing["total"] == 3
    assert IncidentService.list(search="North")["total"] == 2
    assert IncidentService.list(category="footpath_obstruction")["total"] == 1
    assert IncidentService.list(source_ref="media-1")["total"] == 3
    stats = IncidentService.stats()
    assert stats["total"] == 3
    assert stats["pending_review"] == 3
    assert stats["by_category"]["illegal_parking"] == 1


def test_http_incident_endpoints(client):
    incident = make_incident()
    response = client.get("/api/incidents")
    assert response.status_code == 200
    assert response.json()["total"] == 1

    detail = client.get(f"/api/incidents/{incident['id']}")
    assert detail.status_code == 200
    assert detail.json()["disclaimer"].startswith("Automated output")

    update = client.patch(
        f"/api/incidents/{incident['id']}", json={"status": "dismissed", "note": "Parked legally"}
    )
    assert update.status_code == 200
    assert update.json()["status"] == "dismissed"

    note = client.post(f"/api/incidents/{incident['id']}/notes", json={"note": "Re-checked"})
    assert note.status_code == 200
    assert note.json()["events"][-1]["note"] == "Re-checked"

    bad = client.patch(f"/api/incidents/{incident['id']}", json={"status": "nonsense"})
    assert bad.status_code == 400
    missing = client.get("/api/incidents/INC-19990101-9999")
    assert missing.status_code == 404

    csv = client.get("/api/incidents/export.csv")
    assert csv.status_code == 200
    assert "Incident ID" in csv.text
    assert "Potential illegal parking" in csv.text

    pdf = client.get("/api/incidents/export.pdf")
    assert pdf.status_code == 200
    assert pdf.content[:4] == b"%PDF"


def test_evidence_paths_outside_the_evidence_folder_are_not_served(client):
    outside = get_paths().data / "secret.jpg"
    outside.write_bytes(b"not-an-image")
    incident = IncidentService.create(
        IncidentDraft(
            id=IncidentService.reserve_id(),
            category="illegal_parking",
            confidence=0.6,
            camera="cam",
            location=None,
            captured_at="2026-03-04T09:00:00+00:00",
            source_type="video",
            source_ref="m1",
            snapshot_path=str(outside),
        )
    )
    response = client.get(f"/api/incidents/{incident['id']}/snapshot")
    assert response.status_code == 404


def test_evidence_is_served_when_inside_the_evidence_folder(client):
    incident = make_incident()
    folder = get_paths().evidence / incident["id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "snapshot.jpg").write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    IncidentService.create(
        IncidentDraft(
            id=incident["id"] + "-b",
            category="illegal_parking",
            confidence=0.6,
            camera="cam",
            location=None,
            captured_at="2026-03-04T09:00:00+00:00",
            source_type="video",
            source_ref="m1",
            snapshot_path=str(folder / "snapshot.jpg"),
        )
    )
    response = client.get(f"/api/incidents/{incident['id']}-b/snapshot")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"


def test_deleting_an_incident_removes_evidence(client):
    incident = make_incident()
    folder = get_paths().evidence / incident["id"]
    folder.mkdir(parents=True, exist_ok=True)
    snapshot = folder / "snapshot.jpg"
    snapshot.write_bytes(b"jpeg")
    IncidentService.update_status(incident["id"], "detected")
    from civicwatch.db import execute

    execute("UPDATE incidents SET snapshot_path = ? WHERE id = ?", (str(snapshot), incident["id"]))
    client.delete(f"/api/incidents/{incident['id']}")
    assert not snapshot.exists()
    assert IncidentService.get(incident["id"]) is None


def test_report_contains_large_registers(client):
    for _ in range(3):
        make_incident()
    pdf = client.get("/api/incidents/export.pdf")
    assert pdf.status_code == 200
    assert len(pdf.content) > 2000
    assert Path(get_paths().data / "exports").exists()
