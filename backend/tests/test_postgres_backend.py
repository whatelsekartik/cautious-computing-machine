"""PostgreSQL backend tests against a real PostgreSQL server.

``pgserver`` ships a relocatable PostgreSQL build, so the suite exercises real
SQL (migrations, constraints, ILIKE search, JSON text columns) instead of a
mocked driver. If the binary is unavailable the tests skip with a clear reason —
they are never silently "passed".
"""

from __future__ import annotations

import glob
from datetime import datetime, timezone

import pytest

from civicwatch.db_common import translate_sql
from civicwatch.services.incidents import IncidentDraft, IncidentService

TABLES = (
    "incident_events",
    "incidents",
    "connector_models",
    "connector_outbox",
    "connectors",
    "jobs",
    "media",
    "zones",
    "cameras",
    "custom_models",
    "settings",
    "audit_log",
)


@pytest.fixture(scope="module")
def pg_url(tmp_path_factory):
    pgserver = pytest.importorskip("pgserver", reason="pgserver provides a bundled PostgreSQL")
    data_dir = tmp_path_factory.mktemp("pgdata")
    try:
        server = pgserver.get_server(str(data_dir), cleanup_mode=None)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL could not start here: {exc}")
    sockets = [
        path
        for path in glob.glob(str(data_dir / ".s.PGSQL.*"))
        if not path.endswith(".lock")
    ]
    if not sockets:
        pytest.skip("PostgreSQL socket was not created")
    url = f"postgresql://postgres:@/postgres?host={sockets[0]}"
    yield url
    server.cleanup()


@pytest.fixture
def postgres_db(monkeypatch, pg_url):
    monkeypatch.setenv("CIVICWATCH_DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", pg_url)
    from civicwatch import db as db_module
    from civicwatch import settings_store
    from civicwatch.storage import reset_storage

    db_module.reset_connections()
    settings_store._cache = None
    reset_storage()
    db_module.init_db()
    for table in TABLES:
        db_module.execute(f"DELETE FROM {table}")
    settings_store._cache = None
    yield db_module
    db_module.reset_connections()
    db_module.init_db()
    settings_store._cache = None


def test_migrations_apply_once_and_match_the_sqlite_schema(postgres_db):
    assert postgres_db.backend_name() == "postgres"
    # Applied by the fixture already; a second call must be a no-op.
    assert postgres_db.init_db() == []
    versions = [row["version"] for row in postgres_db.query("SELECT version FROM schema_migrations ORDER BY version")]
    assert versions == [1, 2]

    tables = {
        row["table_name"]
        for row in postgres_db.query(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    }
    for required in ("incidents", "incident_events", "jobs", "media", "connectors", "connector_models"):
        assert required in tables

    columns = {
        row["column_name"]
        for row in postgres_db.query(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'incidents'"
        )
    }
    # Migration 2 columns exist alongside the original incident schema.
    assert {"client_incident_id", "snapshot_key", "clip_key", "preview_key"} <= columns
    assert {"category", "confidence", "captured_at", "status", "severity"} <= columns


def test_incident_workflow_runs_on_postgres(postgres_db):
    draft = IncidentDraft(
        id="INC-20261108-8001",
        category="illegal_parking",
        confidence=0.87,
        camera="Ward 4 · Station Road",
        location="Outside the school gate",
        captured_at="2026-11-08T09:15:00+00:00",
        source_type="video",
        source_ref="media-42",
        class_name="car",
        track_id=17,
        zone_name="No parking",
        dwell_seconds=22.5,
        snapshot_path="evidence/INC-20261108-8001/snapshot.jpg",
        clip_path="evidence/INC-20261108-8001/clip.mp4",
    )
    created = IncidentService.create(draft)
    assert created["id"] == draft.id
    assert created["status"] == "detected"
    assert created["has_snapshot"] is False  # local file does not exist in this test

    fetched = IncidentService.get(draft.id)
    assert fetched and fetched["camera"].startswith("Ward 4")
    assert "disclaimer" in fetched["evidence"]
    assert fetched["evidence_refs"]["snapshot"].endswith("snapshot.jpg")

    # Case-insensitive search works because the adapter maps LIKE → ILIKE.
    assert len(IncidentService.list(search="STATION ROAD")["items"]) == 1
    assert len(IncidentService.list(search="nothing-matches")["items"]) == 0

    # Filters, ordering and pagination run on real SQL.
    listing = IncidentService.list(status="detected", limit=10, offset=0, sort="confidence_desc")
    assert listing["total"] == 1 and listing["items"][0]["confidence"] == 0.87

    updated = IncidentService.update_status(draft.id, "verified", note="Confirmed by officer", actor="ops@example")
    assert updated["status"] == "verified"
    events = postgres_db.query(
        "SELECT status, note FROM incident_events WHERE incident_id = ? ORDER BY id", (draft.id,)
    )
    assert [row["status"] for row in events] == ["detected", "verified"]

    stats = IncidentService.stats()
    assert stats["total"] == 1 and stats["by_status"]["verified"] == 1

    csv_text = IncidentService.csv_rows()
    assert draft.id in csv_text and "Potential illegal parking" in csv_text

    IncidentService.delete(draft.id)
    assert IncidentService.get(draft.id) is None
    assert postgres_db.query_one("SELECT COUNT(*) AS n FROM incident_events")["n"] == 0


def test_reserve_id_is_sequential_on_postgres(postgres_db):
    first = IncidentService.reserve_id(datetime(2026, 11, 8, tzinfo=timezone.utc))
    draft = IncidentDraft(
        id=first,
        category="restricted_entry",
        confidence=0.5,
        camera="Gate",
        location=None,
        captured_at="2026-11-08T10:00:00+00:00",
        source_type="live",
        source_ref="camera-1",
    )
    IncidentService.create(draft)
    second = IncidentService.reserve_id(datetime(2026, 11, 8, tzinfo=timezone.utc))
    assert first == "INC-20261108-0001"
    assert second == "INC-20261108-0002"


def test_connector_ingestion_and_deduplication_on_postgres(postgres_db):
    from civicwatch.cloud import service

    created = service.create_connector("Test yard Mac")
    assert created["token"].startswith("cwconn_")
    connector = service.authenticate_connector(created["token"])
    assert connector and connector["id"] == created["id"]

    payload = {
        "client_incident_id": "INC-20261108-7001",
        "category": "footpath_obstruction",
        "confidence": 0.66,
        "camera": "Ward 2 · Market",
        "captured_at": "2026-11-08T11:00:00+00:00",
        "source_type": "live",
        "evidence": {"snapshot_key": "evidence/INC-20261108-7001/snapshot.jpg"},
    }
    first = service.ingest_incidents(connector, [payload])
    assert len(first["accepted"]) == 1
    cloud_id = first["accepted"][0]["incident_id"]

    second = service.ingest_incidents(connector, [payload])
    assert second["accepted"] == []
    assert second["duplicates"][0]["incident_id"] == cloud_id
    assert postgres_db.query_one("SELECT COUNT(*) AS n FROM incidents")["n"] == 1

    row = IncidentService.get(cloud_id)
    assert row["origin"] == "connector"
    assert row["connector_id"] == connector["id"]
    assert row["snapshot_key"] == "evidence/INC-20261108-7001/snapshot.jpg"

    # A payload that tries to carry camera credentials is rejected, not stored.
    bad = dict(payload, client_incident_id="INC-20261108-7002", camera="rtsp://admin:secret@10.0.0.5/live")
    result = service.ingest_incidents(connector, [bad])
    assert result["rejected"] and "credentials" in result["rejected"][0]["reason"]
    assert postgres_db.query_one("SELECT COUNT(*) AS n FROM incidents")["n"] == 1

    service.delete_connector(connector["id"])
    assert service.authenticate_connector(created["token"]) is None


def test_database_url_is_reported_without_the_password():
    from civicwatch.db_postgres import describe_url, parse_url

    url = "postgresql://civicwatch:s3cret-pass@db.example.com:5432/civicwatch?sslmode=require"
    info = parse_url(url)
    assert info["user"] == "civicwatch"
    assert info["host"] == "db.example.com"
    assert info["sslmode"] == "require"
    described = describe_url(url)
    assert "s3cret-pass" not in described
    assert "db.example.com" in described and "civicwatch" in described


def test_sql_translation_keeps_placeholders_and_search_parity():
    assert translate_sql("SELECT * FROM incidents WHERE id = ?") == "SELECT * FROM incidents WHERE id = %s"
    assert translate_sql("SELECT * FROM incidents WHERE camera LIKE ?") == (
        "SELECT * FROM incidents WHERE camera ILIKE %s"
    )
    # Literals and quoted text are untouched: a '?' inside a string is not a
    # placeholder, and the word "like" inside a literal is not an operator.
    assert translate_sql("SELECT '?' AS q, note FROM t WHERE note = 'like this'") == (
        "SELECT '?' AS q, note FROM t WHERE note = 'like this'"
    )
    assert translate_sql("PRAGMA journal_mode = WAL;") == ""
