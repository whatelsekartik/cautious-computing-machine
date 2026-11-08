"""Hardware/performance reporting, storage accounting and retention."""

from __future__ import annotations

import os
import platform
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import APP_NAME, APP_VERSION, get_paths
from ..db import audit, execute, query, query_one, utc_now
from ..detect import registry
from ..detect.device import resolve_device, torch_status
from ..settings_store import all_settings
from .cameras import camera_service
from .events import bus
from .incidents import IncidentService


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def _cpu_label() -> str:
    if platform.system() == "Darwin":
        try:
            import subprocess

            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
    return platform.processor() or platform.machine()


def hardware_info() -> dict[str, Any]:
    settings = all_settings()
    device = resolve_device(settings["device_preference"])
    torch = torch_status()
    memory_gb = None
    try:
        import psutil

        memory_gb = round(psutil.virtual_memory().total / (1024**3), 1)
    except Exception:
        pass
    disk = shutil.disk_usage(get_paths().data)
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "apple_silicon": platform.system() == "Darwin" and platform.machine() == "arm64",
        "cpu": _cpu_label(),
        "cpu_cores": os.cpu_count(),
        "memory_gb": memory_gb,
        "python": sys.version.split()[0],
        "torch": torch,
        "device": device.as_dict(),
        "disk": {
            "total_gb": round(disk.total / (1024**3), 1),
            "free_gb": round(disk.free / (1024**3), 1),
            "data_dir": str(get_paths().data),
        },
        "app": {"name": APP_NAME, "version": APP_VERSION, "offline_capable": True},
    }


def storage_info() -> dict[str, Any]:
    paths = get_paths()
    media = _dir_size(paths.media)
    evidence = _dir_size(paths.evidence)
    processed = _dir_size(paths.processed)
    models = _dir_size(paths.models)
    db_bytes = paths.db.stat().st_size if paths.db.exists() else 0
    total = media + evidence + processed + models + db_bytes
    disk = shutil.disk_usage(paths.data)
    counts = {
        "videos": int((query_one("SELECT COUNT(*) AS n FROM media") or {"n": 0})["n"]),
        "incidents": int((query_one("SELECT COUNT(*) AS n FROM incidents") or {"n": 0})["n"]),
        "zones": int((query_one("SELECT COUNT(*) AS n FROM zones") or {"n": 0})["n"]),
        "cameras": int((query_one("SELECT COUNT(*) AS n FROM cameras") or {"n": 0})["n"]),
    }
    return {
        "bytes": {
            "media": media,
            "evidence": evidence,
            "processed": processed,
            "models": models,
            "database": db_bytes,
            "total": total,
        },
        "human": _human(total),
        "free_gb": round(disk.free / (1024**3), 2),
        "counts": counts,
        "data_dir": str(paths.data),
        "retention": {
            "evidence_days": all_settings()["evidence_retention_days"],
            "media_days": all_settings()["media_retention_days"],
        },
    }


def performance_info() -> dict[str, Any]:
    rows = query(
        "SELECT id, status, device, model_id, frames_processed, incidents, runtime_s, "
        "created_at, finished_at FROM jobs WHERE status IN ('completed','stopped') "
        "ORDER BY created_at DESC LIMIT 10"
    )
    jobs = []
    for row in rows:
        item = dict(row)
        runtime = float(item.get("runtime_s") or 0)
        frames = int(item.get("frames_processed") or 0)
        item["frames_per_second"] = round(frames / runtime, 2) if runtime > 0 else None
        jobs.append(item)
    completed = int(
        (query_one("SELECT COUNT(*) AS n FROM jobs WHERE status = 'completed'") or {"n": 0})["n"]
    )
    failed = int(
        (query_one("SELECT COUNT(*) AS n FROM jobs WHERE status = 'failed'") or {"n": 0})["n"]
    )
    frames_total = int(
        (query_one("SELECT COALESCE(SUM(frames_processed), 0) AS n FROM jobs") or {"n": 0})["n"]
    )
    runtime_total = float(
        (query_one("SELECT COALESCE(SUM(runtime_s), 0) AS n FROM jobs") or {"n": 0})["n"]
    )
    return {
        "videos_processed": completed,
        "jobs_failed": failed,
        "frames_processed": frames_total,
        "processing_hours": round(runtime_total / 3600, 2),
        "average_fps": round(frames_total / runtime_total, 2) if runtime_total > 0 else None,
        "recent_jobs": jobs,
    }


def camera_status() -> dict[str, Any]:
    cameras = camera_service.list_cameras()
    live = [c for c in cameras if c.get("monitoring")]
    return {
        "total": len(cameras),
        "monitoring": len(live),
        "cameras": [
            {
                "id": c["id"],
                "name": c["name"],
                "source_type": c["source_type"],
                "monitoring": c.get("monitoring", False),
                "status": (c.get("session") or {}).get("status", c.get("last_status", "idle")),
                "display_source": c["display_source"],
                "last_error": c.get("last_error"),
            }
            for c in cameras
        ],
    }


def model_status() -> dict[str, Any]:
    return {
        "builtin": registry.list_builtin_models(),
        "custom": registry.custom_models(),
        "categories": registry.category_status(),
    }


def overview() -> dict[str, Any]:
    settings = all_settings()
    device = resolve_device(settings["device_preference"])
    incidents = IncidentService.stats()
    perf = performance_info()
    storage = storage_info()
    recent = IncidentService.list(limit=8)["items"]
    media_count = int((query_one("SELECT COUNT(*) AS n FROM media") or {"n": 0})["n"])
    zone_count = int((query_one("SELECT COUNT(*) AS n FROM zones") or {"n": 0})["n"])
    cameras = camera_status()

    since = (datetime.now(timezone.utc) - timedelta(days=6)).strftime("%Y-%m-%d")
    timeline_rows = query(
        "SELECT substr(captured_at, 1, 10) AS day, COUNT(*) AS n FROM incidents "
        "WHERE captured_at >= ? GROUP BY day ORDER BY day",
        (since,),
    )
    timeline_map = {row["day"]: row["n"] for row in timeline_rows}
    timeline = []
    for offset in range(6, -1, -1):
        day = (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")
        timeline.append({"day": day, "count": timeline_map.get(day, 0)})

    model_installed = bool(registry.weights_path(settings["model_id"]).exists())
    return {
        "app": {"name": APP_NAME, "version": APP_VERSION},
        "monitoring": {
            "device": device.device,
            "device_reason": device.reason,
            "model_id": settings["model_id"],
            "model_installed": model_installed,
            "cameras_live": cameras["monitoring"],
            "cameras_total": cameras["total"],
            "zones": zone_count,
            "processing": [
                j for j in job_manager_snapshot() if j["status"] in {"processing", "paused", "queued"}
            ],
        },
        "incidents": incidents,
        "recent": recent,
        "performance": perf,
        "storage": storage,
        "videos": {"uploaded": media_count, "processed": perf["videos_processed"]},
        "timeline": timeline,
        "hardware": hardware_info(),
        "cameras": cameras["cameras"],
        "categories": registry.category_status(),
        "safety": {
            "disclaimer": (
                "Automated output is a potential violation signal for human review only. "
                "CivicWatch never issues challans."
            )
        },
    }


def job_manager_snapshot() -> list[dict[str, Any]]:
    from .jobs import job_manager

    return job_manager.list_jobs(limit=20)


def diagnostics() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    settings = all_settings()
    device = resolve_device(settings["device_preference"])

    def add(name: str, ok: bool, detail: str, level: str = "required") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "level": level})

    add("Python runtime", sys.version_info >= (3, 10),
        f"Python {sys.version.split()[0]}", "required")
    try:
        import cv2

        add("OpenCV", True, f"opencv {cv2.__version__}")
    except Exception as exc:
        add("OpenCV", False, f"not importable: {exc}")
    torch = torch_status()
    add("PyTorch", bool(torch.get("installed")), f"torch {torch.get('version', 'missing')}")
    add(
        "Apple Silicon MPS",
        bool(torch.get("mps_available")),
        (
            "MPS available — GPU acceleration enabled"
            if torch.get("mps_available")
            else f"CPU mode ({device.reason})"
        ),
        "optional",
    )
    path = registry.weights_path(settings["model_id"])
    model_ok = path.exists()
    if model_ok:
        try:
            info = registry.validate_weights(path)
            add("Detection model", True, f"{path.name} readable, {len(info['classes'])} classes")
        except Exception as exc:
            model_ok = False
            add("Detection model", False, f"{path.name} invalid: {exc}")
    else:
        add("Detection model", False, f"{settings['model_id']} weights are not installed")
    try:
        paths = get_paths()
        probe = paths.data / ".write_test"
        probe.write_text("ok")
        probe.unlink()
        add("Data directory writable", True, str(paths.data))
    except Exception as exc:
        add("Data directory writable", False, str(exc))
    try:
        row = query_one("SELECT COUNT(*) AS n FROM schema_migrations")
        add("SQLite schema", True, f"{row['n'] if row else 0} migration(s) applied")
    except Exception as exc:
        add("SQLite schema", False, str(exc))
    categories = registry.category_status()
    missing = [c["label"] for c in categories if not c["installed"]]
    add(
        "Specialised models",
        True,
        "Not installed yet: " + ", ".join(missing) if missing else "All specialised models installed",
        "optional",
    )
    add("Event stream", True, f"{bus.subscriber_count} connected dashboard(s)", "optional")

    required_failures = [c for c in checks if c["level"] == "required" and not c["ok"]]
    return {
        "ok": not required_failures,
        "checks": checks,
        "device": device.as_dict(),
        "settings_device": settings["device_preference"],
        "data_dir": str(get_paths().data),
        "checked_at": utc_now(),
    }


def cleanup(retention_days_override: dict[str, int] | None = None, purge_all: bool = False) -> dict[str, Any]:
    """Delete footage past its retention window; keep incident metadata."""
    settings = all_settings()
    overrides = retention_days_override or {}
    evidence_days = int(overrides.get("evidence", settings["evidence_retention_days"]))
    media_days = int(overrides.get("media", settings["media_retention_days"]))
    now = datetime.now(timezone.utc)
    evidence_cutoff = (now - timedelta(days=0 if purge_all else evidence_days)).isoformat()
    media_cutoff = (now - timedelta(days=0 if purge_all else media_days)).isoformat()

    rows = query(
        "SELECT id, snapshot_path, clip_path FROM incidents "
        "WHERE (snapshot_path IS NOT NULL OR clip_path IS NOT NULL) AND captured_at < ?",
        (evidence_cutoff,),
    )
    removed_files = 0
    removed_bytes = 0
    for row in rows:
        for key in ("snapshot_path", "clip_path"):
            path = Path(row[key]) if row[key] else None
            if path and path.exists():
                removed_bytes += path.stat().st_size
                path.unlink(missing_ok=True)
                removed_files += 1
        folder = Path(row["snapshot_path"]).parent if row["snapshot_path"] else None
        if folder and folder.exists():
            for leftover in folder.glob("*"):
                try:
                    removed_bytes += leftover.stat().st_size
                    leftover.unlink()
                    removed_files += 1
                except OSError:
                    pass
            try:
                folder.rmdir()
            except OSError:
                pass
        execute(
            "UPDATE incidents SET snapshot_path = NULL, clip_path = NULL, updated_at = ? WHERE id = ?",
            (utc_now(), row["id"]),
        )

    old_media = query(
        "SELECT id, stored_name FROM media WHERE created_at < ?", (media_cutoff,)
    )
    removed_media = 0
    for media in old_media:
        path = get_paths().media / media["stored_name"]
        if path.exists():
            removed_bytes += path.stat().st_size
            path.unlink(missing_ok=True)
            removed_media += 1
        for job in query("SELECT id FROM jobs WHERE media_id = ?", (media["id"],)):
            (get_paths().processed / f"{job['id']}.mp4").unlink(missing_ok=True)
        execute("DELETE FROM jobs WHERE media_id = ?", (media["id"],))
        execute("DELETE FROM media WHERE id = ?", (media["id"],))

    orphan_processed = 0
    for file in get_paths().processed.glob("*.mp4"):
        if now.timestamp() - file.stat().st_mtime > (0 if purge_all else media_days) * 86400:
            removed_bytes += file.stat().st_size
            file.unlink(missing_ok=True)
            orphan_processed += 1

    audit(
        "storage.cleanup",
        {
            "evidence_files": removed_files,
            "media": removed_media,
            "processed": orphan_processed,
            "bytes": removed_bytes,
            "purge_all": purge_all,
        },
    )
    return {
        "ok": True,
        "evidence_files_removed": removed_files,
        "media_removed": removed_media,
        "processed_removed": orphan_processed,
        "bytes_removed": removed_bytes,
        "human_freed": _human(removed_bytes),
        "evidence_retention_days": evidence_days,
        "media_retention_days": media_days,
        "purge_all": purge_all,
    }


def _human(size: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def purge_evidence() -> dict[str, Any]:
    return cleanup(purge_all=True)


def uptime_seconds(start: float) -> float:
    return round(time.monotonic() - start, 1)
