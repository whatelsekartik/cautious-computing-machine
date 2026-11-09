"""HTTP surface of the hosted deployment.

Separate from the local routers on purpose: the cloud function must never
import torch/OpenCV, and several local endpoints (jobs, cameras, zone drawing,
model downloads) only make sense on the machine that performs inference.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from ..config import APP_NAME, APP_VERSION, SAFETY_DISCLAIMER
from ..db import audit, execute, query, query_one
from ..labels import STATUS_LABELS
from ..services.incidents import VALID_STATUSES, IncidentService
from ..services.reports import build_incident_report
from ..settings_store import all_settings
from ..storage import StorageError, get_storage
from . import service
from .auth import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    admin_token,
    auth_configured,
    bearer_token,
    cloud_mode,
    compare,
    issue_session,
    request_is_dashboard_authenticated,
)
from .schemas import (
    AssignmentUpdate,
    ConnectorCreate,
    EvidenceUploadRequest,
    HeartbeatPayload,
    IncidentBatch,
    ModelInfo,
    ProcessedUploadRequest,
    QueueJobRequest,
    SessionRequest,
    SettingsPatch,
    UploadRequest,
)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def require_admin(request: Request) -> None:
    if not auth_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "Dashboard authentication is not configured. Set CIVICWATCH_ADMIN_TOKEN "
                "in the deployment environment."
            ),
        )
    if not request_is_dashboard_authenticated(request):
        raise HTTPException(
            status_code=401,
            detail="Sign in with the dashboard token to continue.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_connector(request: Request) -> dict[str, Any]:
    token = bearer_token(request.headers.get("authorization"))
    connector = service.authenticate_connector(token)
    if not connector:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing connector token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return connector


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

public = APIRouter()


@public.get("/health")
def health():
    payload: dict[str, Any] = {"status": "ok", "app": APP_NAME, "version": APP_VERSION, "mode": "cloud"}
    try:
        query_one("SELECT 1 AS ok")
        payload["database"] = "reachable"
    except Exception as exc:  # noqa: BLE001 - health must answer, not crash
        payload.update({"status": "degraded", "database": "unreachable", "detail": str(exc)[:200]})
        return JSONResponse(status_code=503, content=payload)
    return payload


@public.get("/cloud/config")
def cloud_config() -> dict[str, Any]:
    deployment = service.deployment_info()
    return {
        "app": {"name": APP_NAME, "version": APP_VERSION},
        "mode": "cloud",
        "auth_required": auth_configured(),
        "deployment": deployment,
        "capabilities": deployment["capabilities"],
        "limits": {
            "max_upload_bytes": 4 * 1024**3,
            "max_json_request_bytes": 4 * 1024 * 1024,
            "function_timeout_s": 60,
            "incidents_per_batch": 50,
        },
    }


auth = APIRouter()


@auth.post("/auth/session")
def create_session(payload: SessionRequest, request: Request, response: Response) -> dict[str, Any]:
    if not auth_configured():
        raise HTTPException(status_code=503, detail="Dashboard authentication is not configured")
    if not compare(payload.token.strip(), admin_token()):
        raise HTTPException(status_code=401, detail="That token is not correct")
    value, expires = issue_session()
    secure = request.url.scheme == "https" or bool(request.headers.get("x-forwarded-proto") == "https")
    response.set_cookie(
        SESSION_COOKIE,
        value,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="strict",
        secure=secure,
        path="/",
    )
    audit("auth.session_created", {"ip": request.client.host if request.client else "unknown"}, actor="dashboard")
    return {"ok": True, "expires_at": expires, "mode": "cloud"}


@auth.delete("/auth/session")
def delete_session(response: Response) -> dict[str, Any]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@auth.get("/auth/status")
def auth_status(request: Request) -> dict[str, Any]:
    return {
        "mode": "cloud" if cloud_mode() else "local",
        "auth_required": auth_configured(),
        "authenticated": request_is_dashboard_authenticated(request),
    }


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

dashboard = APIRouter(dependencies=[Depends(require_admin)])


def _actor() -> str:
    return all_settings().get("operator_name") or "dashboard operator"


@dashboard.get("/overview")
def overview() -> dict[str, Any]:
    return service.overview()


@dashboard.get("/system/info")
def system_info() -> dict[str, Any]:
    return service.system_info()


@dashboard.get("/system/diagnostics")
def diagnostics() -> dict[str, Any]:
    return service.cloud_diagnostics()


@dashboard.get("/settings")
def get_settings() -> dict[str, Any]:
    values = all_settings(refresh=True)
    device = service.deployment_info()["capabilities"]
    from ..settings_store import DEFAULT_SETTINGS

    return {
        "values": values,
        "defaults": DEFAULT_SETTINGS,
        "device": {
            "device": "connector",
            "reason": "Inference runs on the paired local connector; these settings are sent to it",
        },
        "capabilities": device,
        "mode": "cloud",
    }


@dashboard.patch("/settings")
def patch_settings(payload: SettingsPatch) -> dict[str, Any]:
    try:
        values = service.update_settings_patch(payload.as_dict(), actor=_actor())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"values": values}


@dashboard.post("/settings/reset/{key}")
def reset_setting(key: str) -> dict[str, Any]:
    from ..settings_store import DEFAULT_SETTINGS, all_settings as current

    if key not in DEFAULT_SETTINGS:
        raise HTTPException(status_code=404, detail="Unknown setting")
    try:
        values = service.update_settings_patch({key: DEFAULT_SETTINGS[key]}, actor=_actor())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"values": values, "reset": key, "current": current(refresh=False)}


@dashboard.get("/models")
def models() -> dict[str, Any]:
    return service.model_catalogue()


@dashboard.get("/incidents")
def list_incidents(
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
    try:
        return IncidentService.list(
            status=status,
            category=category,
            search=search,
            severity=severity,
            source_type=source_type,
            source_ref=source_ref,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
            sort=sort,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@dashboard.get("/incidents/stats")
def incident_stats() -> dict[str, Any]:
    return IncidentService.stats()


@dashboard.get("/incidents/categories")
def incident_categories() -> dict[str, Any]:
    return {"items": IncidentService.categories(), "statuses": list(VALID_STATUSES)}


@dashboard.get("/incidents/export.csv")
def export_csv(status: str | None = None, ids: str | None = None) -> Response:
    listing = _report_items(status, ids)
    text = IncidentService.csv_rows([item["id"] for item in listing])
    audit("incident.exported", {"format": "csv", "count": len(listing)}, actor=_actor())
    return Response(
        content=text.encode("utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="civicwatch-incidents.csv"'},
    )


@dashboard.get("/incidents/export.pdf")
def export_pdf(status: str | None = None, ids: str | None = None) -> Response:
    listing = _report_items(status, ids)
    pdf = build_incident_report(
        listing,
        operator=all_settings().get("operator_name", ""),
        title="CivicWatch — civic violation review report",
    )
    audit("incident.exported", {"format": "pdf", "count": len(listing)}, actor=_actor())
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="civicwatch-incidents.pdf"'},
    )


@dashboard.get("/incidents/{incident_id}")
def get_incident(incident_id: str) -> dict[str, Any]:
    incident = IncidentService.get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    incident["disclaimer"] = SAFETY_DISCLAIMER
    return incident


@dashboard.patch("/incidents/{incident_id}")
def update_incident(incident_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    status = payload.get("status")
    note = payload.get("note")
    try:
        if status:
            if status not in VALID_STATUSES:
                raise HTTPException(status_code=400, detail=f"Unknown status: {status}")
            result = IncidentService.update_status(incident_id, status, note=note, actor=_actor())
            if note:
                result = IncidentService.add_note(incident_id, note, actor=_actor())
            return result
        if note:
            return IncidentService.add_note(incident_id, note, actor=_actor())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Incident not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail="Nothing to update")


@dashboard.delete("/incidents/{incident_id}")
def delete_incident(incident_id: str) -> dict[str, Any]:
    try:
        IncidentService.delete(incident_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Incident not found") from exc
    audit("incident.deleted", {"id": incident_id}, actor=_actor())
    return {"ok": True}


@dashboard.post("/incidents/{incident_id}/notes")
def add_note(incident_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    note = (payload or {}).get("note")
    if not note or not str(note).strip():
        raise HTTPException(status_code=400, detail="Note cannot be empty")
    try:
        return IncidentService.add_note(incident_id, str(note), actor=_actor())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Incident not found") from exc


@dashboard.get("/incidents/{incident_id}/snapshot")
def snapshot(incident_id: str, download: bool = False):
    return _evidence(incident_id, "snapshot", "image/jpeg", download)


@dashboard.get("/incidents/{incident_id}/clip")
def clip(incident_id: str, download: bool = False):
    return _evidence(incident_id, "clip", "video/mp4", download)


@dashboard.get("/incidents/{incident_id}/preview")
def preview(incident_id: str):
    return _evidence(incident_id, "preview", "image/gif", False)


def _evidence(incident_id: str, kind: str, media_type: str, download: bool):
    incident = IncidentService.get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    refs = incident.get("evidence_refs") or {}
    ref = refs.get(kind)
    if not ref and kind == "preview":
        ref = refs.get("snapshot")
        media_type = "image/jpeg"
    if not ref:
        raise HTTPException(status_code=404, detail=f"No {kind} stored for this incident")
    try:
        storage = get_storage()
        if storage.remote:
            url = storage.presign_get(
                ref,
                filename=f"{incident_id}.{_extension(media_type)}",
                disposition="attachment" if download else "inline",
            )
            return RedirectResponse(
                url, status_code=302, headers={"Cache-Control": "private, max-age=60"}
            )
        return FileResponse(storage.materialize(ref), media_type=media_type)
    except StorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _extension(media_type: str) -> str:
    return {"image/jpeg": "jpg", "video/mp4": "mp4", "image/gif": "gif"}.get(media_type, "bin")


@dashboard.get("/audit")
def audit_log(limit: int = 100) -> dict[str, Any]:
    rows = query(
        "SELECT id, created_at, actor, action, detail FROM audit_log ORDER BY id DESC LIMIT ?",
        (max(1, min(500, limit)),),
    )
    return {"items": [dict(row) for row in rows]}


@dashboard.get("/storage")
def storage() -> dict[str, Any]:
    return service.storage_summary()


@dashboard.post("/storage/cleanup")
def storage_cleanup() -> dict[str, Any]:
    raise HTTPException(
        status_code=501,
        detail=(
            "Retention is enforced by the object-store lifecycle policy in cloud mode. "
            "Configure a bucket rule matching your evidence retention setting; "
            "local installs use Settings → storage cleanup."
        ),
    )


@dashboard.get("/connectors")
def connectors() -> dict[str, Any]:
    return {"items": service.list_connectors()}


@dashboard.post("/connectors")
def create_connector(payload: ConnectorCreate) -> dict[str, Any]:
    try:
        return service.create_connector(payload.name, payload.notes)
    except service.CloudError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@dashboard.delete("/connectors/{connector_id}")
def delete_connector(connector_id: str) -> dict[str, Any]:
    try:
        service.delete_connector(connector_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Connector not found") from exc
    return {"ok": True}


# ---------------------------------------------------------------------------
# Media uploads requested from the browser
# ---------------------------------------------------------------------------

@dashboard.post("/media/upload-url")
def media_upload_url(payload: UploadRequest) -> dict[str, Any]:
    try:
        return service.create_upload(
            payload.filename, payload.content_type, payload.size_bytes, actor=_actor()
        )
    except (service.CloudError, StorageError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@dashboard.post("/media/{media_id}/complete")
def media_complete(media_id: str) -> dict[str, Any]:
    try:
        return service.complete_upload(media_id, actor=_actor())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Upload not found") from exc
    except (service.CloudError, StorageError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@dashboard.get("/media")
def list_media(limit: int = 50) -> dict[str, Any]:
    rows = query(
        "SELECT id, original_name, size_bytes, created_at, origin, upload_status, storage_key "
        "FROM media ORDER BY created_at DESC LIMIT ?",
        (max(1, min(200, limit)),),
    )
    return {"items": [dict(row) for row in rows]}


@dashboard.delete("/media/{media_id}")
def delete_media(media_id: str) -> dict[str, Any]:
    row = query_one("SELECT * FROM media WHERE id = ?", (media_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Upload not found")
    if row.get("storage_key"):
        try:
            get_storage().delete(row["storage_key"])
        except StorageError:
            pass
    execute("DELETE FROM media WHERE id = ?", (media_id,))
    audit("media.deleted", {"id": media_id}, actor=_actor())
    return {"ok": True}


@dashboard.post("/jobs")
def queue_job(payload: QueueJobRequest) -> dict[str, Any]:
    try:
        return service.queue_job(payload.media_id, actor=_actor())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Upload not found") from exc
    except (service.CloudError, StorageError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@dashboard.get("/jobs")
def list_jobs(limit: int = 50) -> dict[str, Any]:
    return {"items": service.list_jobs(limit)}


@dashboard.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = service.job_public(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@dashboard.get("/jobs/{job_id}/video")
def job_video(job_id: str):
    row = query_one("SELECT data_json FROM jobs WHERE id = ?", (job_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    data = {}
    try:
        data = json.loads(row["data_json"] or "{}")
    except (TypeError, ValueError):
        data = {}
    key = data.get("processed_key")
    if not key:
        raise HTTPException(status_code=404, detail="No annotated video for this job")
    storage = get_storage()
    if storage.remote:
        return RedirectResponse(storage.presign_get(key, filename=f"{job_id}.mp4"), status_code=302)
    return FileResponse(storage.materialize(key), media_type="video/mp4")


def _report_items(status: str | None, ids: str | None) -> list[dict[str, Any]]:
    selected = [item for item in (ids or "").split(",") if item.strip()]
    if selected:
        items = []
        for incident_id in selected:
            incident = IncidentService.get(incident_id.strip())
            if incident:
                items.append(incident)
        return items
    return IncidentService.list(status=status, limit=500)["items"]


# ---------------------------------------------------------------------------
# Connector protocol
# ---------------------------------------------------------------------------

connector_api = APIRouter()


@connector_api.post("/connector/heartbeat")
def heartbeat(payload: HeartbeatPayload, connector: dict = Depends(require_connector)) -> dict[str, Any]:
    return service.record_heartbeat(connector, payload.model_dump(exclude_none=True))


@connector_api.get("/connector/assignments")
def assignments(
    limit: int = Query(default=3, ge=1, le=10),
    connector: dict = Depends(require_connector),
) -> dict[str, Any]:
    items = service.claim_assignments(connector, limit=limit)
    return {"items": items, "count": len(items)}


@connector_api.post("/connector/assignments/{job_id}")
def assignment_update(
    job_id: str, payload: AssignmentUpdate, connector: dict = Depends(require_connector)
) -> dict[str, Any]:
    try:
        return service.update_assignment(connector, job_id, payload.model_dump(exclude_none=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Assignment not found") from exc
    except service.CloudError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@connector_api.post("/connector/evidence/upload-urls")
def evidence_upload_urls(
    payload: EvidenceUploadRequest, connector: dict = Depends(require_connector)
) -> dict[str, Any]:
    try:
        files = service.issue_upload_urls(
            connector,
            payload.client_incident_id,
            [item.model_dump() for item in payload.files],
        )
    except (service.CloudError, StorageError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"client_incident_id": payload.client_incident_id, "files": files}


@connector_api.post("/connector/incidents")
def ingest(payload: IncidentBatch, connector: dict = Depends(require_connector)) -> dict[str, Any]:
    result = service.ingest_incidents(
        connector, [item.model_dump(exclude_none=True) for item in payload.incidents]
    )
    return result


@connector_api.post("/connector/processed/upload-url")
def processed_upload_url(
    payload: ProcessedUploadRequest, connector: dict = Depends(require_connector)
) -> dict[str, Any]:
    try:
        return service.issue_processed_url(connector, payload.job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Assignment not found") from exc
    except (service.CloudError, StorageError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@connector_api.post("/connector/models")
def report_models(
    models: list[ModelInfo], connector: dict = Depends(require_connector)
) -> dict[str, Any]:
    service._store_models(connector["id"], [model.model_dump(exclude_none=True) for model in models])
    return {"ok": True, "count": len(models)}


@connector_api.get("/connector/config")
def connector_config(connector: dict = Depends(require_connector)) -> dict[str, Any]:
    return {
        "config": service.connector_config(connector),
        "server_time": service.utc_now(),
        "connector_name": connector["name"],
    }


__all__ = [
    "auth",
    "connector_api",
    "dashboard",
    "public",
    "STATUS_LABELS",
]
