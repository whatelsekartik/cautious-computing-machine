"""Business logic for the hosted dashboard and the connector protocol.

Everything here is safe to run inside a serverless function: no torch, no
OpenCV, no long loops. Video processing is performed by the local connector,
which sends incidents, metadata and evidence keys here over authenticated
HTTPS.
"""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from ..config import APP_NAME, APP_VERSION, SAFETY_DISCLAIMER
from ..db import audit, execute, query, query_one, utc_now
from ..detect import registry
from ..labels import CATEGORY_LABELS
from ..services.incidents import IncidentDraft, IncidentService
from ..settings_store import all_settings, update_settings
from ..storage import StorageError, get_storage, ref_available, storage_info
from .auth import (
    FORBIDDEN_PAYLOAD_KEYS,
    cloud_mode,
    contains_credentials,
    hash_token,
    new_connector_token,
    sanitize_label,
)

ONLINE_WINDOW_SECONDS = 120
MAX_ASSIGNMENTS_PER_POLL = 3
EVIDENCE_FILES = {
    "snapshot": ("snapshot.jpg", "image/jpeg"),
    "clip": ("clip.mp4", "video/mp4"),
    "preview": ("preview.gif", "image/gif"),
}
ALLOWED_MODEL_FAMILIES = {"vehicle_pedestrian", "pothole", "garbage"}


class CloudError(RuntimeError):
    """Bad request from a connector or dashboard client."""


# ---------------------------------------------------------------------------
# Connectors
# ---------------------------------------------------------------------------

def create_connector(name: str, notes: str | None = None) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise CloudError("Connector name is required")
    token = new_connector_token()
    record_id = f"CON-{uuid.uuid4().hex[:10]}"
    now = utc_now()
    execute(
        "INSERT INTO connectors (id, name, token_hash, created_at, status, notes) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (record_id, name[:80], hash_token(token), now, "pending", (notes or "")[:500] or None),
    )
    audit("connector.created", {"id": record_id, "name": name}, actor="dashboard")
    row = query_one("SELECT * FROM connectors WHERE id = ?", (record_id,))
    result = public_connector(row)
    result["token"] = token  # shown exactly once
    return result


def list_connectors() -> list[dict[str, Any]]:
    return [public_connector(row) for row in query("SELECT * FROM connectors ORDER BY created_at")]


def get_connector(connector_id: str) -> dict[str, Any] | None:
    return query_one("SELECT * FROM connectors WHERE id = ?", (connector_id,))


def delete_connector(connector_id: str) -> None:
    if not get_connector(connector_id):
        raise KeyError("Connector not found")
    execute("DELETE FROM connector_models WHERE connector_id = ?", (connector_id,))
    execute("DELETE FROM connectors WHERE id = ?", (connector_id,))
    audit("connector.deleted", {"id": connector_id}, actor="dashboard")


def authenticate_connector(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    return query_one("SELECT * FROM connectors WHERE token_hash = ?", (hash_token(token),))


def public_connector(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    last_seen = row.get("last_seen_at")
    online = False
    if last_seen:
        try:
            seen = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            online = (datetime.now(timezone.utc) - seen).total_seconds() <= ONLINE_WINDOW_SECONDS
        except ValueError:
            online = False
    return {
        "id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"],
        "last_seen_at": last_seen,
        "version": row.get("version"),
        "device": row.get("device"),
        "status": row.get("status"),
        "online": online,
        "capabilities": _json(row.get("capabilities_json"), []),
        "models": _json(row.get("models_json"), {}),
        "notes": row.get("notes"),
    }


def record_heartbeat(connector: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    capabilities = payload.get("capabilities") or {}
    models = payload.get("models") or {}
    execute(
        "UPDATE connectors SET last_seen_at = ?, status = ?, version = ?, device = ?, "
        "capabilities_json = ?, models_json = ? WHERE id = ?",
        (
            now,
            "online",
            str(payload.get("version") or "")[:40] or None,
            str(payload.get("device") or "")[:40] or None,
            json.dumps(capabilities or {}),
            json.dumps(models or {}),
            connector["id"],
        ),
    )
    if payload.get("models_installed"):
        _store_models(connector["id"], payload["models_installed"])
    return {
        "ok": True,
        "server_time": now,
        "connector_id": connector["id"],
        "assignments_pending": _count_pending_assignments(),
        "config": connector_config(connector),
    }


def _store_models(connector_id: str, models: Iterable[dict[str, Any]]) -> None:
    execute("DELETE FROM connector_models WHERE connector_id = ?", (connector_id,))
    now = utc_now()
    for model in models:
        family = str(model.get("family") or "").strip().lower()
        if family not in ALLOWED_MODEL_FAMILIES:
            continue
        model_id = str(model.get("id") or "")[:80]
        if not model_id:
            continue
        classes = model.get("classes") or []
        if isinstance(classes, dict):
            classes = list(classes.values())
        execute(
            "INSERT INTO connector_models (id, connector_id, family, name, classes_json, "
            "license, installed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"{connector_id}:{model_id}",
                connector_id,
                family,
                str(model.get("name") or model_id)[:120],
                json.dumps([str(c) for c in classes][:80]),
                str(model.get("license") or "AGPL-3.0")[:40],
                str(model.get("installed_at") or now),
            ),
        )


def connector_config(connector: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = all_settings(refresh=True)
    return {
        "confidence": settings["confidence"],
        "iou_threshold": settings["iou_threshold"],
        "imgsz": settings["imgsz"],
        "sample_every": settings["sample_every"],
        "model_id": settings["model_id"],
        "max_processing_width": settings["max_processing_width"],
        "store_clips": settings["store_clips"],
        "store_snapshots": settings["store_snapshots"],
        "clip_pre_seconds": settings["clip_pre_seconds"],
        "clip_post_seconds": settings["clip_post_seconds"],
        "blur_persons": settings["blur_persons"],
        "evidence_retention_days": settings["evidence_retention_days"],
        "heartbeat_seconds": 30,
        "assignment_poll_seconds": 20,
        "upload_expiry_seconds": 900,
    }


# ---------------------------------------------------------------------------
# Models (reported by connectors, never fabricated here)
# ---------------------------------------------------------------------------

def _connector_model_rows() -> list[dict[str, Any]]:
    rows = query(
        "SELECT m.*, c.name AS connector_name, c.last_seen_at FROM connector_models m "
        "JOIN connectors c ON c.id = m.connector_id ORDER BY m.family, m.name"
    )
    return [dict(row) for row in rows]


def model_catalogue() -> dict[str, Any]:
    settings = all_settings(refresh=True)
    connector_models = _connector_model_rows()
    by_family: dict[str, list[dict[str, Any]]] = {}
    for row in connector_models:
        by_family.setdefault(row["family"], []).append(row)

    builtin = []
    for entry in registry.BUILTIN_MODELS:
        reported = [
            row for row in connector_models if row["family"] == entry["family"]
        ]
        builtin.append(
            {
                "id": entry["id"],
                "name": entry["name"],
                "filename": entry["filename"],
                "url": entry["url"],
                "size_mb": entry["size_mb"],
                "license": entry["license"],
                "family": entry["family"],
                "description": entry["description"],
                "recommended": bool(entry.get("recommended")),
                "installed": bool(reported),
                "installed_bytes": 0,
                "download": {
                    "status": "idle",
                    "progress": 0,
                    "downloaded_bytes": 0,
                    "total_bytes": 0,
                    "error": None,
                },
                "connector_id": reported[0]["connector_id"] if reported else None,
                "connector_name": reported[0]["connector_name"] if reported else None,
            }
        )

    custom = [
        {
            "id": row["id"],
            "name": row["name"],
            "category": row["family"],
            "filename": f"{row['name']}.pt",
            "license": row["license"],
            "source": "local connector",
            "installed_at": row["installed_at"],
            "installed": True,
            "enabled": True,
            "metadata": {"classes": _json(row["classes_json"], []), "connector": row["connector_name"]},
        }
        for row in connector_models
        if row["family"] in {"pothole", "garbage"}
    ]

    categories = []
    for category in ("pothole", "garbage"):
        rows = by_family.get(category, [])
        categories.append(
            {
                "category": category,
                "label": CATEGORY_LABELS.get(category, category),
                "description": (
                    "Requires dedicated weights trained for this class. "
                    "CivicWatch never infers it from the vehicle model."
                ),
                "installed": bool(rows),
                "model": (
                    {
                        "id": rows[0]["id"],
                        "name": rows[0]["name"],
                        "license": rows[0]["license"],
                        "classes": _json(rows[0]["classes_json"], []),
                    }
                    if rows
                    else None
                ),
                "connector_name": rows[0]["connector_name"] if rows else None,
            }
        )

    return {
        "builtin": builtin,
        "custom": custom,
        "categories": categories,
        "selected": settings["model_id"],
        "device": {
            "device": "connector",
            "reason": "Inference runs on the paired local connector, not on Vercel",
            "mps_available": None,
            "mps_built": None,
            "cuda_available": None,
            "torch_version": None,
            "machine": "connector",
            "arm64": None,
        },
        "downloads": {},
        "management": "connector",
    }


# ---------------------------------------------------------------------------
# Media uploads (browser → object storage, never through the function)
# ---------------------------------------------------------------------------

def create_upload(filename: str, content_type: str, size_bytes: int, actor: str = "dashboard") -> dict[str, Any]:
    filename = (filename or "video.mp4").strip()
    if size_bytes <= 0:
        raise CloudError("File size must be greater than zero")
    if size_bytes > 4 * 1024**3:
        raise CloudError("Single uploads are limited to 4 GB")
    storage = get_storage()
    if not storage.remote:
        raise CloudError(
            "Direct upload requires object storage. Set CIVICWATCH_S3_BUCKET (cloud mode)"
        )
    media_id = uuid.uuid4().hex[:12]
    suffix = Path(filename).suffix.lower()
    suffix = suffix if suffix and suffix[1:].isalnum() else ".mp4"
    key = f"media/{media_id}/source{suffix}"
    content_type = content_type or "video/mp4"
    presigned = storage.presign_put(key, content_type, expires=3600)
    now = utc_now()
    execute(
        "INSERT INTO media (id, original_name, stored_name, size_bytes, created_at, storage_key, "
        "content_type, origin, upload_status, uploaded_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            media_id,
            filename[:180],
            key.rsplit("/", 1)[-1],
            int(size_bytes),
            now,
            key,
            content_type[:80],
            "cloud",
            "pending",
            actor[:80],
        ),
    )
    audit("media.upload_started", {"id": media_id, "bytes": size_bytes}, actor=actor)
    return {"media_id": media_id, "key": key, "upload": presigned}


def complete_upload(media_id: str, actor: str = "dashboard") -> dict[str, Any]:
    media = query_one("SELECT * FROM media WHERE id = ?", (media_id,))
    if not media:
        raise KeyError("Upload not found")
    storage = get_storage()
    key = media.get("storage_key")
    if not key:
        raise CloudError("Upload has no storage key")
    if storage.remote and not storage.exists(key):
        raise CloudError(
            "The file has not reached storage yet. Finish the upload and try again."
        )
    execute("UPDATE media SET upload_status = 'ready' WHERE id = ?", (media_id,))
    audit("media.upload_complete", {"id": media_id}, actor=actor)
    return {"media_id": media_id, "status": "ready"}


def queue_job(media_id: str, actor: str = "dashboard") -> dict[str, Any]:
    media = query_one("SELECT * FROM media WHERE id = ?", (media_id,))
    if not media:
        raise KeyError("Upload not found")
    if media.get("upload_status") != "ready":
        raise CloudError("Upload is not complete yet")
    settings = all_settings(refresh=True)
    job_id = uuid.uuid4().hex[:12]
    now = utc_now()
    execute(
        "INSERT INTO jobs (id, media_id, status, device, model_id, sample_every, imgsz, "
        "progress, created_at, origin, source_key, data_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            job_id,
            media_id,
            "queued",
            None,
            settings["model_id"],
            settings["sample_every"],
            settings["imgsz"],
            0,
            now,
            "cloud",
            media.get("storage_key"),
            json.dumps({"requested_by": actor, "requested_at": now}),
        ),
    )
    audit("job.requested", {"id": job_id, "media_id": media_id}, actor=actor)
    return job_public(job_id) or {}


def _job_row(job_id: str) -> dict[str, Any] | None:
    row = query_one(
        "SELECT j.*, m.original_name AS media_name, m.duration_s AS media_duration "
        "FROM jobs j LEFT JOIN media m ON m.id = j.media_id WHERE j.id = ?",
        (job_id,),
    )
    return dict(row) if row else None


def job_public(job_id: str) -> dict[str, Any] | None:
    row = _job_row(job_id)
    return _public_job(row) if row else None


def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    rows = query(
        "SELECT j.*, m.original_name AS media_name FROM jobs j "
        "LEFT JOIN media m ON m.id = j.media_id ORDER BY j.created_at DESC LIMIT ?",
        (max(1, min(200, limit)),),
    )
    return [_public_job(dict(row)) for row in rows]


def _public_job(row: dict[str, Any]) -> dict[str, Any]:
    data = _json(row.get("data_json"), {})
    started = row.get("started_at")
    finished = row.get("finished_at")
    elapsed = float(row.get("runtime_s") or 0)
    fps = None
    processed = int(row.get("frames_processed") or 0)
    if elapsed > 0 and processed:
        fps = round(processed / elapsed, 2)
    return {
        "id": row["id"],
        "media_id": row.get("media_id"),
        "media_name": row.get("media_name") or "video",
        "status": row.get("status") or "queued",
        "progress": float(row.get("progress") or 0),
        "frames_processed": processed,
        "frames_total": int(row.get("frames_total") or 0),
        "sample_every": int(row.get("sample_every") or 5),
        "imgsz": int(row.get("imgsz") or 640),
        "detections": int(row.get("detections") or 0),
        "incidents": int(row.get("incidents") or 0),
        "device": row.get("device"),
        "model_id": row.get("model_id"),
        "error": row.get("error"),
        "started_at": started,
        "finished_at": finished,
        "elapsed_s": elapsed,
        "fps": fps,
        "eta_s": None,
        "has_preview_video": bool(data.get("processed_key")),
        "preview_frame": False,
        "annotated_duration_s": row.get("media_duration"),
        "origin": row.get("origin") or "local",
        "connector_id": row.get("connector_id"),
    }


def claim_assignments(connector: dict[str, Any], limit: int = MAX_ASSIGNMENTS_PER_POLL) -> list[dict[str, Any]]:
    storage = get_storage()
    jobs = query(
        "SELECT j.*, m.original_name AS media_name FROM jobs j "
        "JOIN media m ON m.id = j.media_id "
        "WHERE j.origin = 'cloud' AND j.status = 'queued' "
        "AND (j.connector_id IS NULL OR j.connector_id = ?) "
        "ORDER BY j.created_at ASC LIMIT ?",
        (connector["id"], max(1, min(limit, 10))),
    )
    claimed: list[dict[str, Any]] = []
    for row in jobs:
        job = dict(row)
        execute(
            "UPDATE jobs SET status = 'assigned', connector_id = ?, assigned_at = ? "
            "WHERE id = ? AND status = 'queued'",
            (connector["id"], utc_now(), job["id"]),
        )
        refreshed = _job_row(job["id"])
        if not refreshed or refreshed.get("connector_id") != connector["id"]:
            continue  # another connector won the race
        item = _public_job(refreshed)
        key = job.get("source_key")
        item["download"] = (
            {"url": storage.presign_get(key, expires=3600), "expires_in": 3600}
            if key and storage.remote
            else None
        )
        item["settings"] = connector_config(connector)
        claimed.append(item)
    if claimed:
        audit("connector.assignments_claimed", {"count": len(claimed)}, actor=connector["id"])
    return claimed


def update_assignment(connector: dict[str, Any], job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    job = _job_row(job_id)
    if not job:
        raise KeyError("Assignment not found")
    if job.get("connector_id") not in {connector["id"], None}:
        raise CloudError("Assignment belongs to another connector")
    status = str(payload.get("status") or "").lower()
    if status not in {"processing", "completed", "failed", "stopped"}:
        raise CloudError("status must be processing, completed, failed or stopped")
    now = utc_now()
    fields: list[str] = ["status = ?", "connector_id = ?"]
    params: list[Any] = [status, connector["id"]]
    if status == "processing":
        fields += ["started_at = COALESCE(started_at, ?)", "progress = ?"]
        params += [now, float(payload.get("progress") or 0)]
    else:
        fields += ["finished_at = ?", "progress = ?", "runtime_s = ?"]
        params += [
            now,
            1.0 if status == "completed" else float(payload.get("progress") or 0),
            float(payload.get("runtime_s") or 0),
        ]
    for field, key in (
        ("frames_total", "frames_total"),
        ("frames_processed", "frames_processed"),
        ("detections", "detections"),
        ("incidents", "incidents"),
    ):
        if payload.get(key) is not None:
            fields.append(f"{field} = ?")
            params.append(int(payload[key]))
    if payload.get("device"):
        fields.append("device = ?")
        params.append(str(payload["device"])[:40])
    if payload.get("error"):
        fields.append("error = ?")
        params.append(str(payload["error"])[:500])
    data = _json(job.get("data_json"), {})
    if payload.get("processed_key"):
        data["processed_key"] = str(payload["processed_key"])
    if payload.get("notes"):
        data["connector_notes"] = str(payload["notes"])[:500]
    fields.append("data_json = ?")
    params.append(json.dumps(data))
    params.append(job_id)
    execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", params)
    audit(
        "job.assignment_updated",
        {"id": job_id, "status": status, "incidents": payload.get("incidents")},
        actor=connector["id"],
    )
    return job_public(job_id) or {}


# ---------------------------------------------------------------------------
# Evidence upload URLs
# ---------------------------------------------------------------------------

def issue_upload_urls(connector: dict[str, Any], client_incident_id: str, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    client_incident_id = (client_incident_id or "").strip()
    if not client_incident_id or len(client_incident_id) > 80:
        raise CloudError("client_incident_id is required")
    if not files:
        raise CloudError("At least one evidence file is required")
    storage = get_storage()
    if not storage.remote:
        raise CloudError("Evidence uploads require configured object storage")
    issued = []
    for item in files[:3]:
        kind = str(item.get("kind") or "").lower()
        if kind not in EVIDENCE_FILES:
            raise CloudError(f"Unknown evidence kind: {kind}")
        filename, content_type = EVIDENCE_FILES[kind]
        key = f"evidence/{client_incident_id}/{filename}"
        presigned = storage.presign_put(key, content_type, expires=900)
        issued.append(
            {
                "kind": kind,
                "key": key,
                "content_type": content_type,
                "url": presigned["url"],
                "method": "PUT",
                "headers": presigned["headers"],
                "expires_in": presigned["expires_in"],
            }
        )
    audit(
        "connector.evidence_urls",
        {"client_incident_id": client_incident_id, "kinds": [i["kind"] for i in issued]},
        actor=connector["id"],
    )
    return issued


def issue_processed_url(connector: dict[str, Any], job_id: str) -> dict[str, Any]:
    """Presigned PUT for the annotated video produced by a connector assignment."""
    job = query_one("SELECT id, connector_id FROM jobs WHERE id = ?", (job_id,))
    if not job:
        raise KeyError("Assignment not found")
    if job.get("connector_id") not in {connector["id"], None}:
        raise CloudError("Assignment belongs to another connector")
    storage = get_storage()
    if not storage.remote:
        raise CloudError("Annotated video upload requires configured object storage")
    key = f"processed/{job_id}/annotated.mp4"
    presigned = storage.presign_put(key, "video/mp4", expires=3600)
    return {
        "job_id": job_id,
        "key": key,
        "url": presigned["url"],
        "method": "PUT",
        "headers": presigned["headers"],
        "expires_in": presigned["expires_in"],
    }


# ---------------------------------------------------------------------------
# Incident ingestion
# ---------------------------------------------------------------------------

def ingest_incidents(connector: dict[str, Any], incidents: list[dict[str, Any]]) -> dict[str, Any]:
    accepted: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for index, payload in enumerate(incidents[:50]):
        client_id = str(payload.get("client_incident_id") or "").strip()
        try:
            _reject_credentials(payload)
            if not client_id:
                raise CloudError("client_incident_id is required")
            existing = query_one(
                "SELECT id FROM incidents WHERE client_incident_id = ?", (client_id,)
            )
            if existing:
                duplicates.append({"client_incident_id": client_id, "incident_id": existing["id"]})
                continue
            created = None
            for attempt in range(2):
                draft = _draft_from_payload(connector, payload)
                try:
                    created = IncidentService.create(draft)
                    break
                except Exception:
                    # Either a duplicate client id or an id collision from a
                    # concurrent invocation: re-check, then retry once.
                    again = query_one(
                        "SELECT id FROM incidents WHERE client_incident_id = ?", (client_id,)
                    )
                    if again:
                        duplicates.append(
                            {"client_incident_id": client_id, "incident_id": again["id"]}
                        )
                        created = None
                        break
                    if attempt == 1:
                        raise
            if created is None:
                continue
            accepted.append(
                {"client_incident_id": client_id, "incident_id": created.get("id"), "index": index}
            )
        except (CloudError, ValueError, KeyError) as exc:
            rejected.append({"client_incident_id": client_id or f"#{index}", "reason": str(exc)[:200]})
    if accepted:
        audit(
            "connector.incidents_ingested",
            {"accepted": len(accepted), "duplicates": len(duplicates)},
            actor=connector["id"],
        )
    return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected}


def _draft_from_payload(connector: dict[str, Any], payload: dict[str, Any]) -> IncidentDraft:
    category = str(payload.get("category") or "").strip()
    if category not in CATEGORY_LABELS:
        raise CloudError(f"Unknown category: {category}")
    confidence = float(payload.get("confidence") or 0)
    if not 0 < confidence <= 1:
        raise CloudError("confidence must be between 0 and 1")
    captured_at = str(payload.get("captured_at") or "").strip()
    if not captured_at:
        raise CloudError("captured_at is required")
    evidence = payload.get("evidence") or {}
    if not isinstance(evidence, dict):
        raise CloudError("evidence must be an object")
    allowed_evidence_keys = {"snapshot_key", "clip_key", "preview_key"}
    if set(evidence) - allowed_evidence_keys:
        raise CloudError("evidence contains unsupported fields")
    for value in evidence.values():
        if value and contains_credentials(str(value)):
            raise CloudError("evidence keys must not contain credentials")

    camera = sanitize_label(str(payload.get("camera") or "Local camera")) or "Local camera"
    location = sanitize_label(payload.get("location"))
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise CloudError("metadata must be an object")
    metadata = {
        key: value
        for key, value in metadata.items()
        if str(key).lower() not in FORBIDDEN_PAYLOAD_KEYS
    }
    metadata = {
        key: (sanitize_label(value) if isinstance(value, str) else value)
        for key, value in metadata.items()
    }
    return IncidentDraft(
        id=IncidentService.reserve_id(),
        category=category,
        confidence=confidence,
        camera=camera,
        location=location,
        captured_at=captured_at,
        source_type=str(payload.get("source_type") or "video")[:20],
        source_ref=str(payload.get("source_ref") or "")[:120],
        track_id=_optional_int(payload.get("track_id")),
        zone_id=_optional_str(payload.get("zone_id")),
        zone_name=sanitize_label(_optional_str(payload.get("zone_name"))),
        dwell_seconds=_optional_float(payload.get("dwell_seconds")),
        class_name=_optional_str(payload.get("class_name")),
        severity=str(payload.get("severity") or "medium")[:20],
        client_incident_id=str(payload.get("client_incident_id"))[:80],
        origin="connector",
        connector_id=connector["id"],
        snapshot_key=_optional_str(evidence.get("snapshot_key")),
        clip_key=_optional_str(evidence.get("clip_key")),
        preview_key=_optional_str(evidence.get("preview_key")),
        evidence={**metadata, "connector": connector.get("name"), "ingested_via": "connector"},
    )


def _reject_credentials(payload: dict[str, Any]) -> None:
    def walk(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in FORBIDDEN_PAYLOAD_KEYS:
                    raise CloudError(f"Field '{path}{key}' is not accepted; camera credentials stay local")
                walk(item, f"{path}{key}.")
        elif isinstance(value, list):
            for item in value:
                walk(item, path)
        elif isinstance(value, str) and contains_credentials(value):
            raise CloudError(f"Field '{path.rstrip('.')}' looks like a URL with credentials")

    walk(payload)


# ---------------------------------------------------------------------------
# Dashboard aggregates
# ---------------------------------------------------------------------------

def _count_pending_assignments() -> int:
    row = query_one(
        "SELECT COUNT(*) AS n FROM jobs WHERE origin = 'cloud' AND status IN ('queued','assigned')"
    )
    return int((row or {}).get("n") or 0)


def performance() -> dict[str, Any]:
    totals = query_one(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed, "
        "SUM(frames_processed) AS frames, SUM(runtime_s) AS runtime, SUM(incidents) AS incidents "
        "FROM jobs WHERE origin = 'cloud'"
    ) or {}
    frames = int(totals.get("frames") or 0)
    runtime = float(totals.get("runtime") or 0)
    rows = query(
        "SELECT id, status, device, model_id, frames_processed, incidents, runtime_s, created_at "
        "FROM jobs WHERE origin = 'cloud' ORDER BY created_at DESC LIMIT 6"
    )
    recent = []
    for row in rows:
        row = dict(row)
        recent.append(
            {
                "id": row["id"],
                "status": row["status"],
                "device": row.get("device") or "connector",
                "model_id": row.get("model_id") or "",
                "frames_processed": int(row.get("frames_processed") or 0),
                "incidents": int(row.get("incidents") or 0),
                "runtime_s": float(row.get("runtime_s") or 0),
                "frames_per_second": (
                    round(int(row.get("frames_processed") or 0) / float(row["runtime_s"]), 2)
                    if row.get("runtime_s")
                    else None
                ),
                "created_at": row["created_at"],
            }
        )
    return {
        "videos_processed": int(totals.get("total") or 0),
        "jobs_failed": int(totals.get("failed") or 0),
        "frames_processed": frames,
        "processing_hours": round(runtime / 3600, 2),
        "average_fps": round(frames / runtime, 2) if runtime and frames else None,
        "recent_jobs": recent,
    }


def storage_summary() -> dict[str, Any]:
    media_bytes = int((query_one("SELECT COALESCE(SUM(size_bytes), 0) AS n FROM media") or {}).get("n") or 0)
    counts = {
        "videos": int((query_one("SELECT COUNT(*) AS n FROM media") or {}).get("n") or 0),
        "incidents": int((query_one("SELECT COUNT(*) AS n FROM incidents") or {}).get("n") or 0),
        "zones": int((query_one("SELECT COUNT(*) AS n FROM zones") or {}).get("n") or 0),
        "cameras": 0,
    }
    settings = all_settings(refresh=True)
    info = storage_info()
    return {
        "bytes": {
            "media": media_bytes,
            "evidence": 0,
            "processed": 0,
            "models": 0,
            "database": 0,
            "total": media_bytes,
        },
        "human": _human(media_bytes),
        "free_gb": 0,
        "counts": counts,
        "data_dir": info.get("bucket") or "object storage",
        "retention": {
            "evidence_days": settings["evidence_retention_days"],
            "media_days": settings["media_retention_days"],
        },
        "backend": info,
    }


def timeline(days: int = 7) -> list[dict[str, Any]]:
    since = (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    rows = query(
        "SELECT substr(captured_at, 1, 10) AS day, COUNT(*) AS n FROM incidents "
        "WHERE captured_at >= ? GROUP BY substr(captured_at, 1, 10) ORDER BY day",
        (since,),
    )
    counts = {row["day"]: int(row["n"]) for row in rows}
    now = datetime.now(timezone.utc)
    return [
        {
            "day": (now - timedelta(days=offset)).strftime("%Y-%m-%d"),
            "count": counts.get((now - timedelta(days=offset)).strftime("%Y-%m-%d"), 0),
        }
        for offset in range(days - 1, -1, -1)
    ]


def overview() -> dict[str, Any]:
    settings = all_settings(refresh=True)
    catalogue = model_catalogue()
    connectors = list_connectors()
    online = [c for c in connectors if c["online"]]
    jobs = list_jobs(limit=8)
    active = [job for job in jobs if job["status"] in {"queued", "assigned", "processing", "paused"}]
    categories = catalogue["categories"]
    return {
        "app": {"name": APP_NAME, "version": APP_VERSION},
        "deployment": deployment_info(),
        "monitoring": {
            "device": "connector" if online else "offline",
            "device_reason": (
                f"{len(online)} connector(s) online — inference runs on your own hardware"
                if online
                else "No connector heartbeat yet. Start the local connector to process footage."
            ),
            "model_id": settings["model_id"],
            "model_installed": any(model["installed"] for model in catalogue["builtin"]),
            "cameras_live": 0,
            "cameras_total": 0,
            "zones": int((query_one("SELECT COUNT(*) AS n FROM zones") or {}).get("n") or 0),
            "processing": active,
        },
        "incidents": IncidentService.stats(),
        "recent": IncidentService.list(limit=8)["items"],
        "performance": performance(),
        "storage": storage_summary(),
        "videos": {
            "uploaded": storage_summary()["counts"]["videos"],
            "processed": performance()["videos_processed"],
        },
        "timeline": timeline(),
        "hardware": {
            "platform": "vercel",
            "system": "Vercel Functions",
            "machine": "serverless",
            "apple_silicon": False,
            "cpu": "managed by Vercel",
            "cpu_cores": 0,
            "memory_gb": None,
            "python": "managed runtime",
            "torch": {"installed": False, "note": "Inference is not hosted here"},
            "device": {
                "device": "connector",
                "reason": "Cloud dashboard only — no local inference",
                "mps_available": None,
                "mps_built": None,
                "cuda_available": None,
                "torch_version": None,
                "machine": "connector",
                "arm64": None,
            },
            "disk": {"total_gb": 0, "free_gb": 0, "data_dir": "object storage"},
            "app": {"name": APP_NAME, "version": APP_VERSION, "offline_capable": False},
        },
        "cameras": [],
        "categories": categories,
        "connectors": connectors,
        "safety": {
            "disclaimer": (
                "Automated output is a potential violation signal for human review only. "
                "CivicWatch never issues challans."
            ),
            "disclaimer_full": SAFETY_DISCLAIMER,
        },
    }


def deployment_info() -> dict[str, Any]:
    storage = storage_info()
    return {
        "mode": "cloud" if cloud_mode() else "local",
        "database": _db_label(),
        "storage": storage,
        "inference": "local_connector",
        "capabilities": {
            "local_inference": False,
            "live_capture": False,
            "video_upload": bool(storage.get("remote")),
            "evidence_redirect": bool(storage.get("remote")),
            "connector": True,
            "exports": True,
        },
    }


def _db_label() -> str:
    from ..db import describe

    return describe().get("target", "")


def cloud_diagnostics() -> dict[str, Any]:
    from ..db import describe, query_one as q1

    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, level: str = "required") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "level": level})

    try:
        row = q1("SELECT 1 AS ok")
        add("Database", bool(row), describe().get("target", "database reachable"))
    except Exception as exc:  # noqa: BLE001
        add("Database", False, f"{type(exc).__name__}: {exc}")

    info = storage_info()
    add(
        "Evidence storage",
        bool(info.get("configured")),
        (
            f"{info.get('kind')} · private bucket {info.get('bucket', 'n/a')}"
            if info.get("configured")
            else info.get("error", "not configured")
        ),
    )
    try:
        if info.get("configured") and info.get("kind") == "s3":
            get_storage()
            # A presigned URL is generated locally; no network call needed.
            add("Presigned URLs", True, "HMAC signing available (no request performed)")
    except Exception as exc:  # noqa: BLE001
        add("Presigned URLs", False, str(exc))

    connectors = list_connectors()
    online = [c for c in connectors if c["online"]]
    add(
        "Local connector",
        bool(online),
        f"{len(online)} of {len(connectors)} connector(s) online" if connectors else "no connector registered",
        level="optional",
    )
    from .auth import auth_configured

    add("Dashboard token", auth_configured(), "CIVICWATCH_ADMIN_TOKEN is set" if auth_configured() else "CIVICWATCH_ADMIN_TOKEN is missing")
    add("Runtime", True, "Vercel serverless · inference intentionally not hosted", level="optional")
    return {
        "ok": all(check["ok"] for check in checks if check["level"] == "required"),
        "mode": "cloud",
        "checks": checks,
        "connectors": connectors,
    }


def system_info() -> dict[str, Any]:
    from ..db import describe

    deployment = deployment_info()
    settings = all_settings(refresh=True)
    return {
        "app": {
            "name": APP_NAME,
            "version": APP_VERSION,
            "data_dir": "object storage",
            "local_only": True,
            "offline_capable": False,
            "mode": "cloud",
        },
        "deployment": deployment,
        "database": describe(),
        "device": {
            "device": "connector",
            "reason": "Inference runs on the paired local connector",
            "mps_available": None,
            "mps_built": None,
            "cuda_available": None,
            "torch_version": None,
            "machine": "connector",
            "arm64": None,
        },
        "torch": {"installed": False, "note": "Not part of the serverless bundle"},
        "hardware": overview()["hardware"],
        "model": {
            "id": settings["model_id"],
            "installed": any(m["installed"] for m in model_catalogue()["builtin"]),
            "catalogue": model_catalogue()["builtin"],
        },
        "privacy": {
            "local_only": True,
            "offline_capable": False,
            "facial_recognition": False,
            "retention_days": settings["evidence_retention_days"],
            "cloud_note": (
                "Video and RTSP credentials remain on the local connector. Only incidents, "
                "metadata and selected evidence are uploaded, to a private bucket."
            ),
        },
        "connectors": list_connectors(),
        "settings_summary": {
            "confidence": settings["confidence"],
            "sample_every": settings["sample_every"],
            "model_id": settings["model_id"],
        },
    }


def update_settings_patch(patch: dict[str, Any], actor: str = "dashboard") -> dict[str, Any]:
    values = update_settings(patch)
    audit("settings.updated", {"keys": sorted(patch)}, actor=actor)
    return values


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def _json(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def new_connector_id() -> str:
    return f"CON-{secrets.token_hex(5)}"


def ensure_schema() -> dict[str, Any]:
    """Apply migrations on cold start (idempotent)."""
    from ..db import describe, init_db

    applied = init_db()
    return {"applied": applied, "database": describe()}


def evidence_available(incident: dict[str, Any]) -> dict[str, bool]:
    return {
        "snapshot": ref_available(incident.get("snapshot_key") or incident.get("snapshot_path")),
        "clip": ref_available(incident.get("clip_key") or incident.get("clip_path")),
        "preview": ref_available(incident.get("preview_key")),
    }


__all__ = [
    "CloudError",
    "StorageError",
    "claim_assignments",
    "cloud_diagnostics",
    "complete_upload",
    "connector_config",
    "create_connector",
    "create_upload",
    "delete_connector",
    "deployment_info",
    "ensure_schema",
    "ingest_incidents",
    "issue_upload_urls",
    "list_connectors",
    "list_jobs",
    "model_catalogue",
    "overview",
    "queue_job",
    "record_heartbeat",
    "system_info",
    "update_assignment",
    "update_settings_patch",
]
