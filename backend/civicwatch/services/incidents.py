"""Incident storage, filtering and the human verification workflow."""

from __future__ import annotations

import csv
import io
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from ..config import SAFETY_DISCLAIMER, get_paths
from ..db import audit, execute, query, query_one, utc_now
from ..detect.base import CATEGORY_LABELS
from ..pipeline.rules import Violation

VALID_STATUSES = ("detected", "pending_review", "verified", "dismissed", "forwarded", "resolved")
REVIEW_STATUSES = ("detected", "pending_review")
SEVERITIES = ("low", "medium", "high")

_id_lock = threading.Lock()


@dataclass
class IncidentDraft:
    id: str
    category: str
    confidence: float
    camera: str
    location: str | None
    captured_at: str
    source_type: str
    source_ref: str
    track_id: int | None = None
    zone_id: str | None = None
    zone_name: str | None = None
    dwell_seconds: float | None = None
    class_name: str | None = None
    severity: str = "medium"
    snapshot_path: str | None = None
    clip_path: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


class IncidentService:
    """Thin, explicit data layer. All writes are audited."""

    # -- creation --------------------------------------------------------
    @staticmethod
    def reserve_id(when: datetime | None = None) -> str:
        moment = when or datetime.now(timezone.utc)
        prefix = f"INC-{moment.strftime('%Y%m%d')}-"
        with _id_lock:
            row = query_one(
                "SELECT COUNT(*) AS n FROM incidents WHERE id LIKE ?", (prefix + "%",)
            )
            sequence = int(row["n"] if row else 0) + 1
            candidate = f"{prefix}{sequence:04d}"
            while query_one("SELECT id FROM incidents WHERE id = ?", (candidate,)):
                sequence += 1
                candidate = f"{prefix}{sequence:04d}"
            return candidate

    @staticmethod
    def create(draft: IncidentDraft) -> dict[str, Any]:
        now = utc_now()
        label = CATEGORY_LABELS.get(draft.category, draft.category)
        execute(
            """INSERT INTO incidents (
                id, category, label, confidence, camera, location, captured_at, created_at,
                updated_at, track_id, zone_id, zone_name, dwell_seconds, status, severity,
                source_type, source_ref, class_name, snapshot_path, clip_path, evidence_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                draft.id,
                draft.category,
                label,
                round(float(draft.confidence), 4),
                draft.camera,
                draft.location,
                draft.captured_at,
                now,
                now,
                draft.track_id,
                draft.zone_id,
                draft.zone_name,
                draft.dwell_seconds,
                "detected",
                draft.severity if draft.severity in SEVERITIES else "medium",
                draft.source_type,
                draft.source_ref,
                draft.class_name,
                draft.snapshot_path,
                draft.clip_path,
                json.dumps({"disclaimer": SAFETY_DISCLAIMER, **(draft.evidence or {})}),
            ),
        )
        execute(
            "INSERT INTO incident_events (incident_id, status, note, created_at) VALUES (?,?,?,?)",
            (
                draft.id,
                "detected",
                "Detected automatically. Awaiting human verification.",
                now,
            ),
        )
        audit("incident.created", {"id": draft.id, "category": draft.category})
        return IncidentService.get(draft.id) or {}

    @staticmethod
    def create_from_violation(
        violation: Violation,
        *,
        incident_id: str,
        camera: str,
        location: str | None,
        captured_at: str,
        source_type: str,
        source_ref: str,
        snapshot_path: str | None,
        clip_path: str | None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        draft = IncidentDraft(
            id=incident_id,
            category=violation.category,
            confidence=violation.confidence,
            camera=camera,
            location=location,
            captured_at=captured_at,
            source_type=source_type,
            source_ref=source_ref,
            track_id=violation.track_id,
            zone_id=violation.zone_id,
            zone_name=violation.zone_name,
            dwell_seconds=round(violation.dwell_seconds, 2),
            class_name=violation.class_name,
            severity=violation.severity,
            snapshot_path=snapshot_path,
            clip_path=clip_path,
            evidence={
                "model_id": violation.model_id,
                "zone": violation.zone_name,
                "dwell_seconds": round(violation.dwell_seconds, 2),
                "box": violation.box,
                **(extra or {}),
            },
        )
        return IncidentService.create(draft)

    # -- reads -----------------------------------------------------------
    @staticmethod
    def get(incident_id: str) -> dict[str, Any] | None:
        row = query_one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
        if not row:
            return None
        item = dict(row)
        item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
        item["has_snapshot"] = bool(item.get("snapshot_path")) and _exists(item["snapshot_path"])
        item["has_clip"] = bool(item.get("clip_path")) and _exists(item["clip_path"])
        item["events"] = [
            dict(event)
            for event in query(
                "SELECT status, note, created_at FROM incident_events WHERE incident_id = ? "
                "ORDER BY id ASC",
                (incident_id,),
            )
        ]
        return item

    @staticmethod
    def list(
        status: str | None = None,
        category: str | None = None,
        search: str | None = None,
        severity: str | None = None,
        source_type: str | None = None,
        source_ref: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
        sort: str = "captured_at_desc",
    ) -> dict[str, Any]:
        where: list[str] = []
        params: list[Any] = []
        if status:
            statuses = [s.strip() for s in status.split(",") if s.strip()]
            invalid = [s for s in statuses if s not in VALID_STATUSES]
            if invalid:
                raise ValueError(f"Unknown status: {', '.join(invalid)}")
            where.append(f"status IN ({','.join('?' for _ in statuses)})")
            params.extend(statuses)
        if category:
            categories = [c.strip() for c in category.split(",") if c.strip()]
            where.append(f"category IN ({','.join('?' for _ in categories)})")
            params.extend(categories)
        if severity:
            where.append("severity = ?")
            params.append(severity)
        if source_type:
            where.append("source_type = ?")
            params.append(source_type)
        if source_ref:
            where.append("source_ref = ?")
            params.append(source_ref)
        if date_from:
            where.append("captured_at >= ?")
            params.append(date_from)
        if date_to:
            where.append("captured_at <= ?")
            params.append(date_to)
        if search:
            like = f"%{search.strip()}%"
            where.append(
                "(id LIKE ? OR camera LIKE ? OR location LIKE ? OR zone_name LIKE ? "
                "OR category LIKE ? OR label LIKE ? OR class_name LIKE ?)"
            )
            params.extend([like] * 7)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        order = {
            "captured_at_desc": "captured_at DESC",
            "captured_at_asc": "captured_at ASC",
            "confidence_desc": "confidence DESC",
            "severity_desc": "CASE severity WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC, captured_at DESC",
        }.get(sort, "captured_at DESC")
        total = int((query_one(f"SELECT COUNT(*) AS n FROM incidents{clause}", params) or {"n": 0})["n"])
        rows = query(
            f"SELECT * FROM incidents{clause} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, max(1, min(500, limit)), max(0, offset)],
        )
        items = []
        for row in rows:
            item = dict(row)
            item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
            item["has_snapshot"] = bool(item.get("snapshot_path")) and _exists(item["snapshot_path"])
            item["has_clip"] = bool(item.get("clip_path")) and _exists(item["clip_path"])
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @staticmethod
    def stats() -> dict[str, Any]:
        counts = {
            row["status"]: row["n"]
            for row in query("SELECT status, COUNT(*) AS n FROM incidents GROUP BY status")
        }
        total = sum(counts.values())
        categories = {
            row["category"]: row["n"]
            for row in query("SELECT category, COUNT(*) AS n FROM incidents GROUP BY category")
        }
        return {
            "total": total,
            "by_status": {status: counts.get(status, 0) for status in VALID_STATUSES},
            "pending_review": sum(counts.get(s, 0) for s in REVIEW_STATUSES),
            "by_category": categories,
        }

    # -- review workflow -------------------------------------------------
    @staticmethod
    def update_status(
        incident_id: str,
        status: str,
        note: str | None = None,
        actor: str = "local operator",
    ) -> dict[str, Any]:
        if status not in VALID_STATUSES:
            raise ValueError(f"Status must be one of {', '.join(VALID_STATUSES)}")
        existing = query_one("SELECT status FROM incidents WHERE id = ?", (incident_id,))
        if not existing:
            raise KeyError("Incident not found")
        if existing["status"] == status and not note:
            return IncidentService.get(incident_id) or {}
        now = utc_now()
        execute(
            "UPDATE incidents SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, incident_id),
        )
        execute(
            "INSERT INTO incident_events (incident_id, status, note, created_at) VALUES (?,?,?,?)",
            (incident_id, status, note or _default_note(status), now),
        )
        audit("incident.status", {"id": incident_id, "from": existing["status"], "to": status}, actor=actor)
        return IncidentService.get(incident_id) or {}

    @staticmethod
    def add_note(incident_id: str, note: str, actor: str = "local operator") -> dict[str, Any]:
        if not note.strip():
            raise ValueError("Note cannot be empty")
        row = query_one("SELECT status FROM incidents WHERE id = ?", (incident_id,))
        if not row:
            raise KeyError("Incident not found")
        execute(
            "INSERT INTO incident_events (incident_id, status, note, created_at) VALUES (?,?,?,?)",
            (incident_id, None, note.strip(), utc_now()),
        )
        execute("UPDATE incidents SET updated_at = ? WHERE id = ?", (utc_now(), incident_id))
        audit("incident.note", {"id": incident_id}, actor=actor)
        return IncidentService.get(incident_id) or {}

    @staticmethod
    def delete(incident_id: str) -> None:
        row = query_one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
        if not row:
            raise KeyError("Incident not found")
        for key in ("snapshot_path", "clip_path"):
            path = row[key]
            if path:
                try:
                    from pathlib import Path

                    Path(path).unlink(missing_ok=True)
                    parent = Path(path).parent
                    if parent.exists() and parent != get_paths().evidence and not any(parent.iterdir()):
                        parent.rmdir()
                except OSError:
                    pass
        execute("DELETE FROM incidents WHERE id = ?", (incident_id,))
        audit("incident.deleted", {"id": incident_id})

    @staticmethod
    def categories() -> list[dict[str, str]]:
        used = {
            row["category"]: row["n"]
            for row in query("SELECT category, COUNT(*) AS n FROM incidents GROUP BY category")
        }
        labels = {**CATEGORY_LABELS}
        return [
            {"value": key, "label": label, "count": used.get(key, 0)}
            for key, label in labels.items()
        ]

    # -- export ----------------------------------------------------------
    @staticmethod
    def csv_rows(incident_ids: Iterable[str] | None = None) -> str:
        rows = [
            row
            for row in query("SELECT * FROM incidents ORDER BY captured_at DESC")
            if not incident_ids or row["id"] in set(incident_ids)
        ]
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "Incident ID",
                "Category",
                "Class",
                "Confidence",
                "Severity",
                "Status",
                "Captured at (UTC)",
                "Camera",
                "Location",
                "Zone",
                "Dwell (s)",
                "Track ID",
                "Source",
                "Snapshot",
                "Clip",
                "Disclaimer",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["id"],
                    row["label"],
                    row["class_name"] or "",
                    f"{row['confidence']:.2f}",
                    row["severity"],
                    row["status"],
                    row["captured_at"],
                    row["camera"],
                    row["location"] or "",
                    row["zone_name"] or "",
                    f"{row['dwell_seconds']:.1f}" if row["dwell_seconds"] else "",
                    row["track_id"] if row["track_id"] is not None else "",
                    row["source_type"],
                    row["snapshot_path"] or "",
                    row["clip_path"] or "",
                    SAFETY_DISCLAIMER,
                ]
            )
        return buffer.getvalue()


def _exists(path: str | None) -> bool:
    if not path:
        return False
    from pathlib import Path

    return Path(path).exists()


def _default_note(status: str) -> str:
    return {
        "pending_review": "Queued for human review.",
        "verified": "Verified by a human reviewer.",
        "dismissed": "Dismissed as not a violation.",
        "forwarded": "Forwarded to the concerned authority.",
        "resolved": "Marked resolved.",
        "detected": "Reopened for review.",
    }.get(status, "")


def new_incident_id() -> str:
    """Fallback id generator (used by tests)."""
    return f"INC-TEST-{uuid.uuid4().hex[:8]}"
