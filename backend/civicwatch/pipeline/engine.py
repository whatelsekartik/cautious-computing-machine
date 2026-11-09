"""Frame processor: detection → tracking → zone rules → evidence → incident."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from ..detect.base import Detection, PERSON_CLASSES
from ..geometry import box_iou
from ..tracking import BYTETracker
from ..tracking.byte_track import Track
from .annotate import blur_box, draw_detection, draw_hud, draw_track, draw_zone
from .evidence import EvidenceRecorder
from .rules import RuleEngine, Violation, ZoneSpec

log = logging.getLogger("civicwatch.engine")


@dataclass
class SourceInfo:
    kind: str  # video | webcam | rtsp | http
    ref: str  # media id or camera id
    camera: str
    location: str | None = None


@dataclass
class ProcessedFrame:
    annotated: np.ndarray
    detections: int
    tracks: list[Track]
    violations: list[Violation]
    stationary_ids: set[int] = field(default_factory=set)


class FrameProcessor:
    """One instance per video job or live camera session."""

    def __init__(
        self,
        detector: Any,
        source: SourceInfo,
        settings: dict[str, Any],
        zones: Sequence[ZoneSpec],
        custom_detectors: Sequence[Any] = (),
        on_incident: Callable[[Any], None] | None = None,
        evidence_dir: Path | None = None,
        clip_fps: float = 4.0,
    ) -> None:
        self.detector = detector
        self.source = source
        self.settings = settings
        self.zones = list(zones)
        self.custom_detectors = list(custom_detectors)
        self.on_incident = on_incident

        self.tracker = BYTETracker(
            track_thresh=settings.get("tracker_track_thresh", 0.4),
            match_thresh=settings.get("tracker_match_thresh", 0.8),
            track_buffer=settings.get("tracker_buffer", 30),
            class_aware=settings.get("tracker_class_aware", True),
        )
        self.rules = RuleEngine(
            zones=self.zones,
            settings=settings,
            cooldown_seconds=float(settings.get("incident_cooldown_minutes", 10)) * 60.0,
        )
        self.evidence = EvidenceRecorder(
            base_dir=evidence_dir or Path("."),
            clip_fps=clip_fps,
            pre_seconds=float(settings.get("clip_pre_seconds", 3.0)),
            post_seconds=float(settings.get("clip_post_seconds", 3.0)),
            clip_width=int(settings.get("clip_width", 640)),
            store_snapshots=bool(settings.get("store_snapshots", True)),
            store_clips=bool(settings.get("store_clips", True)),
        ) if evidence_dir else None

        self.model_label = str(getattr(detector, "model_id", "model"))
        self.frame_index = 0
        self.total_detections = 0
        self.total_incidents = 0
        self._last_emit: dict[str, float] = {}
        self.blur_persons = bool(settings.get("blur_persons", False))

    # -- main entry ------------------------------------------------------
    def process(
        self,
        frame: np.ndarray,
        t_seconds: float,
        wall_clock: datetime | None = None,
        incident_id_factory: Callable[[], str] | None = None,
    ) -> ProcessedFrame:
        self.frame_index += 1
        height, width = frame.shape[:2]
        detections = self.detector.predict(frame)
        tracks = self.tracker.update(detections)

        custom_pairs: list[tuple[str, Detection]] = []
        for detector in self.custom_detectors:
            for det in detector.predict(frame):
                custom_pairs.append((detector.category, det))

        violations = self.rules.evaluate(tracks, t_seconds, width, height)
        if custom_pairs:
            violations.extend(
                self.rules.evaluate_custom(custom_pairs, t_seconds, width, height)
            )
        stationary = self.rules.stationary_tracks()
        self.total_detections += len(detections) + len(custom_pairs)

        annotated = self._annotate(frame, tracks, stationary, custom_pairs, t_seconds)

        if self.evidence is not None:
            self.evidence.push(annotated, t_seconds)
            for violation in violations:
                self._record_incident(violation, annotated, t_seconds, wall_clock, incident_id_factory)

        return ProcessedFrame(
            annotated=annotated,
            detections=len(detections) + len(custom_pairs),
            tracks=tracks,
            violations=violations,
            stationary_ids=stationary,
        )

    # -- incident plumbing ----------------------------------------------
    def _record_incident(
        self,
        violation: Violation,
        frame: np.ndarray,
        t_seconds: float,
        wall_clock: datetime | None,
        incident_id_factory: Callable[[], str] | None,
    ) -> None:
        if self.on_incident is None or incident_id_factory is None or self.evidence is None:
            return
        from ..services.incidents import IncidentService

        when = (wall_clock or datetime.now(timezone.utc)).astimezone(timezone.utc)
        incident_id = incident_id_factory()
        snapshot = self.evidence.snapshot(frame, incident_id)
        clip = self.evidence.start_clip(incident_id)
        try:
            incident = IncidentService.create_from_violation(
                violation,
                incident_id=incident_id,
                camera=self.source.camera,
                location=self.source.location,
                captured_at=when.isoformat(timespec="seconds"),
                source_type="live" if self.source.kind != "video" else "video",
                source_ref=self.source.ref,
                snapshot_path=snapshot,
                clip_path=clip,
                extra={
                    "video_offset_seconds": round(t_seconds, 2),
                    "source_kind": self.source.kind,
                    "frame_index": self.frame_index,
                    "device": getattr(self.detector, "device", None) and str(self.detector.device),
                    "model_id": getattr(self.detector, "model_id", None),
                },
            )
        except Exception:  # noqa: BLE001 - never kill a running job for one incident
            log.exception("Could not store incident for %s", violation.category)
            return
        self.total_incidents += 1
        try:
            self.on_incident(incident)
        except Exception:  # pragma: no cover - subscriber bug
            log.exception("Incident listener failed")

    # -- drawing ---------------------------------------------------------
    def _annotate(
        self,
        frame: np.ndarray,
        tracks: Sequence[Track],
        stationary: set[int],
        custom_pairs: Sequence[tuple[str, Detection]],
        t_seconds: float,
    ) -> np.ndarray:
        annotated = frame.copy()
        for zone in self.zones:
            draw_zone(annotated, zone.polygon, zone.kind, zone.name)

        if self.blur_persons:
            for track in tracks:
                if track.class_name in PERSON_CLASSES:
                    blur_box(annotated, track.box)
        for track in tracks:
            if track.time_since_update > 0:
                continue
            if self.blur_persons and track.class_name in PERSON_CLASSES:
                pass  # already blurred; do not reveal a crisp label box
            draw_track(
                annotated,
                track.box,
                track.class_name,
                track.track_id,
                track.score,
                stationary=track.track_id in stationary,
            )
        for category, det in custom_pairs:
            draw_detection(
                annotated, det.xyxy, f"{det.class_name.upper()} {det.score:.2f}", category
            )

        minutes, seconds = divmod(int(t_seconds), 60)
        draw_hud(
            annotated,
            [
                f"{self.source.camera}",
                f"t+{minutes:02d}:{seconds:02d}  tracks {len(tracks)}  zones {len(self.zones)}",
                f"{getattr(self.detector, 'device', 'cpu')} · {self.model_label}",
            ],
        )
        return annotated

    def close(self) -> None:
        if self.evidence is not None:
            self.evidence.close()

    def stats(self) -> dict[str, Any]:
        return {
            "frames": self.frame_index,
            "detections": self.total_detections,
            "incidents": self.total_incidents,
            "tracks": len(self.tracker.tracked_stracks),
        }
