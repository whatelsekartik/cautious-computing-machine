"""System, settings, model management, overview, storage and event stream APIs."""

from __future__ import annotations

import asyncio
import json
import queue
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..config import APP_NAME, APP_VERSION, allow_remote, get_paths
from ..db import audit, query
from ..detect import registry
from ..detect.device import resolve_device, torch_status
from ..services import system as system_service
from ..services.events import bus, sse_format
from ..settings_store import DEFAULT_SETTINGS, all_settings, reset_setting, update_settings
from ..security import LocalhostOnlyMiddleware  # noqa: F401  (re-exported for tests)

router = APIRouter()


# --------------------------------------------------------------------------
# meta
# --------------------------------------------------------------------------


@router.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "app": APP_NAME, "version": APP_VERSION}


@router.get("/system/info")
def system_info() -> dict[str, Any]:
    settings = all_settings()
    device = resolve_device(settings["device_preference"])
    return {
        "app": {
            "name": APP_NAME,
            "version": APP_VERSION,
            "data_dir": str(get_paths().data),
            "local_only": not allow_remote(),
            "offline_capable": True,
        },
        "device": device.as_dict(),
        "torch": torch_status(),
        "hardware": system_service.hardware_info(),
        "model": {
            "id": settings["model_id"],
            "installed": registry.weights_path(settings["model_id"]).exists(),
        },
        "privacy": {
            "facial_recognition": False,
            "cloud_inference": False,
            "retention_days": settings["evidence_retention_days"],
        },
    }


@router.get("/system/diagnostics")
def diagnostics() -> dict[str, Any]:
    return system_service.diagnostics()


@router.get("/overview")
def overview() -> dict[str, Any]:
    return system_service.overview()


@router.get("/storage")
def storage() -> dict[str, Any]:
    return system_service.storage_info()


class CleanupRequest(BaseModel):
    evidence_days: int | None = Field(default=None, ge=0, le=3650)
    media_days: int | None = Field(default=None, ge=0, le=3650)
    purge_all: bool = False


@router.post("/storage/cleanup")
def cleanup(payload: CleanupRequest | None = None) -> dict[str, Any]:
    payload = payload or CleanupRequest()
    overrides: dict[str, int] = {}
    if payload.evidence_days is not None:
        overrides["evidence"] = payload.evidence_days
    if payload.media_days is not None:
        overrides["media"] = payload.media_days
    return system_service.cleanup(overrides, purge_all=payload.purge_all)


@router.post("/privacy/purge-evidence")
def purge_evidence() -> dict[str, Any]:
    return system_service.purge_evidence()


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    """Server-sent events for job progress, live camera state and new incidents."""
    q = bus.subscribe()

    async def stream():
        try:
            yield sse_format({"type": "hello", "seq": 0, "data": {"ok": True}})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = q.get_nowait()
                except queue.Empty:
                    message = None
                if message is None:
                    await asyncio.sleep(0.2)
                    continue
                yield sse_format(message)
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------


@router.get("/settings")
def get_settings() -> dict[str, Any]:
    settings = all_settings()
    return {
        "values": settings,
        "defaults": DEFAULT_SETTINGS,
        "device": resolve_device(settings["device_preference"]).as_dict(),
    }


@router.patch("/settings")
def patch_settings(patch: dict[str, Any]) -> dict[str, Any]:
    try:
        values = update_settings(patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if "device_preference" in patch or "model_id" in patch:
        registry.clear_detector_cache()
    audit("settings.updated", patch)
    return {"values": values, "device": resolve_device(values["device_preference"]).as_dict()}


@router.post("/settings/reset/{key}")
def reset(key: str) -> dict[str, Any]:
    try:
        values = reset_setting(key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown setting") from exc
    registry.clear_detector_cache()
    return {"values": values}


# --------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------


@router.get("/models")
def models() -> dict[str, Any]:
    settings = all_settings()
    device = resolve_device(settings["device_preference"])
    return {
        "builtin": registry.list_builtin_models(),
        "custom": registry.custom_models(),
        "categories": registry.category_status(),
        "selected": settings["model_id"],
        "device": device.as_dict(),
        "downloads": registry.download_states(),
    }


@router.post("/models/{model_id}/download")
def download_model(model_id: str, force: bool = False) -> dict[str, Any]:
    try:
        state = registry.download_model(model_id, force=force)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit("model.download_started", {"model": model_id, "force": force})
    return state.__dict__


@router.delete("/models/{model_id}")
def delete_model(model_id: str) -> dict[str, Any]:
    try:
        path = registry.weights_path(model_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    running = [
        job
        for job in (system_service.job_manager_snapshot())
        if job.get("status") in {"processing", "paused"}
    ]
    if running:
        raise HTTPException(
            status_code=409, detail="Wait for running jobs to finish before removing a model"
        )
    path.unlink(missing_ok=True)
    registry.clear_detector_cache()
    audit("model.deleted", {"model": model_id})
    return {"ok": True}


@router.post("/models/custom")
def install_custom(
    file: UploadFile = File(...),
    name: str = Form(...),
    category: str = Form(...),
    license: str = Form(""),
    source: str = Form(""),
) -> dict[str, Any]:
    if category not in registry.CUSTOM_CATEGORIES:
        raise HTTPException(status_code=400, detail="Unsupported category")
    suffix = Path(file.filename or "model.pt").suffix.lower()
    if suffix not in {".pt", ".onnx"}:
        raise HTTPException(status_code=400, detail="Upload a .pt weights file")
    if suffix == ".onnx":
        raise HTTPException(
            status_code=400,
            detail="ONNX custom models are not supported yet; export to Ultralytics .pt",
        )
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pt") as handle:
        temp_path = Path(handle.name)
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    try:
        record = registry.install_custom_model(
            temp_path, name=name, category=category, license_text=license, source=source
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        temp_path.unlink(missing_ok=True)
    audit("model.custom_installed", {"id": record["id"], "category": category})
    return record


@router.patch("/models/custom/{model_id}")
def toggle_custom(model_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return registry.set_custom_model_enabled(model_id, bool(payload.get("enabled", True)))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Model not found") from exc


@router.delete("/models/custom/{model_id}")
def delete_custom(model_id: str) -> dict[str, Any]:
    try:
        registry.delete_custom_model(model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Model not found") from exc
    return {"ok": True}


# --------------------------------------------------------------------------
# misc
# --------------------------------------------------------------------------


@router.get("/audit")
def audit_log(limit: int = 100) -> dict[str, Any]:
    rows = query(
        "SELECT created_at, actor, action, detail FROM audit_log ORDER BY id DESC LIMIT ?",
        (max(1, min(500, limit)),),
    )
    return {"items": [dict(row) for row in rows]}


@router.get("/files/{kind}/{name}")
def serve_local_file(kind: str, name: str) -> FileResponse:
    """Serve generated exports (PDF/CSV) stored in the data directory."""
    if kind not in {"exports"}:
        raise HTTPException(status_code=404, detail="Unknown file kind")
    safe = Path(name).name
    path = get_paths().data / kind / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, filename=safe)
