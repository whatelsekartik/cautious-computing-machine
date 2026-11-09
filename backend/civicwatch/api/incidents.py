"""Incident review, evidence delivery and report export."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from ..config import SAFETY_DISCLAIMER, get_paths
from ..db import audit
from ..services.incidents import VALID_STATUSES, IncidentService
from ..services.reports import build_incident_report
from ..storage import StorageError, evidence_keys, get_storage
from ..settings_store import all_settings

router = APIRouter()


class StatusUpdate(BaseModel):
    status: str | None = None
    note: str | None = Field(default=None, max_length=2000)


class NoteRequest(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


@router.get("/incidents")
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


@router.get("/incidents/stats")
def stats() -> dict[str, Any]:
    return IncidentService.stats()


@router.get("/incidents/categories")
def categories() -> dict[str, Any]:
    return {"items": IncidentService.categories(), "statuses": list(VALID_STATUSES)}


@router.get("/incidents/export.csv")
def export_csv(status: str | None = None, ids: str | None = None) -> FileResponse:
    selected = [i for i in (ids or "").split(",") if i]
    listing = _report_items(status, selected)
    csv_text = IncidentService.csv_rows([i["id"] for i in listing])
    path = _export_path("csv")
    path.write_text(csv_text, encoding="utf-8")
    audit("incident.exported", {"format": "csv", "count": len(listing)})
    return FileResponse(path, media_type="text/csv", filename=path.name)


@router.get("/incidents/export.pdf")
def export_pdf(status: str | None = None, ids: str | None = None) -> FileResponse:
    selected = [i for i in (ids or "").split(",") if i]
    listing = _report_items(status, selected)
    settings = all_settings()
    pdf = build_incident_report(
        listing,
        operator=settings.get("operator_name", ""),
        title="CivicWatch — civic violation review report",
    )
    path = _export_path("pdf")
    path.write_bytes(pdf)
    audit("incident.exported", {"format": "pdf", "count": len(listing)})
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: str) -> dict[str, Any]:
    incident = IncidentService.get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    incident["disclaimer"] = SAFETY_DISCLAIMER
    return incident


@router.patch("/incidents/{incident_id}")
def update_incident(incident_id: str, payload: StatusUpdate) -> dict[str, Any]:
    try:
        if payload.status:
            result = IncidentService.update_status(
                incident_id,
                payload.status,
                note=payload.note,
                actor=all_settings().get("operator_name") or "local operator",
            )
            if payload.note and payload.status:
                result = IncidentService.add_note(
                    incident_id,
                    payload.note,
                    actor=all_settings().get("operator_name") or "local operator",
                )
            return result
        if payload.note:
            return IncidentService.add_note(incident_id, payload.note)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Incident not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail="Nothing to update")


@router.post("/incidents/{incident_id}/notes")
def add_note(incident_id: str, payload: NoteRequest) -> dict[str, Any]:
    try:
        return IncidentService.add_note(
            incident_id, payload.note, actor=all_settings().get("operator_name") or "local operator"
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Incident not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/incidents/{incident_id}")
def delete_incident(incident_id: str) -> dict[str, Any]:
    try:
        IncidentService.delete(incident_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Incident not found") from exc
    return {"ok": True}


@router.get("/incidents/{incident_id}/snapshot")
def snapshot(incident_id: str, download: bool = False):
    incident = IncidentService.get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return _evidence_response(
        evidence_keys(incident)["snapshot"],
        media_type="image/jpeg",
        filename=f"{incident_id}.jpg",
        download=download,
        missing="No snapshot stored for this incident",
    )


@router.get("/incidents/{incident_id}/clip")
def clip(incident_id: str, download: bool = False):
    incident = IncidentService.get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return _evidence_response(
        evidence_keys(incident)["clip"],
        media_type="video/mp4",
        filename=f"{incident_id}.mp4",
        download=download,
        missing="No clip stored for this incident",
    )


@router.get("/incidents/{incident_id}/preview")
def preview(incident_id: str):
    incident = IncidentService.get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    refs = evidence_keys(incident)
    storage = get_storage()
    if refs["preview"] and (storage.remote or _safe_evidence_path(refs["preview"])):
        return _evidence_response(
            refs["preview"],
            media_type="image/gif",
            filename=f"{incident_id}.gif",
            download=False,
            missing="No preview available",
        )
    if refs["snapshot"]:
        return _evidence_response(
            refs["snapshot"],
            media_type="image/jpeg",
            filename=f"{incident_id}.jpg",
            download=False,
            missing="No preview available",
        )
    raise HTTPException(status_code=404, detail="No preview available")


def _evidence_response(
    ref: str | None,
    *,
    media_type: str,
    filename: str,
    download: bool,
    missing: str,
):
    """Serve local evidence or redirect to a short-lived presigned URL."""
    if not ref:
        raise HTTPException(status_code=404, detail=missing)
    storage = get_storage()
    if storage.remote:
        try:
            url = storage.presign_get(
                ref,
                filename=filename,
                disposition="attachment" if download else "inline",
            )
        except StorageError as exc:
            raise HTTPException(status_code=503, detail=f"Evidence storage unavailable: {exc}") from exc
        return RedirectResponse(url, status_code=302, headers={"Cache-Control": "private, max-age=60"})
    path = _safe_evidence_path(ref)
    if not path:
        raise HTTPException(status_code=404, detail=missing)
    return FileResponse(path, media_type=media_type, filename=filename)


def _safe_evidence_path(raw: str | None) -> Path | None:
    if not raw:
        return None
    path = Path(raw).resolve()
    evidence_root = get_paths().evidence.resolve()
    try:
        path.relative_to(evidence_root)
    except ValueError:
        return None
    return path if path.exists() else None


def _export_path(extension: str) -> Path:
    directory = get_paths().data / "exports"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return directory / f"civicwatch-incidents-{stamp}.{extension}"


def _report_items(status: str | None, ids: list[str]) -> list[dict[str, Any]]:
    if ids:
        items = []
        for incident_id in ids:
            item = IncidentService.get(incident_id)
            if item:
                items.append(item)
        return items
    listing = IncidentService.list(status=status or "detected,pending_review,verified,forwarded,resolved,dismissed", limit=500)
    full = [IncidentService.get(item["id"]) for item in listing["items"]]
    return [item for item in full if item]
