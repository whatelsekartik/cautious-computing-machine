"""Connector worker: heartbeat, incident sync and remote-assignment processing.

The worker is deliberately single-threaded and idempotent. A tick:

1. reports a heartbeat (device, version, installed models, capabilities);
2. queues any local incidents that are not in the outbox yet;
3. drains the outbox in small batches with backoff;
4. claims at most a few cloud assignments and processes them locally.

Everything that must survive a crash lives in SQLite, so killing the process
(or losing the network) never loses an incident.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

from ..config import APP_VERSION, get_paths
from ..db import execute, query, query_one, utc_now
from ..detect import registry
from ..detect.device import resolve_device
from ..services.incidents import IncidentService
from ..settings_store import all_settings
from ..storage import evidence_keys
from . import outbox
from .client import CloudClient, CloudRejected, CloudUnavailable

log = logging.getLogger("civicwatch.connector")

MAX_EVIDENCE_BYTES = 512 * 1024 * 1024  # per file; clips are far smaller
MAX_PROCESSED_UPLOAD_BYTES = 1024 * 1024 * 1024


class ConnectorWorker:
    def __init__(
        self,
        client: CloudClient,
        *,
        process_assignments: bool = True,
        poll_seconds: int = 20,
        max_assignment_bytes: int = 4 * 1024**3,
    ):
        self.client = client
        self.process_assignments_enabled = process_assignments
        self.poll_seconds = max(5, int(poll_seconds))
        self.max_assignment_bytes = max_assignment_bytes
        self.last_config: dict[str, Any] = {}
        self.last_heartbeat: dict[str, Any] = {}
        self.stats: dict[str, Any] = {
            "heartbeats": 0,
            "sent": 0,
            "duplicates": 0,
            "failed": 0,
            "assignments": 0,
            "last_tick": None,
            "last_error": None,
        }

    # -- discovery -------------------------------------------------------
    def local_models(self) -> list[dict[str, Any]]:
        """What is actually installed here — never a claim about what could be."""
        installed: list[dict[str, Any]] = []
        now = utc_now()
        for entry in registry.BUILTIN_MODELS:
            try:
                path = registry.weights_path(entry["id"])
            except Exception:  # noqa: BLE001
                continue
            if path.exists():
                installed.append(
                    {
                        "id": entry["id"],
                        "name": entry["name"],
                        "family": entry["family"],
                        "license": entry["license"],
                        "classes": [],
                        "installed_at": now,
                    }
                )
        for row in query("SELECT * FROM custom_models"):
            row = dict(row)
            category = row.get("category")
            if category not in {"pothole", "garbage"}:
                continue
            classes: list[str] = []
            try:
                import json

                meta = json.loads(row.get("metadata") or "{}")
                classes = meta.get("classes") or []
            except Exception:  # noqa: BLE001
                classes = []
            installed.append(
                {
                    "id": row["id"],
                    "name": row.get("name") or row["id"],
                    "family": category,
                    "license": row.get("license") or "custom",
                    "classes": classes,
                    "installed_at": row.get("installed_at") or now,
                }
            )
        return installed

    def capabilities(self) -> dict[str, Any]:
        settings = all_settings(refresh=True)
        device = resolve_device(settings["device_preference"])
        return {
            "inference": True,
            "engine": "ultralytics+bytetrack",
            "device": device.device,
            "accelerated": device.device == "mps",
            "live_capture": True,
            "rtsp": True,
            "webcam": True,
            "file_processing": True,
            "evidence_upload": True,
            "platform": "macOS",
            "models": [m["id"] for m in self.local_models()],
        }

    # -- heartbeat -------------------------------------------------------
    def heartbeat(self) -> dict[str, Any]:
        models = self.local_models()
        settings = all_settings(refresh=True)
        payload = {
            "version": APP_VERSION,
            "device": resolve_device(settings["device_preference"]).device,
            "capabilities": self.capabilities(),
            "models": {
                "selected": settings["model_id"],
                "installed": [m["id"] for m in models],
                "custom": [m["id"] for m in models if m["family"] in {"pothole", "garbage"}],
            },
            "models_installed": models,
        }
        result = self.client.heartbeat(payload)
        self.last_heartbeat = result
        self.last_config = result.get("config") or {}
        self.stats["heartbeats"] += 1
        self.stats["last_error"] = None
        return result

    def report_models(self) -> dict[str, Any]:
        return self.client.report_models(self.local_models())

    # -- incidents -------------------------------------------------------
    def enqueue_new_incidents(self) -> int:
        return outbox.enqueue_unsynced()

    def flush(self, limit: int = 10) -> dict[str, Any]:
        queued = self.enqueue_new_incidents()
        rows = outbox.due(limit=limit)
        sent = duplicates = failed = 0
        for row in rows:
            try:
                outcome = self._send_row(row)
            except CloudUnavailable as exc:
                attempts = int(row["attempts"]) + 1
                outbox.mark_failed(row["id"], str(exc), attempts)
                self.stats["failed"] += 1
                self.stats["last_error"] = str(exc)
                failed += 1
                log.warning("Cloud unreachable, will retry: %s", exc)
                break  # do not hammer an offline endpoint
            except CloudRejected as exc:
                attempts = int(row["attempts"]) + 1
                outbox.mark_failed(row["id"], str(exc), attempts)
                self.stats["failed"] += 1
                self.stats["last_error"] = str(exc)
                failed += 1
                log.error("Cloud rejected incident %s: %s", row["incident_id"], exc)
                continue
            if outcome == "sent":
                outbox.mark_sent(row["id"])
                self.stats["sent"] += 1
                sent += 1
            elif outcome.startswith("duplicate"):
                cloud_id = outcome.split(":", 1)[1] if ":" in outcome else None
                outbox.mark_duplicate(row["id"], cloud_id)
                self.stats["duplicates"] += 1
                duplicates += 1
        outbox.purge_sent()
        return {
            "queued_new": queued,
            "attempted": len(rows),
            "sent": sent,
            "duplicates": duplicates,
            "failed": failed,
            "pending": outbox.pending_count(),
        }

    def _send_row(self, row: dict[str, Any]) -> str:
        payload = outbox.incident_payload(row)
        if not payload:
            raise CloudRejected("Outbox payload is unreadable")
        files_local = payload.pop("evidence_local", {}) or {}
        client_id = payload["client_incident_id"]

        evidence_keys_wire: dict[str, str] = {}
        uploadable = [
            (kind, Path(path))
            for kind, path in files_local.items()
            if path and Path(path).is_file() and Path(path).stat().st_size <= MAX_EVIDENCE_BYTES
        ]
        if uploadable:
            requested = [
                {
                    "kind": kind,
                    "content_type": {"snapshot": "image/jpeg", "clip": "video/mp4", "preview": "image/gif"}[kind],
                    "bytes": path.stat().st_size,
                }
                for kind, path in uploadable
            ]
            issued = self.client.evidence_urls(client_id, requested)
            by_kind = {item["kind"]: item for item in issued}
            for kind, path in uploadable:
                item = by_kind.get(kind)
                if not item:
                    continue
                self.client.upload_file(item["url"], path, item.get("headers"))
                evidence_keys_wire[f"{kind}_key"] = item["key"]

        wire = dict(payload)
        wire["evidence"] = evidence_keys_wire or None
        result = self.client.push_incidents([wire])
        accepted = {item["client_incident_id"] for item in result.get("accepted", [])}
        duplicates = {
            item["client_incident_id"]: item.get("incident_id")
            for item in result.get("duplicates", [])
        }
        rejected = {item["client_incident_id"]: item.get("reason") for item in result.get("rejected", [])}
        if client_id in accepted:
            return "sent"
        if client_id in duplicates:
            return f"duplicate:{duplicates[client_id] or ''}"
        raise CloudRejected(rejected.get(client_id, "Cloud did not accept the incident"))

    # -- assignments -----------------------------------------------------
    def process_assignments(self) -> dict[str, Any]:
        if not self.process_assignments_enabled:
            return {"skipped": True}
        items = self.client.claim_assignments(limit=2)
        processed = []
        for item in items:
            try:
                processed.append(self._process_assignment(item))
            except CloudUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001 - report, keep the connector alive
                log.exception("Assignment %s failed", item.get("id"))
                self._report_failure(item, str(exc))
                processed.append({"id": item.get("id"), "status": "failed", "error": str(exc)[:300]})
        return {"claimed": len(items), "processed": processed}

    def _process_assignment(self, item: dict[str, Any]) -> dict[str, Any]:
        job_id = item["id"]
        settings = {**all_settings(refresh=True), **(item.get("settings") or {})}
        download = item.get("download") or {}
        if not download.get("url"):
            self._report_failure(item, "No download URL was provided for this assignment")
            return {"id": job_id, "status": "failed"}

        model_id = settings.get("model_id") or "yolov8n"
        if not registry.weights_path(model_id).exists():
            installed = [m["id"] for m in self.local_models() if m["family"] == "vehicle_pedestrian"]
            if not installed:
                self._report_failure(
                    item,
                    "No vehicle-detection weights are installed on this connector. "
                    "Run Setup CivicWatch.command or download a model in local Settings.",
                )
                return {"id": job_id, "status": "failed"}
            model_id = installed[0]

        self.client.update_assignment(job_id, {"status": "processing", "progress": 0.0, "device": "local"})
        self.stats["assignments"] += 1

        media_name = item.get("media_name") or f"{job_id}.mp4"
        suffix = Path(media_name).suffix.lower() or ".mp4"
        target = get_paths().media / f"cwconn-{job_id}{suffix}"
        self.client.download_to(download["url"], target, max_bytes=self.max_assignment_bytes)

        media_id = self._register_media(target, media_name, job_id)
        from ..services.jobs import JobOptions, job_manager

        local_job = job_manager.create_job(
            media_id,
            JobOptions(
                sample_every=int(settings.get("sample_every") or 5),
                imgsz=int(settings.get("imgsz") or 640),
                model_id=model_id,
                max_width=int(settings.get("max_processing_width") or 1280),
            ),
        )
        local_job_id = local_job["id"]
        result = self._wait_for_job(local_job_id, remote_job_id=job_id)
        if result.get("status") != "completed":
            self._report_failure(item, result.get("error") or "Local processing failed")
            return {"id": job_id, "status": "failed", "error": result.get("error")}

        incidents = IncidentService.list(source_ref=media_id, limit=200)["items"]
        for incident in incidents:
            outbox.enqueue_incident(incident)
        flushed = self.flush(limit=50)

        processed_key = self._upload_processed(local_job_id, job_id)
        snapshot = query_one(
            "SELECT COUNT(*) AS n FROM incidents WHERE source_ref = ?", (media_id,)
        ) or {"n": 0}
        self.client.update_assignment(
            job_id,
            {
                "status": "completed",
                "progress": 1.0,
                "frames_total": result.get("frames_total", 0),
                "frames_processed": result.get("frames_processed", 0),
                "detections": result.get("detections", 0),
                "incidents": int(snapshot.get("n") or 0),
                "runtime_s": float(result.get("elapsed_s") or 0),
                "device": result.get("device") or "local",
                **({"processed_key": processed_key} if processed_key else {}),
            },
        )
        return {
            "id": job_id,
            "status": "completed",
            "local_job": local_job_id,
            "incidents": int(snapshot.get("n") or 0),
            "sync": flushed,
        }

    def _register_media(self, path: Path, original_name: str, remote_job_id: str) -> str:
        from ..services.jobs import probe_video

        probe = probe_video(path)
        media_id = uuid.uuid4().hex[:12]
        execute(
            "INSERT INTO media (id, original_name, stored_name, size_bytes, duration_s, width, height, "
            "fps, frame_count, created_at, origin, upload_status, uploaded_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                media_id,
                f"cloud:{original_name}"[:180],
                path.name,
                path.stat().st_size,
                probe["duration_s"],
                probe["width"],
                probe["height"],
                probe["fps"],
                probe["frame_count"],
                utc_now(),
                "connector",
                "ready",
                f"connector:{remote_job_id}",
            ),
        )
        return media_id

    def _wait_for_job(self, local_job_id: str, remote_job_id: str, timeout_s: int = 6 * 3600) -> dict[str, Any]:
        from ..services.jobs import job_manager

        started = time.time()
        last_report = 0.0
        while time.time() - started < timeout_s:
            job = job_manager.get_job(local_job_id) or {}
            status = job.get("status")
            if status in {"completed", "failed", "stopped"}:
                return job
            now = time.time()
            if now - last_report >= 10:
                last_report = now
                try:
                    self.client.update_assignment(
                        remote_job_id,
                        {
                            "status": "processing",
                            "progress": float(job.get("progress") or 0),
                            "frames_total": job.get("frames_total"),
                            "frames_processed": job.get("frames_processed"),
                            "detections": job.get("detections"),
                            "device": job.get("device") or "local",
                        },
                    )
                except CloudUnavailable:
                    raise
                except Exception:  # noqa: BLE001 - progress is best-effort
                    pass
            time.sleep(2)
        return {"status": "failed", "error": "Local processing timed out"}

    def _upload_processed(self, local_job_id: str, remote_job_id: str) -> str | None:
        path = get_paths().processed / f"{local_job_id}.mp4"
        if not path.exists() or path.stat().st_size > MAX_PROCESSED_UPLOAD_BYTES:
            return None
        issued = self.client.request_processed_url(remote_job_id)
        if not issued or not issued.get("url"):
            return None
        try:
            self.client.upload_file(issued["url"], path, issued.get("headers"))
        except (CloudRejected, CloudUnavailable) as exc:
            log.warning("Annotated video upload skipped: %s", exc)
            return None
        return issued.get("key")

    def _report_failure(self, item: dict[str, Any], error: str) -> None:
        try:
            self.client.update_assignment(
                item["id"], {"status": "failed", "error": error[:500], "runtime_s": 0}
            )
        except Exception:  # noqa: BLE001 - best effort
            pass

    # -- loop ------------------------------------------------------------
    def tick(self) -> dict[str, Any]:
        started = time.time()
        summary: dict[str, Any] = {}
        try:
            summary["heartbeat"] = {"ok": True}
            self.heartbeat()
            summary["sync"] = self.flush()
            summary["assignments"] = self.process_assignments()
            summary["ok"] = True
            self.stats["last_error"] = None
        except CloudUnavailable as exc:
            self.stats["last_error"] = str(exc)
            summary.update({"ok": False, "offline": True, "error": str(exc), "pending": outbox.pending_count()})
            log.warning("Connector offline: %s (queued incidents are kept locally)", exc)
        except CloudRejected as exc:
            self.stats["last_error"] = str(exc)
            summary.update({"ok": False, "error": str(exc)})
            log.error("Cloud rejected connector credentials: %s", exc)
        finally:
            self.stats["last_tick"] = utc_now()
            summary["stats"] = dict(self.stats)
            summary["outbox"] = outbox.stats()
            summary["duration_s"] = round(time.time() - started, 2)
        return summary

    def run_forever(self, interval: int | None = None, stop_after: int | None = None) -> None:
        interval = interval or self.poll_seconds
        ticks = 0
        while True:
            summary = self.tick()
            ticks += 1
            if not summary.get("ok"):
                log.info("Next attempt in %ss", interval)
            if stop_after and ticks >= stop_after:
                return
            time.sleep(interval)


def local_evidence_summary() -> dict[str, Any]:
    """Small helper for the CLI banner."""
    rows = query("SELECT snapshot_path, clip_path, snapshot_key, clip_key, preview_key FROM incidents")
    with_evidence = 0
    for row in rows:
        refs = evidence_keys(dict(row))
        if any(refs.values()):
            with_evidence += 1
    return {"incidents": len(rows), "with_evidence": with_evidence, "outbox": outbox.stats()}
