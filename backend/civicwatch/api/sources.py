"""Media, processing jobs, zones and camera endpoints."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from ..config import get_paths
from ..db import audit
from ..services.cameras import CameraError, camera_service
from ..services.jobs import JobOptions, MediaError, job_manager
from ..services.zones import ZoneService
from ..settings_store import all_settings

router = APIRouter()

MJPEG_BOUNDARY = "civicwatchframe"


def _mjpeg_chunk(jpeg: bytes) -> bytes:
    return (
        b"--" + MJPEG_BOUNDARY.encode() + b"\r\n"
        b"Content-Type: image/jpeg\r\n"
        b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n"
    )


def mjpeg_response(source, is_active, fps: float = 8.0) -> StreamingResponse:
    """Multipart JPEG stream used for job and live-camera previews."""

    async def stream():
        interval = 1.0 / max(1.0, fps)
        idle = 0
        while True:
            try:
                frame = source()
            except Exception:
                frame = None
            if frame is None:
                idle += 1
                if not is_active() and idle > 20:
                    break
                await asyncio.sleep(interval)
                continue
            idle = 0
            yield _mjpeg_chunk(frame)
            await asyncio.sleep(interval)
            if not is_active() and idle > 4:
                break

    return StreamingResponse(
        stream(),
        media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}",
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


# --------------------------------------------------------------------------
# media
# --------------------------------------------------------------------------


@router.post("/media")
async def upload_media(
    file: UploadFile = File(...),
    location: str | None = Form(None),
) -> dict[str, Any]:
    try:
        media = job_manager.save_upload(file.file, file.filename or "upload.mp4")
    except MediaError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Upload failed: {exc}") from exc
    if location:
        media["location"] = location
    return media


@router.get("/media")
def list_media() -> dict[str, Any]:
    return {"items": job_manager.list_media()}


@router.get("/media/{media_id}")
def get_media(media_id: str) -> dict[str, Any]:
    media = job_manager.get_media(media_id)
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")
    return media


@router.delete("/media/{media_id}")
def delete_media(media_id: str) -> dict[str, Any]:
    try:
        job_manager.delete_media(media_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Media not found") from exc
    return {"ok": True}


@router.get("/media/{media_id}/frame")
def media_frame(media_id: str, t: float = 0.0) -> Response:
    try:
        jpeg = job_manager.frame_jpeg(media_id, t)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MediaError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------


class JobRequest(BaseModel):
    media_id: str
    sample_every: int | None = Field(default=None, ge=1, le=60)
    imgsz: int | None = Field(default=None, ge=160, le=1920)
    model_id: str | None = None
    max_width: int | None = Field(default=None, ge=320, le=3840)
    location: str | None = None


@router.post("/jobs")
def create_job(payload: JobRequest) -> dict[str, Any]:
    settings = all_settings()
    options = JobOptions(
        sample_every=payload.sample_every or settings["sample_every"],
        imgsz=payload.imgsz or settings["imgsz"],
        model_id=payload.model_id or settings["model_id"],
        max_width=payload.max_width or settings["max_processing_width"],
        location=payload.location,
    )
    try:
        job = job_manager.create_job(payload.media_id, options)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (MediaError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit("job.started", {"id": job["id"], "media": payload.media_id})
    return job


@router.get("/jobs")
def list_jobs(limit: int = 30) -> dict[str, Any]:
    return {"items": job_manager.list_jobs(limit=max(1, min(100, limit)))}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") in {"completed", "stopped", "failed"}:
        job["annotated_duration_s"] = job_manager.annotated_duration(job_id)
    else:
        job["annotated_duration_s"] = None
    return job


@router.post("/jobs/{job_id}/{action}")
def control_job(job_id: str, action: str) -> dict[str, Any]:
    try:
        return job_manager.control(job_id, action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str) -> dict[str, Any]:
    if not job_manager.get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    job_manager.delete_job(job_id)
    return {"ok": True}


@router.get("/jobs/{job_id}/stream")
def job_stream(job_id: str) -> StreamingResponse:
    runner = job_manager.get_runner(job_id)
    if runner is None:
        raise HTTPException(status_code=404, detail="This job is not active")
    return mjpeg_response(
        lambda: runner.latest_jpeg,
        lambda: runner.status in {"processing", "paused", "queued"},
    )


@router.get("/jobs/{job_id}/frame")
def job_frame(job_id: str, t: float = 0.0) -> Response:
    try:
        jpeg = job_manager.annotated_frame_jpeg(job_id, t)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MediaError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/jobs/{job_id}/video")
def job_video(job_id: str) -> FileResponse:
    path = get_paths().processed / f"{job_id}.mp4"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Annotated video not available")
    return FileResponse(path, media_type="video/mp4", filename=f"civicwatch-{job_id}.mp4")


# --------------------------------------------------------------------------
# zones
# --------------------------------------------------------------------------


class ZoneRequest(BaseModel):
    name: str
    kind: str
    polygon: list[list[float]]
    rules: dict[str, Any] = Field(default_factory=dict)
    scope_type: str = "all"
    scope_id: str | None = None
    enabled: bool = True


class ZonePatch(BaseModel):
    name: str | None = None
    kind: str | None = None
    polygon: list[list[float]] | None = None
    rules: dict[str, Any] | None = None
    scope_type: str | None = None
    scope_id: str | None = None
    enabled: bool | None = None


@router.get("/zones/kinds")
def zone_kinds() -> dict[str, Any]:
    return {"items": ZoneService.kinds()}


@router.get("/zones")
def list_zones(scope_type: str | None = None, scope_id: str | None = None) -> dict[str, Any]:
    try:
        return {"items": ZoneService.list(scope_type, scope_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/zones")
def create_zone(payload: ZoneRequest) -> dict[str, Any]:
    try:
        return ZoneService.create(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/zones/{zone_id}")
def update_zone(zone_id: str, payload: ZonePatch) -> dict[str, Any]:
    data = {k: v for k, v in payload.model_dump().items() if v is not None}
    try:
        return ZoneService.update(zone_id, data)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Zone not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/zones/{zone_id}")
def delete_zone(zone_id: str) -> dict[str, Any]:
    try:
        ZoneService.delete(zone_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Zone not found") from exc
    return {"ok": True}


# --------------------------------------------------------------------------
# cameras
# --------------------------------------------------------------------------


class CameraRequest(BaseModel):
    name: str
    source_type: str = "rtsp"
    source: str | None = None
    username: str | None = None
    password: str | None = None
    device_index: int | None = None
    location: str | None = None
    notes: str | None = None
    authorised: bool = False


class CameraPatch(BaseModel):
    name: str | None = None
    source_type: str | None = None
    source: str | None = None
    username: str | None = None
    password: str | None = None
    location: str | None = None
    notes: str | None = None
    authorised: bool | None = None


@router.get("/cameras")
def list_cameras() -> dict[str, Any]:
    return {"items": camera_service.list_cameras(), "webcams_available": False}


@router.get("/cameras/webcams/discover")
def discover_webcams() -> dict[str, Any]:
    return {"items": camera_service.discover_webcams()}


@router.post("/cameras")
def create_camera(payload: CameraRequest) -> dict[str, Any]:
    try:
        return camera_service.create(payload.model_dump())
    except CameraError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/cameras/{camera_id}")
def update_camera(camera_id: str, payload: CameraPatch) -> dict[str, Any]:
    data = {k: v for k, v in payload.model_dump().items() if v is not None}
    try:
        return camera_service.update(camera_id, data)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Camera not found") from exc
    except CameraError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/cameras/{camera_id}")
def delete_camera(camera_id: str) -> dict[str, Any]:
    try:
        camera_service.delete(camera_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Camera not found") from exc
    return {"ok": True}


@router.post("/cameras/{camera_id}/test")
def test_camera(camera_id: str) -> dict[str, Any]:
    try:
        return camera_service.test(camera_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Camera not found") from exc
    except CameraError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/cameras/{camera_id}/start")
def start_camera(camera_id: str) -> dict[str, Any]:
    try:
        return camera_service.start(camera_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Camera not found") from exc
    except CameraError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/cameras/{camera_id}/stop")
def stop_camera(camera_id: str) -> dict[str, Any]:
    return camera_service.stop(camera_id)


@router.get("/cameras/{camera_id}/stream")
def camera_stream(camera_id: str) -> StreamingResponse:
    camera = camera_service.get(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    session = camera_service.session(camera_id)

    def current_frame():
        active = camera_service.session(camera_id)
        if active is not None and active.latest_jpeg:
            return active.latest_jpeg
        return None

    return mjpeg_response(
        current_frame,
        lambda: bool(session and session.is_alive()),
        fps=6.0,
    )


@router.get("/cameras/{camera_id}/frame")
def camera_frame(camera_id: str, annotated: bool = False) -> Response:
    try:
        jpeg = camera_service.frame(camera_id, annotated=annotated)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Camera not found") from exc
    except CameraError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
