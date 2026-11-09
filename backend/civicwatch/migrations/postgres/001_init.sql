-- CivicWatch AI Studio schema, migration 1 (PostgreSQL / Supabase)
-- Mirrors migrations/sqlite/001_init.sql column-for-column so the same
-- service layer runs against either backend.

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media (
    id            TEXT PRIMARY KEY,
    original_name TEXT NOT NULL,
    stored_name   TEXT NOT NULL,
    size_bytes    BIGINT NOT NULL,
    duration_s    DOUBLE PRECISION,
    width         INTEGER,
    height        INTEGER,
    fps           DOUBLE PRECISION,
    frame_count   INTEGER,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT PRIMARY KEY,
    media_id         TEXT REFERENCES media(id) ON DELETE CASCADE,
    status           TEXT NOT NULL,
    device           TEXT,
    model_id         TEXT,
    sample_every     INTEGER NOT NULL DEFAULT 5,
    imgsz            INTEGER NOT NULL DEFAULT 640,
    frames_total     INTEGER NOT NULL DEFAULT 0,
    frames_processed INTEGER NOT NULL DEFAULT 0,
    progress         DOUBLE PRECISION NOT NULL DEFAULT 0,
    detections       INTEGER NOT NULL DEFAULT 0,
    incidents        INTEGER NOT NULL DEFAULT 0,
    error            TEXT,
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT,
    runtime_s        DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cameras (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    source_type    TEXT NOT NULL,
    source_value   TEXT NOT NULL,
    location       TEXT,
    notes          TEXT,
    authorised     INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    last_status    TEXT NOT NULL DEFAULT 'idle',
    last_error     TEXT
);

CREATE TABLE IF NOT EXISTS zones (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    scope_type  TEXT NOT NULL DEFAULT 'all',
    scope_id    TEXT,
    polygon     TEXT NOT NULL,
    rules       TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
    id            TEXT PRIMARY KEY,
    category      TEXT NOT NULL,
    label         TEXT NOT NULL,
    confidence    DOUBLE PRECISION NOT NULL,
    camera        TEXT NOT NULL,
    location      TEXT,
    captured_at   TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    track_id      INTEGER,
    zone_id       TEXT,
    zone_name     TEXT,
    dwell_seconds DOUBLE PRECISION,
    status        TEXT NOT NULL DEFAULT 'detected',
    severity      TEXT NOT NULL DEFAULT 'medium',
    source_type   TEXT NOT NULL,
    source_ref    TEXT NOT NULL,
    class_name    TEXT,
    snapshot_path TEXT,
    clip_path     TEXT,
    evidence_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_incidents_captured ON incidents(captured_at);
CREATE INDEX IF NOT EXISTS idx_incidents_category ON incidents(category);

CREATE TABLE IF NOT EXISTS incident_events (
    id          BIGSERIAL PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    status      TEXT,
    note        TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_incident_events ON incident_events(incident_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id         BIGSERIAL PRIMARY KEY,
    created_at TEXT NOT NULL,
    actor      TEXT,
    action     TEXT NOT NULL,
    detail     TEXT
);

CREATE TABLE IF NOT EXISTS custom_models (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL,
    filename     TEXT NOT NULL,
    license      TEXT,
    source       TEXT,
    installed_at TEXT NOT NULL,
    metadata     TEXT
);
