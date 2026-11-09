-- CivicWatch AI Studio schema, migration 2 (SQLite / standalone)
-- Adds the cloud-connector columns and tables. Purely additive: an existing
-- standalone database keeps every row and gains the new fields in place.

ALTER TABLE incidents ADD COLUMN client_incident_id TEXT;
ALTER TABLE incidents ADD COLUMN origin TEXT NOT NULL DEFAULT 'local';
ALTER TABLE incidents ADD COLUMN connector_id TEXT;
ALTER TABLE incidents ADD COLUMN snapshot_key TEXT;
ALTER TABLE incidents ADD COLUMN clip_key TEXT;
ALTER TABLE incidents ADD COLUMN preview_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_incidents_client_ref
    ON incidents(client_incident_id);

ALTER TABLE media ADD COLUMN storage_key TEXT;
ALTER TABLE media ADD COLUMN content_type TEXT;
ALTER TABLE media ADD COLUMN origin TEXT NOT NULL DEFAULT 'local';
ALTER TABLE media ADD COLUMN upload_status TEXT NOT NULL DEFAULT 'ready';
ALTER TABLE media ADD COLUMN uploaded_by TEXT;

ALTER TABLE jobs ADD COLUMN origin TEXT NOT NULL DEFAULT 'local';
ALTER TABLE jobs ADD COLUMN connector_id TEXT;
ALTER TABLE jobs ADD COLUMN source_key TEXT;
ALTER TABLE jobs ADD COLUMN assigned_at TEXT;
ALTER TABLE jobs ADD COLUMN data_json TEXT;

CREATE INDEX IF NOT EXISTS idx_jobs_origin ON jobs(origin, status);

-- Local connector registrations (token hashes only, never the token itself).
CREATE TABLE IF NOT EXISTS connectors (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    token_hash        TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    last_seen_at      TEXT,
    version           TEXT,
    device            TEXT,
    capabilities_json TEXT,
    models_json       TEXT,
    status            TEXT NOT NULL DEFAULT 'pending',
    notes             TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_connectors_token ON connectors(token_hash);

-- Models a connector reports as installed. Used so the cloud UI can say
-- "available on connector" without ever claiming a model it does not have.
CREATE TABLE IF NOT EXISTS connector_models (
    id           TEXT PRIMARY KEY,
    connector_id TEXT NOT NULL REFERENCES connectors(id) ON DELETE CASCADE,
    family       TEXT NOT NULL,
    name         TEXT NOT NULL,
    classes_json TEXT,
    license      TEXT,
    installed_at TEXT NOT NULL
);

-- Durable outbox used by the local connector. Created in both backends so the
-- same migration set runs everywhere; the cloud simply never uses it.
CREATE TABLE IF NOT EXISTS connector_outbox (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id    TEXT NOT NULL UNIQUE,
    payload_json   TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    attempts       INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_error     TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    sent_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_due ON connector_outbox(status, next_attempt_at);
