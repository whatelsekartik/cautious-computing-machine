"""Video upload, probing and the resumable processing job runner."""

from __future__ import annotations

import logging
import math
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2

from ..config import get_paths
from ..db import audit, execute, query, query_one, utc_now
from ..detect import registry
from ..detect.device import resolve_device
from ..detect.yolo import ModelNotAvailable
from ..pipeline.annotate import encode_jpeg
from ..pipeline.engine import FrameProcessor, SourceInfo
from ..settings_store import all_settings
from .events import publish
from .incidents import IncidentService
from .zones import ZoneService

log = logging.getLogger("civicwatch.jobs")

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".mpg", ".mpeg", ".wmv"}
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB
JOB_STATUSES = ("queued", "processing", "paused", "completed", "stopped", "failed")


class MediaError(ValueError):
    pass


@dataclass
class JobOptions:
    sample_every: int = 5
    imgsz: int = 640
    model_id: str | None = None
    max_width: int | None = None
    save_annotated_video: bool | None = None
    location: str | None = None


class JobRunner:
    """Owns one processing thread and its shared preview state."""

    def __init__(self, job_id: str, media: dict[str, Any], options: JobOptions) -> None:
        self.job_id = job_id
        self.media = media
        self.options = options
        self.status = "queued"
        self.progress = 0.0
        self.frames_total = int(media.get("frame_count") or 0)
        self.frames_processed = 0
        self.detections = 0
        self.incidents = 0
        self.device: str | None = None
        self.model_id: str = options.model_id or "yolov8n"
        self.error: str | None = None
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.fps_effective = 0.0
        self.processed_path: str | None = None
        self.latest_jpeg: bytes | None = None
        self.duration_s = float(media.get("duration_s") or 0.0)
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._process: FrameProcessor | None = None
        self._last_persist = 0.0
        self.sample_every = max(1, int(options.sample_every))

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"job-{self.job_id}", daemon=True)
        self._thread.start()

    def request_pause(self) -> None:
        if self.status == "processing":
            self._pause.set()

    def request_resume(self) -> None:
        self._pause.clear()

    def request_stop(self) -> None:
        self._stop.set()
        self._pause.clear()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- main loop -------------------------------------------------------
    def _run(self) -> None:
        settings = all_settings(refresh=True)
        self.sample_every = max(1, int(self.options.sample_every))
        imgsz = int(self.options.imgsz)
        max_width = int(self.options.max_width or settings["max_processing_width"])
        save_video = (
            settings["save_annotated_video"]
            if self.options.save_annotated_video is None
            else bool(self.options.save_annotated_video)
        )
        media_path = Path(self.media["path"])
        cap = cv2.VideoCapture(str(media_path))
        if not cap.isOpened():
            self._fail("The video could not be opened. The file may be damaged or use an unsupported codec.")
            return

        writer: cv2.VideoWriter | None = None
        try:
            device_info = resolve_device(settings["device_preference"])
            self.device = device_info.device
            detector = registry.get_detector(
                model_id=self.model_id,
                device=device_info.device,
                confidence=float(settings["confidence"]),
                iou=float(settings["iou_threshold"]),
                imgsz=imgsz,
            )
            zones = ZoneService.for_source("media", self.media["id"])
            video_fps = float(self.media.get("fps") or 25.0)
            if not video_fps or math.isnan(video_fps):
                video_fps = 25.0
            clip_fps = max(1.5, min(15.0, video_fps / self.sample_every))
            source = SourceInfo(
                kind="video",
                ref=self.media["id"],
                camera=self.media["original_name"],
                location=self.options.location,
            )
            self._process = FrameProcessor(
                detector=detector,
                source=source,
                settings=settings,
                zones=zones,
                custom_detectors=registry.active_custom_detectors(
                    device_info.device, float(settings["confidence"]), imgsz
                ),
                on_incident=self._on_incident,
                evidence_dir=get_paths().evidence,
                clip_fps=clip_fps,
            )
            self._process.model_label = detector.model_id

            if save_video:
                self.processed_path = str(get_paths().processed / f"{self.job_id}.mp4")

            self._set_status("processing")
            self.started_at = utc_now()
            started = time.monotonic()
            frame_index = 0
            last_frames = 0
            last_time = started

            while not self._stop.is_set():
                if self._pause.is_set():
                    if self.status != "paused":
                        self._set_status("paused")
                        publish("job.updated", self.snapshot())
                    time.sleep(0.1)
                    continue
                if self.status == "paused":
                    self._set_status("processing")
                    publish("job.updated", self.snapshot())

                if not cap.grab():
                    break
                frame_index += 1
                if (frame_index - 1) % self.sample_every != 0:
                    continue
                ok, frame = cap.retrieve()
                if not ok or frame is None:
                    continue
                frame = _fit_width(frame, max_width)
                t_seconds = frame_index / video_fps
                result = self._process.process(
                    frame,
                    t_seconds,
                    wall_clock=datetime.now(timezone.utc),
                    incident_id_factory=IncidentService.reserve_id,
                )
                self.frames_processed += 1
                self.detections = self._process.total_detections
                self.incidents = self._process.total_incidents
                self.latest_jpeg = encode_jpeg(result.annotated, quality=75)

                if save_video:
                    if writer is None:
                        height, width = result.annotated.shape[:2]
                        get_paths().processed.mkdir(parents=True, exist_ok=True)
                        writer = cv2.VideoWriter(
                            str(self.processed_path),
                            cv2.VideoWriter_fourcc(*"mp4v"),
                            clip_fps,
                            (width, height),
                        )
                        if not writer.isOpened():
                            log.warning("Annotated video encoder unavailable; continuing without it")
                            writer = None
                            self.processed_path = None
                    if writer is not None:
                        writer.write(result.annotated)

                self.progress = (
                    min(0.999, frame_index / self.frames_total) if self.frames_total else 0.0
                )
                now = time.monotonic()
                if now - last_time >= 1.0:
                    self.fps_effective = (self.frames_processed - last_frames) / (now - last_time)
                    last_frames, last_time = self.frames_processed, now
                    self._persist(force=True)
                    publish("job.updated", self.snapshot())

            if self._stop.is_set():
                self.status = "stopped"
            else:
                self.status = "completed"
                self.progress = 1.0
        except ModelNotAvailable as exc:
            self._fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - surface to UI, keep server alive
            log.exception("Job %s failed", self.job_id)
            self._fail(f"Processing failed: {exc}")
        finally:
            try:
                cap.release()
            except Exception:
                pass
            if writer is not None:
                try:
                    writer.release()
                except Exception:
                    pass
            if self._process is not None:
                self._process.close()
            self.finished_at = utc_now()
            self.latest_jpeg = self.latest_jpeg if self.status in {"completed", "stopped"} else None
            self._persist(force=True)
            publish("job.updated", self.snapshot())
            if self.status == "completed":
                audit("job.completed", {"id": self.job_id, "incidents": self.incidents})

    # -- helpers ---------------------------------------------------------
    def _fail(self, message: str) -> None:
        self.status = "failed"
        self.error = message
        log.warning("Job %s failed: %s", self.job_id, message)
        publish("job.updated", self.snapshot())

    def _set_status(self, status: str) -> None:
        self.status = status
        self._persist(force=True)

    def _on_incident(self, incident: dict[str, Any]) -> None:
        publish("incident.created", {k: incident.get(k) for k in (
            "id", "category", "label", "confidence", "camera", "captured_at", "severity",
            "status", "zone_name", "track_id", "source_type", "source_ref", "has_snapshot",
        )})

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            elapsed = 0.0
            if self.started_at:
                try:
                    elapsed = (
                        datetime.now(timezone.utc)
                        - datetime.fromisoformat(self.started_at)
                    ).total_seconds()
                except ValueError:
                    elapsed = 0.0
            remaining_s = None
            if self.fps_effective > 0 and self.frames_total and self.frames_processed:
                total_samples = max(1, math.ceil(self.frames_total / self.sample_every))
                remaining_samples = max(0, total_samples - self.frames_processed)
                remaining_s = round(remaining_samples / max(0.1, self.fps_effective), 1)
            return {
                "id": self.job_id,
                "media_id": self.media["id"],
                "media_name": self.media["original_name"],
                "status": self.status,
                "progress": round(self.progress, 4),
                "frames_processed": self.frames_processed,
                "frames_total": self.frames_total,
                "sample_every": self.sample_every,
                "imgsz": self.options.imgsz,
                "detections": self.detections,
                "incidents": self.incidents,
                "device": self.device,
                "model_id": self.model_id,
                "error": self.error,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "elapsed_s": round(elapsed, 1),
                "fps": round(self.fps_effective, 2),
                "eta_s": remaining_s,
                "has_preview_video": bool(self.processed_path and Path(self.processed_path).exists()),
                "preview_frame": self.latest_jpeg is not None,
            }

    def _persist(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_persist < 2.0:
            return
        self._last_persist = now
        state = self.snapshot()
        execute(
            """UPDATE jobs SET status=?, device=?, model_id=?, sample_every=?, imgsz=?,
               frames_total=?, frames_processed=?, progress=?, detections=?, incidents=?,
               error=?, started_at=?, finished_at=?, runtime_s=? WHERE id=?""",
            (
                state["status"], state["device"], state["model_id"], state["sample_every"],
                state["imgsz"], state["frames_total"], state["frames_processed"],
                state["progress"], state["detections"], state["incidents"], state["error"],
                state["started_at"], state["finished_at"], state["elapsed_s"], self.job_id,
            ),
        )


def _fit_width(frame, max_width: int):
    height, width = frame.shape[:2]
    if width <= max_width:
        return frame
    scale = max_width / float(width)
    return cv2.resize(
        frame, (max_width, int(round(height * scale))), interpolation=cv2.INTER_AREA
    )


class JobManager:
    def __init__(self) -> None:
        self._runners: dict[str, JobRunner] = {}
        self._lock = threading.Lock()

    # -- media -----------------------------------------------------------
    def save_upload(self, source, original_name: str) -> dict[str, Any]:
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise MediaError(
                f"Unsupported file type '{suffix or 'unknown'}'. "
                f"Supported: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            )
        media_id = uuid.uuid4().hex[:12]
        stored_name = f"{media_id}{suffix}"
        destination = get_paths().media / stored_name
        size = 0
        source.seek(0)
        with open(destination, "wb") as handle:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    handle.close()
                    destination.unlink(missing_ok=True)
                    raise MediaError("File is larger than the 4 GB limit")
                handle.write(chunk)
        if size == 0:
            destination.unlink(missing_ok=True)
            raise MediaError("The uploaded file is empty")

        try:
            probe = probe_video(destination)
        except MediaError:
            destination.unlink(missing_ok=True)
            raise
        row = {
            "id": media_id,
            "original_name": original_name,
            "stored_name": stored_name,
            "size_bytes": size,
            **probe,
        }
        execute(
            """INSERT INTO media (id, original_name, stored_name, size_bytes, duration_s, width,
               height, fps, frame_count, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                media_id, original_name, stored_name, size, probe["duration_s"],
                probe["width"], probe["height"], probe["fps"], probe["frame_count"], utc_now(),
            ),
        )
        audit("media.uploaded", {"id": media_id, "name": original_name, "bytes": size})
        return self.get_media(media_id) or {}

    def list_media(self) -> list[dict[str, Any]]:
        rows = query("SELECT * FROM media ORDER BY created_at DESC")
        items = []
        for row in rows:
            item = dict(row)
            item["path"] = str(get_paths().media / item["stored_name"])
            item["exists"] = Path(item["path"]).exists()
            jobs = query(
                "SELECT id, status, incidents, created_at FROM jobs WHERE media_id = ? "
                "ORDER BY created_at DESC",
                (item["id"],),
            )
            item["jobs"] = [dict(j) for j in jobs]
            item["processed"] = any(j["status"] == "completed" for j in item["jobs"])
            items.append(item)
        return items

    def get_media(self, media_id: str) -> dict[str, Any] | None:
        row = query_one("SELECT * FROM media WHERE id = ?", (media_id,))
        if not row:
            return None
        item = dict(row)
        item["path"] = str(get_paths().media / item["stored_name"])
        item["exists"] = Path(item["path"]).exists()
        return item

    def delete_media(self, media_id: str) -> None:
        media = self.get_media(media_id)
        if not media:
            raise KeyError("Media not found")
        with self._lock:
            running = [
                r for r in self._runners.values()
                if r.media["id"] == media_id and r.is_alive()
            ]
        for runner in running:
            runner.request_stop()
        for job in query("SELECT id FROM jobs WHERE media_id = ?", (media_id,)):
            self._cleanup_job_artifacts(job["id"])
        execute("DELETE FROM media WHERE id = ?", (media_id,))
        Path(media["path"]).unlink(missing_ok=True)
        audit("media.deleted", {"id": media_id})

    def frame_jpeg(self, media_id: str, t_seconds: float = 0.0, max_width: int = 1280) -> bytes:
        media = self.get_media(media_id)
        if not media or not Path(media["path"]).exists():
            raise KeyError("Media not found")
        cap = cv2.VideoCapture(media["path"])
        if not cap.isOpened():
            raise MediaError("Could not read this video")
        try:
            if t_seconds > 0:
                cap.set(cv2.CAP_PROP_POS_MSEC, t_seconds * 1000.0)
            ok, frame = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = cap.read()
            if not ok or frame is None:
                raise MediaError("Could not decode a frame from this video")
            return encode_jpeg(_fit_width(frame, max_width), quality=85)
        finally:
            cap.release()

    # -- jobs ------------------------------------------------------------
    def create_job(self, media_id: str, options: JobOptions) -> dict[str, Any]:
        media = self.get_media(media_id)
        if not media:
            raise KeyError("Media not found")
        if not Path(media["path"]).exists():
            raise MediaError("The uploaded video is missing from local storage")
        settings = all_settings()
        model_id = options.model_id or settings["model_id"]
        if not registry.builtin_entry(model_id):
            raise ValueError(f"Unknown model: {model_id}")
        if not registry.weights_path(model_id).exists():
            raise ValueError(
                "The selected AI model is not installed. Open Settings → AI models to download it."
            )
        job_id = uuid.uuid4().hex[:12]
        options.sample_every = max(1, min(60, int(options.sample_every)))
        options.imgsz = int(options.imgsz)
        options.model_id = model_id
        execute(
            """INSERT INTO jobs (id, media_id, status, sample_every, imgsz, created_at)
               VALUES (?,?,?,?,?,?)""",
            (job_id, media_id, "queued", options.sample_every, options.imgsz, utc_now()),
        )
        runner = JobRunner(job_id, media, options)
        with self._lock:
            self._runners[job_id] = runner
            self._prune_runners()
        runner.start()
        publish("job.updated", runner.snapshot())
        return runner.snapshot()

    def list_jobs(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = query(
            "SELECT j.*, m.original_name AS media_name FROM jobs j "
            "LEFT JOIN media m ON m.id = j.media_id ORDER BY j.created_at DESC LIMIT ?",
            (limit,),
        )
        items = []
        for row in rows:
            item = dict(row)
            runner = self._runners.get(item["id"])
            if runner:
                item.update(runner.snapshot())
            else:
                item["media_name"] = item.get("media_name") or "Deleted video"
                item["has_preview_video"] = bool(
                    (get_paths().processed / f"{item['id']}.mp4").exists()
                )
            items.append(item)
        return items

    def get_runner(self, job_id: str) -> JobRunner | None:
        return self._runners.get(job_id)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        runner = self._runners.get(job_id)
        if runner:
            return runner.snapshot()
        row = query_one(
            "SELECT j.*, m.original_name AS media_name FROM jobs j "
            "LEFT JOIN media m ON m.id = j.media_id WHERE j.id = ?",
            (job_id,),
        )
        if not row:
            return None
        item = dict(row)
        item["has_preview_video"] = (get_paths().processed / f"{job_id}.mp4").exists()
        return item

    def control(self, job_id: str, action: str) -> dict[str, Any]:
        runner = self._runners.get(job_id)
        if runner is None or not runner.is_alive():
            raise ValueError("This job is not running")
        if action == "pause":
            runner.request_pause()
        elif action == "resume":
            runner.request_resume()
        elif action == "stop":
            runner.request_stop()
        else:
            raise ValueError("Action must be pause, resume or stop")
        return runner.snapshot()

    def delete_job(self, job_id: str) -> None:
        runner = self._runners.get(job_id)
        if runner and runner.is_alive():
            runner.request_stop()
            for _ in range(50):
                if not runner.is_alive():
                    break
                time.sleep(0.1)
        with self._lock:
            self._runners.pop(job_id, None)
        self._cleanup_job_artifacts(job_id)
        execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        audit("job.deleted", {"id": job_id})

    def _prune_runners(self, keep: int = 20) -> None:
        """Bound in-memory job state for long-running installations."""
        finished = [
            job_id
            for job_id, runner in self._runners.items()
            if not runner.is_alive()
        ]
        for job_id in finished[:-keep] if len(finished) > keep else []:
            self._runners.pop(job_id, None)

    def _cleanup_job_artifacts(self, job_id: str) -> None:
        (get_paths().processed / f"{job_id}.mp4").unlink(missing_ok=True)

    def annotated_frame_jpeg(self, job_id: str, t_seconds: float) -> bytes:
        path = get_paths().processed / f"{job_id}.mp4"
        if not path.exists():
            raise KeyError("Annotated preview is not available for this job")
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise MediaError("Could not open the annotated preview")
        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 4.0
            frame_no = max(0, int(round(t_seconds * fps)))
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
            ok, frame = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = cap.read()
            if not ok or frame is None:
                raise MediaError("No frame at that position")
            return encode_jpeg(frame, quality=82)
        finally:
            cap.release()

    def annotated_duration(self, job_id: str) -> float:
        path = get_paths().processed / f"{job_id}.mp4"
        if not path.exists():
            return 0.0
        cap = cv2.VideoCapture(str(path))
        try:
            frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            fps = cap.get(cv2.CAP_PROP_FPS) or 1
            return round(frames / fps, 2) if fps else 0.0
        finally:
            cap.release()

    def shutdown(self) -> None:
        with self._lock:
            runners = list(self._runners.values())
        for runner in runners:
            if runner.is_alive():
                runner.request_stop()
        for runner in runners:
            if runner._thread:
                runner._thread.join(timeout=5)


def probe_video(path: Path) -> dict[str, Any]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise MediaError(
            "OpenCV could not open this video. It may use a codec that needs FFmpeg, "
            "or the file may be corrupt."
        )
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        ok, frame = cap.read()
        if not ok or frame is None:
            raise MediaError("The first frame of this video could not be decoded")
        if width <= 0 or height <= 0:
            height, width = frame.shape[:2]
        if not fps or math.isnan(fps) or fps <= 0 or fps > 240:
            fps = 25.0
        duration = round(frame_count / fps, 2) if frame_count > 0 else 0.0
        return {
            "duration_s": duration,
            "width": width,
            "height": height,
            "fps": round(fps, 3),
            "frame_count": frame_count,
        }
    finally:
        cap.release()


job_manager = JobManager()
