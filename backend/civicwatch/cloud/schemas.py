"""Request models for the cloud API.

``extra="forbid"`` is deliberate: a connector must not be able to smuggle
camera credentials, stream URLs or unknown fields into the hosted database.
Rejected payloads are returned to the connector with a reason.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelInfo(Strict):
    id: str = Field(min_length=1, max_length=80)
    name: str | None = Field(default=None, max_length=120)
    family: Literal["vehicle_pedestrian", "pothole", "garbage"]
    classes: list[str] | dict[str, str] | None = None
    license: str | None = Field(default=None, max_length=40)
    installed_at: str | None = None


class HeartbeatPayload(Strict):
    version: str | None = Field(default=None, max_length=40)
    device: str | None = Field(default=None, max_length=40)
    capabilities: dict[str, Any] | None = None
    models: dict[str, Any] | None = None
    models_installed: list[ModelInfo] | None = None


class EvidenceRefs(Strict):
    snapshot_key: str | None = Field(default=None, max_length=200)
    clip_key: str | None = Field(default=None, max_length=200)
    preview_key: str | None = Field(default=None, max_length=200)


class IncidentPayload(Strict):
    client_incident_id: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=40)
    confidence: float = Field(gt=0, le=1)
    camera: str = Field(min_length=1, max_length=120)
    location: str | None = Field(default=None, max_length=160)
    captured_at: str = Field(min_length=1, max_length=40)
    track_id: int | None = None
    zone_id: str | None = Field(default=None, max_length=80)
    zone_name: str | None = Field(default=None, max_length=120)
    dwell_seconds: float | None = Field(default=None, ge=0)
    class_name: str | None = Field(default=None, max_length=40)
    severity: Literal["low", "medium", "high"] = "medium"
    source_type: Literal["video", "live"] = "video"
    source_ref: str | None = Field(default=None, max_length=120)
    evidence: EvidenceRefs | None = None
    metadata: dict[str, Any] | None = None


class IncidentBatch(Strict):
    incidents: list[IncidentPayload] = Field(min_length=1, max_length=50)


class EvidenceFileRequest(Strict):
    kind: Literal["snapshot", "clip", "preview"]
    content_type: str | None = Field(default=None, max_length=80)
    bytes: int | None = Field(default=None, ge=0)


class EvidenceUploadRequest(Strict):
    client_incident_id: str = Field(min_length=1, max_length=80)
    files: list[EvidenceFileRequest] = Field(min_length=1, max_length=3)


class AssignmentUpdate(Strict):
    status: Literal["processing", "completed", "failed", "stopped"]
    progress: float | None = Field(default=None, ge=0, le=1)
    runtime_s: float | None = Field(default=None, ge=0)
    frames_total: int | None = Field(default=None, ge=0)
    frames_processed: int | None = Field(default=None, ge=0)
    detections: int | None = Field(default=None, ge=0)
    incidents: int | None = Field(default=None, ge=0)
    device: str | None = Field(default=None, max_length=40)
    error: str | None = Field(default=None, max_length=500)
    processed_key: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=500)


class UploadRequest(Strict):
    filename: str = Field(min_length=1, max_length=180)
    content_type: str = Field(default="video/mp4", max_length=80)
    size_bytes: int = Field(gt=0, le=4 * 1024**3)


class QueueJobRequest(Strict):
    media_id: str = Field(min_length=1, max_length=40)


class ConnectorCreate(Strict):
    name: str = Field(min_length=1, max_length=80)
    notes: str | None = Field(default=None, max_length=500)


class ProcessedUploadRequest(Strict):
    job_id: str = Field(min_length=1, max_length=40)


class SessionRequest(Strict):
    token: str = Field(min_length=1, max_length=300)


class SettingsPatch(Strict):
    model_config = ConfigDict(extra="allow")

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)
