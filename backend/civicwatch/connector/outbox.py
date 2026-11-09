"""Durable outbox for incident sync (SQLite, survives restarts and outages).

Every local incident is copied into ``connector_outbox`` exactly once. The
unique ``incident_id`` column is the duplicate guard; the cloud performs its own
de-duplication on ``client_incident_id`` as a second line of defence.

Retries use exponential backoff (5s → 15s → 45s → … capped at 15 minutes) and
``next_attempt_at``, so a laptop that was offline for a day drains its backlog
as soon as connectivity returns without hammering the endpoint.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from ..db import execute, query, query_one, utc_now
from ..storage import evidence_keys

BACKOFF_SECONDS = (5, 15, 45, 120, 300, 900)
MAX_ATTEMPTS_BEFORE_STALE_ALERT = 8


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue_incident(incident: dict[str, Any]) -> bool:
    """Queue one local incident. Returns False when it was already queued."""
    incident_id = incident.get("id")
    if not incident_id:
        raise ValueError("Incident has no id")
    if query_one("SELECT id FROM connector_outbox WHERE incident_id = ?", (incident_id,)):
        return False
    refs = evidence_keys(incident)
    payload = {
        "client_incident_id": incident_id,
        "category": incident.get("category"),
        "confidence": float(incident.get("confidence") or 0),
        "camera": incident.get("camera") or "Local camera",
        "location": incident.get("location"),
        "captured_at": incident.get("captured_at") or utc_now(),
        "track_id": incident.get("track_id"),
        "zone_id": incident.get("zone_id"),
        "zone_name": incident.get("zone_name"),
        "dwell_seconds": incident.get("dwell_seconds"),
        "class_name": incident.get("class_name"),
        "severity": incident.get("severity") or "medium",
        "source_type": incident.get("source_type") or "video",
        "source_ref": incident.get("source_ref"),
        "metadata": {
            "local_origin": incident.get("origin") or "local",
            "connector_job": incident.get("source_ref") if incident.get("source_type") == "video" else None,
            "evidence": {
                key: value for key, value in (incident.get("evidence") or {}).items()
                if key in {"model_id", "zone", "dwell_seconds", "box"}
            },
        },
        "evidence_local": {name: ref for name, ref in refs.items() if ref},
    }
    now = utc_now()
    execute(
        "INSERT INTO connector_outbox (incident_id, payload_json, status, attempts, "
        "next_attempt_at, created_at, updated_at) VALUES (?, ?, 'pending', 0, ?, ?, ?)",
        (incident_id, json.dumps(payload, default=str), now, now, now),
    )
    return True


def enqueue_unsynced(limit: int = 200) -> int:
    """Find local incidents that were never queued (e.g. created before setup)."""
    rows = query(
        "SELECT i.* FROM incidents i LEFT JOIN connector_outbox o ON o.incident_id = i.id "
        "WHERE o.id IS NULL AND (i.origin IS NULL OR i.origin = 'local') "
        "ORDER BY i.created_at ASC LIMIT ?",
        (limit,),
    )
    queued = 0
    for row in rows:
        if enqueue_incident(dict(row)):
            queued += 1
    return queued


def due(limit: int = 20) -> list[dict[str, Any]]:
    now = utc_now()
    rows = query(
        "SELECT * FROM connector_outbox WHERE status IN ('pending', 'failed') "
        "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
        "ORDER BY created_at ASC LIMIT ?",
        (now, max(1, limit)),
    )
    return [dict(row) for row in rows]


def mark_sent(outbox_id: int) -> None:
    now = utc_now()
    execute(
        "UPDATE connector_outbox SET status = 'sent', sent_at = ?, updated_at = ?, "
        "last_error = NULL WHERE id = ?",
        (now, now, outbox_id),
    )


def mark_duplicate(outbox_id: int, cloud_incident_id: str | None = None) -> None:
    now = utc_now()
    execute(
        "UPDATE connector_outbox SET status = 'sent', sent_at = ?, updated_at = ?, "
        "last_error = ? WHERE id = ?",
        (now, now, f"already present in cloud as {cloud_incident_id}" if cloud_incident_id else "duplicate", outbox_id),
    )


def mark_failed(outbox_id: int, error: str, attempts: int) -> str:
    """Record a failure and schedule the next attempt."""
    delay = BACKOFF_SECONDS[min(max(attempts, 1) - 1, len(BACKOFF_SECONDS) - 1)]
    next_at = (_now() + timedelta(seconds=delay)).isoformat(timespec="seconds")
    execute(
        "UPDATE connector_outbox SET status = 'failed', attempts = ?, next_attempt_at = ?, "
        "last_error = ?, updated_at = ? WHERE id = ?",
        (attempts, next_at, error[:500], utc_now(), outbox_id),
    )
    return next_at


def pending_count() -> int:
    row = query_one("SELECT COUNT(*) AS n FROM connector_outbox WHERE status != 'sent'")
    return int((row or {}).get("n") or 0)


def stats() -> dict[str, Any]:
    rows = query("SELECT status, COUNT(*) AS n, MAX(attempts) AS attempts FROM connector_outbox GROUP BY status")
    by_status = {row["status"]: int(row["n"]) for row in rows}
    failures = query_one(
        "SELECT last_error, attempts, next_attempt_at FROM connector_outbox "
        "WHERE status = 'failed' ORDER BY updated_at DESC LIMIT 1"
    )
    last_error = dict(failures) if failures else None
    return {
        "pending": int(by_status.get("pending", 0)),
        "failed": int(by_status.get("failed", 0)),
        "sent": int(by_status.get("sent", 0)),
        "total": sum(by_status.values()),
        "by_status": by_status,
        "last_error": last_error,
        "stale": bool(last_error and int(last_error.get("attempts") or 0) >= MAX_ATTEMPTS_BEFORE_STALE_ALERT),
    }


def purge_sent(keep_days: int = 14) -> int:
    cutoff = (_now() - timedelta(days=keep_days)).isoformat(timespec="seconds")
    rows = query(
        "SELECT id FROM connector_outbox WHERE status = 'sent' AND sent_at IS NOT NULL AND sent_at < ?",
        (cutoff,),
    )
    for row in rows:
        execute("DELETE FROM connector_outbox WHERE id = ?", (row["id"],))
    return len(rows)


def incident_payload(row: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(row["payload_json"])
    except (TypeError, ValueError):
        return {}


def iter_pending(limit: int = 100) -> Iterable[dict[str, Any]]:
    rows = query(
        "SELECT * FROM connector_outbox WHERE status != 'sent' ORDER BY created_at ASC LIMIT ?",
        (limit,),
    )
    return [dict(row) for row in rows]
