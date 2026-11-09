"""Live monitoring: webcams and authorised RTSP/NVR streams.

Security notes
--------------
* URL credentials are encrypted at rest (Fernet, key in ``data/secrets`` with
  0600 permissions). The plaintext URL never leaves this module except as the
  argument to ``cv2.VideoCapture``.
* Every API response uses a masked display URL — no usernames, passwords or
  tokens are ever returned to the browser.
* Errors are redacted before they are logged or shown, because libav likes to
  echo the full connection string back in its messages.
"""

from __future__ import annotations

import json
import logging
import platform
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import cv2

from ..config import get_paths
from ..db import audit, execute, query, query_one, utc_now
from ..detect import registry
from ..detect.device import resolve_device
from ..detect.yolo import ModelNotAvailable
from ..pipeline.annotate import encode_jpeg
from ..pipeline.engine import FrameProcessor, SourceInfo
from ..security import decrypt, encrypt, redact
from ..settings_store import all_settings
from .events import publish
from .incidents import IncidentService
from .zones import ZoneService

log = logging.getLogger("civicwatch.cameras")

SOURCE_TYPES = ("webcam", "rtsp", "http")


class CameraError(ValueError):
    pass


# --------------------------------------------------------------------------
# URL helpers
# --------------------------------------------------------------------------


def build_rtsp_url(url: str, username: str | None, password: str | None) -> str:
    url = url.strip()
    if not url:
        raise CameraError("A stream URL is required")
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"rtsp", "rtsps", "http", "https", "rtmp"}:
        raise CameraError("Stream URL must start with rtsp://, rtsps://, http:// or https://")
    if not parts.hostname:
        raise CameraError("Stream URL is missing a host")
    if username:
        credentials = quote(username, safe="")
        if password:
            credentials += ":" + quote(password, safe="")
        netloc = f"{credentials}@{parts.netloc.split('@')[-1]}"
        parts = parts._replace(netloc=netloc)
    return urlunsplit(parts)


def display_url(url: str) -> str:
    """Host-only preview of a stream URL, never including credentials."""
    try:
        parts = urlsplit(decrypt(url))
    except Exception:
        try:
            parts = urlsplit(url)
        except Exception:
            return "hidden"
    host = parts.hostname or "hidden"
    if parts.port:
        host = f"{host}:{parts.port}"
    return f"{parts.scheme}://{host}/***"


def open_capture(target: str | int) -> cv2.VideoCapture:
    """Open a capture with the best available backend for the platform."""
    if isinstance(target, int):
        if platform.system() == "Darwin" and hasattr(cv2, "CAP_AVFOUNDATION"):
            return cv2.VideoCapture(target, cv2.CAP_AVFOUNDATION)
        return cv2.VideoCapture(target)
    params: list[int] = []
    if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
        params += [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000]
    if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
        params += [cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8000]
    if hasattr(cv2, "CAP_FFMPEG"):
        try:
            return cv2.VideoCapture(target, cv2.CAP_FFMPEG, params)
        except Exception:  # pragma: no cover - backend differences
            pass
    return cv2.VideoCapture(target)


# --------------------------------------------------------------------------
# Live session
# --------------------------------------------------------------------------


class LiveSession:
    def __init__(self, camera: dict[str, Any], target: str | int) -> None:
        self.camera = camera
        self.target = target
        self.status = "connecting"
        self.error: str | None = None
        self.fps = 0.0
        self.frames = 0
        self.detections = 0
        self.incidents = 0
        self.device: str | None = None
        self.latest_jpeg: bytes | None = None
        self.latest_raw_jpeg: bytes | None = None
        self.started_at = utc_now()
        self.resolution: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name=f"live-{self.camera['id']}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def snapshot(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera["id"],
            "status": self.status,
            "error": redact(self.error) if self.error else None,
            "fps": round(self.fps, 2),
            "frames": self.frames,
            "detections": self.detections,
            "incidents": self.incidents,
            "device": self.device,
            "started_at": self.started_at,
            "resolution": self.resolution,
            "has_frame": self.latest_jpeg is not None,
        }

    # -- loop ------------------------------------------------------------
    def _run(self) -> None:
        settings = all_settings(refresh=True)
        sample_every = max(1, int(settings.get("live_sample_every", 5)))
        imgsz = int(settings["imgsz"])
        cap: cv2.VideoCapture | None = None
        processor: FrameProcessor | None = None
        try:
            device_info = resolve_device(settings["device_preference"])
            self.device = device_info.device
            detector = registry.get_detector(
                model_id=settings["model_id"],
                device=device_info.device,
                confidence=float(settings["confidence"]),
                iou=float(settings["iou_threshold"]),
                imgsz=imgsz,
            )
            zones = ZoneService.for_source("camera", self.camera["id"])
            processor = FrameProcessor(
                detector=detector,
                source=SourceInfo(
                    kind=self.camera["source_type"],
                    ref=self.camera["id"],
                    camera=self.camera["name"],
                    location=self.camera.get("location"),
                ),
                settings=settings,
                zones=zones,
                custom_detectors=registry.active_custom_detectors(
                    device_info.device, float(settings["confidence"]), imgsz
                ),
                on_incident=self._on_incident,
                evidence_dir=get_paths().evidence,
                clip_fps=max(1.5, min(15.0, 12.0 / sample_every)),
            )

            cap = open_capture(self.target)
            if not cap.isOpened():
                self._fail(
                    "Could not connect to this camera. Check that the stream URL is reachable, "
                    "that the camera is on the same network, and that the credentials are correct."
                )
                return
            self.status = "live"
            publish("live.status", self.snapshot())
            started = time.monotonic()
            last_count, last_time = 0, started
            read_failures = 0
            raw_index = 0

            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    read_failures += 1
                    if read_failures % 40 == 0:
                        log.warning("Camera %s read failure (%s)", self.camera["id"], read_failures)
                    if read_failures > 120:
                        self._fail("Lost the camera stream. Monitoring stopped.")
                        return
                    time.sleep(0.05)
                    continue
                read_failures = 0
                raw_index += 1
                height, width = frame.shape[:2]
                self.resolution = f"{width}x{height}"
                if raw_index % max(1, sample_every) != 0:
                    continue
                self.latest_raw_jpeg = encode_jpeg(frame, quality=80)
                t_seconds = time.monotonic() - started
                result = processor.process(
                    frame,
                    t_seconds,
                    wall_clock=datetime.now(timezone.utc),
                    incident_id_factory=IncidentService.reserve_id,
                )
                self.frames += 1
                self.detections = processor.total_detections
                self.incidents = processor.total_incidents
                self.latest_jpeg = encode_jpeg(result.annotated, quality=75)
                now = time.monotonic()
                if now - last_time >= 1.0:
                    self.fps = (self.frames - last_count) / (now - last_time)
                    last_count, last_time = self.frames, now
                    publish("live.status", self.snapshot())
        except ModelNotAvailable as exc:
            self._fail(str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("Live session failed for camera %s", self.camera["id"])
            self._fail(f"Monitoring stopped: {redact(str(exc))}")
        finally:
            self._stop.set()
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            if processor is not None:
                processor.close()
            if self.status != "error":
                self.status = "stopped"
            publish("live.status", self.snapshot())
            _persist_status(self.camera["id"], self.status, self.error)
            if self.incidents:
                audit("live.session", {"camera": self.camera["id"], "incidents": self.incidents})

    def _fail(self, message: str) -> None:
        self.status = "error"
        self.error = message
        log.warning("Camera %s error: %s", self.camera["id"], redact(message))
        publish("live.status", self.snapshot())

    def _on_incident(self, incident: dict[str, Any]) -> None:
        publish(
            "incident.created",
            {
                key: incident.get(key)
                for key in (
                    "id", "category", "label", "confidence", "camera", "captured_at",
                    "severity", "status", "zone_name", "track_id", "source_type",
                    "source_ref", "has_snapshot",
                )
            },
        )


def _persist_status(camera_id: str, status: str, error: str | None) -> None:
    execute(
        "UPDATE cameras SET last_status = ?, last_error = ?, updated_at = ? WHERE id = ?",
        (status, redact(error) if error else None, utc_now(), camera_id),
    )


# --------------------------------------------------------------------------
# Camera service
# --------------------------------------------------------------------------


class CameraService:
    def __init__(self) -> None:
        self._sessions: dict[str, LiveSession] = {}
        self._lock = threading.Lock()

    # -- CRUD ------------------------------------------------------------
    def list_cameras(self) -> list[dict[str, Any]]:
        rows = query("SELECT * FROM cameras ORDER BY created_at ASC")
        return [self._decorate(dict(row)) for row in rows]

    def get(self, camera_id: str) -> dict[str, Any] | None:
        row = query_one("SELECT * FROM cameras WHERE id = ?", (camera_id,))
        return self._decorate(dict(row)) if row else None

    def _decorate(self, camera: dict[str, Any]) -> dict[str, Any]:
        raw_source = camera.pop("source_value", "")
        camera["display_source"] = (
            f"Built-in camera #{raw_source}" if camera["source_type"] == "webcam" else display_url(raw_source)
        )
        camera["has_credentials"] = camera["source_type"] != "webcam"
        camera["authorised"] = bool(camera["authorised"])
        session = self._sessions.get(camera["id"])
        camera["session"] = session.snapshot() if session else None
        camera["monitoring"] = bool(session and session.is_alive())
        return camera

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name", "")).strip()
        if not name:
            raise CameraError("Camera name is required")
        source_type = str(payload.get("source_type", "rtsp"))
        if source_type not in SOURCE_TYPES:
            raise CameraError("Camera type must be webcam, rtsp or http")
        if not payload.get("authorised"):
            raise CameraError(
                "You must confirm that you are authorised to monitor this camera before adding it."
            )
        stored, location = self._encode_source(source_type, payload)
        camera_id = f"cam-{uuid.uuid4().hex[:10]}"
        now = utc_now()
        execute(
            """INSERT INTO cameras (id, name, source_type, source_value, location, notes,
               authorised, created_at, updated_at, last_status) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                camera_id, name, source_type, stored, location,
                payload.get("notes"), 1, now, now, "idle",
            ),
        )
        audit("camera.created", {"id": camera_id, "type": source_type})
        return self.get(camera_id) or {}

    def update(self, camera_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        existing = query_one("SELECT * FROM cameras WHERE id = ?", (camera_id,))
        if not existing:
            raise KeyError("Camera not found")
        updates: dict[str, Any] = {}
        if "name" in payload:
            name = str(payload["name"]).strip()
            if not name:
                raise CameraError("Camera name cannot be empty")
            updates["name"] = name
        for field in ("location", "notes"):
            if field in payload:
                updates[field] = payload[field]
        if "authorised" in payload:
            updates["authorised"] = 1 if payload["authorised"] else 0
        if payload.get("source") or payload.get("username") or payload.get("password"):
            source_type = payload.get("source_type", existing["source_type"])
            stored, location = self._encode_source(source_type, payload)
            updates["source_type"] = source_type
            updates["source_value"] = stored
            if location:
                updates["location"] = location
        if not updates:
            return self.get(camera_id) or {}
        updates["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        execute(f"UPDATE cameras SET {assignments} WHERE id = ?", [*updates.values(), camera_id])
        audit("camera.updated", {"id": camera_id})
        return self.get(camera_id) or {}

    def delete(self, camera_id: str) -> None:
        if not query_one("SELECT id FROM cameras WHERE id = ?", (camera_id,)):
            raise KeyError("Camera not found")
        self.stop(camera_id)
        execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
        audit("camera.deleted", {"id": camera_id})

    def _encode_source(self, source_type: str, payload: dict[str, Any]) -> tuple[str, str | None]:
        if source_type == "webcam":
            index = payload.get("device_index", 0)
            try:
                index = int(index)
            except (TypeError, ValueError) as exc:
                raise CameraError("Camera index must be a number") from exc
            if not 0 <= index <= 32:
                raise CameraError("Camera index must be between 0 and 32")
            return str(index), payload.get("location") or "This Mac"
        url = str(payload.get("source") or payload.get("url") or "").strip()
        if not url:
            raise CameraError("A stream URL is required")
        merged = build_rtsp_url(url, payload.get("username"), payload.get("password"))
        location = payload.get("location")
        if not location:
            try:
                location = urlsplit(merged).hostname
            except Exception:
                location = None
        return encrypt(merged), location

    # -- connection testing ----------------------------------------------
    def test(self, camera_id: str) -> dict[str, Any]:
        row = query_one("SELECT * FROM cameras WHERE id = ?", (camera_id,))
        if not row:
            raise KeyError("Camera not found")
        target = self._resolve_target(dict(row))
        started = time.monotonic()
        cap = open_capture(target)
        try:
            if not cap.isOpened():
                return {
                    "ok": False,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "message": "Could not open this stream. Check the URL, network access and credentials.",
                }
            ok, frame = cap.read()
            latency = int((time.monotonic() - started) * 1000)
            if not ok or frame is None:
                return {
                    "ok": False,
                    "latency_ms": latency,
                    "message": "Connected but no frames arrived. The stream may be offline.",
                }
            height, width = frame.shape[:2]
            return {
                "ok": True,
                "latency_ms": latency,
                "resolution": f"{width}x{height}",
                "message": f"Connected. First frame {width}x{height} received in {latency} ms.",
            }
        finally:
            try:
                cap.release()
            except Exception:
                pass

    def discover_webcams(self, max_index: int = 4) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for index in range(max_index + 1):
            cap = open_capture(index)
            try:
                available = cap.isOpened()
                frame = None
                if available:
                    ok, frame = cap.read()
                    available = ok and frame is not None
                found.append(
                    {
                        "index": index,
                        "label": f"Camera {index}",
                        "available": bool(available),
                        "resolution": f"{frame.shape[1]}x{frame.shape[0]}" if available and frame is not None else None,
                    }
                )
            finally:
                try:
                    cap.release()
                except Exception:
                    pass
        return found

    # -- sessions --------------------------------------------------------
    def start(self, camera_id: str) -> dict[str, Any]:
        row = query_one("SELECT * FROM cameras WHERE id = ?", (camera_id,))
        if not row:
            raise KeyError("Camera not found")
        camera = dict(row)
        if not camera["authorised"]:
            raise CameraError(
                "This camera is not marked as authorised. Confirm authorisation before monitoring."
            )
        with self._lock:
            existing = self._sessions.get(camera_id)
            if existing and existing.is_alive():
                return existing.snapshot()
            target = self._resolve_target(camera)
            session = LiveSession(camera, target)
            self._sessions[camera_id] = session
        session.start()
        audit("camera.monitoring_started", {"id": camera_id})
        return session.snapshot()

    def stop(self, camera_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(camera_id)
        if not session:
            return {"camera_id": camera_id, "status": "idle"}
        session.stop()
        for _ in range(60):
            if not session.is_alive():
                break
            time.sleep(0.1)
        with self._lock:
            self._sessions.pop(camera_id, None)
        _persist_status(camera_id, "stopped", None)
        audit("camera.monitoring_stopped", {"id": camera_id})
        return {"camera_id": camera_id, "status": "stopped"}

    def session(self, camera_id: str) -> LiveSession | None:
        session = self._sessions.get(camera_id)
        return session if session and session.is_alive() else session

    def frame(self, camera_id: str, annotated: bool = True) -> bytes:
        session = self._sessions.get(camera_id)
        if session and session.is_alive():
            data = session.latest_jpeg if annotated else session.latest_raw_jpeg
            if data:
                return data
        row = query_one("SELECT * FROM cameras WHERE id = ?", (camera_id,))
        if not row:
            raise KeyError("Camera not found")
        cap = open_capture(self._resolve_target(dict(row)))
        try:
            if not cap.isOpened():
                raise CameraError("Could not connect to the camera to capture a frame")
            ok, frame = cap.read()
            if not ok or frame is None:
                raise CameraError("The camera did not return a frame")
            return encode_jpeg(frame, quality=85)
        finally:
            try:
                cap.release()
            except Exception:
                pass

    def _resolve_target(self, camera: dict[str, Any]) -> str | int:
        if camera["source_type"] == "webcam":
            try:
                return int(camera["source_value"])
            except (TypeError, ValueError) as exc:
                raise CameraError("Stored camera index is invalid") from exc
        try:
            return decrypt(camera["source_value"])
        except ValueError as exc:
            raise CameraError(
                "Stored camera credentials could not be decrypted. Re-enter the stream URL."
            ) from exc

    def shutdown(self) -> None:
        with self._lock:
            sessions = list(self._sessions.items())
        for camera_id, session in sessions:
            session.stop()
        for camera_id, session in sessions:
            if session._thread:
                session._thread.join(timeout=5)


camera_service = CameraService()
