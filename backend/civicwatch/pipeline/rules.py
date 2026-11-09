"""Zone rule engine: turns tracked objects into *potential* violation signals.

Nothing in this module decides that an offence happened. It emits candidate
incidents with the evidence needed for a human to verify or dismiss them.
"""

from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from ..detect.base import CATEGORY_LABELS, Detection, VEHICLE_CLASSES
from ..geometry import (
    ZoneMask,
    box_anchor,
    box_center,
    box_scale,
    point_in_polygon,
    validate_polygon,
)

KIND_DEFAULTS: dict[str, dict[str, Any]] = {
    "no_parking": {
        "categories": ("illegal_parking", "double_parking"),
        "classes": ["car", "motorcycle", "bus", "truck"],
        "dwell_seconds": 20.0,
        "severity": "medium",
        "double_parking": True,
    },
    "footpath": {
        "categories": ("footpath_obstruction",),
        "classes": ["car", "motorcycle", "bus", "truck", "bicycle"],
        "dwell_seconds": 5.0,
        "severity": "medium",
        "double_parking": False,
    },
    "road": {
        "categories": ("stopped_in_carriageway",),
        "classes": [],
        "dwell_seconds": 10.0,
        "severity": "low",
        "double_parking": False,
        "no_stopping": False,
    },
    "restricted": {
        "categories": ("restricted_entry",),
        "classes": ["car", "motorcycle", "bus", "truck", "person", "bicycle"],
        "dwell_seconds": 2.0,
        "severity": "medium",
        "double_parking": False,
    },
}

ZONE_KINDS = tuple(KIND_DEFAULTS.keys())


@dataclass
class ZoneSpec:
    id: str
    name: str
    kind: str
    polygon: list[list[float]]
    rules: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    _masks: dict[tuple[int, int], ZoneMask] = field(default_factory=dict, repr=False)

    @classmethod
    def from_row(cls, row: Any) -> "ZoneSpec":
        record = dict(row)
        polygon = record["polygon"]
        if isinstance(polygon, str):
            polygon = json.loads(polygon)
        raw_rules = record.get("rules")
        rules = raw_rules if isinstance(raw_rules, dict) else json.loads(raw_rules or "{}")
        return cls(
            id=row["id"],
            name=row["name"],
            kind=row["kind"],
            polygon=validate_polygon(polygon),
            rules=rules,
            enabled=bool(row["enabled"]),
        )

    def option(self, key: str, fallback: Any = None) -> Any:
        defaults = KIND_DEFAULTS.get(self.kind, {})
        value = self.rules.get(key) if self.rules else None
        if value is None:
            return defaults.get(key, fallback)
        return value

    @property
    def classes(self) -> set[str]:
        value = self.rules.get("classes") if self.rules else None
        if not value:
            return set(self.option("classes", []))
        return set(value)

    @property
    def dwell_seconds(self) -> float:
        value = self.rules.get("dwell_seconds") if self.rules else None
        return float(value if value is not None else self.option("dwell_seconds", 10.0))

    @property
    def overlap_threshold(self) -> float:
        value = self.rules.get("overlap_threshold") if self.rules else None
        return float(value if value is not None else 0.2)

    @property
    def neighbour_ratio(self) -> float:
        value = self.rules.get("neighbour_ratio") if self.rules else None
        return float(value if value is not None else 0.11)

    def mask_for(self, frame_w: int, frame_h: int) -> ZoneMask:
        key = (max(1, frame_w // 4), max(1, frame_h // 4))
        mask = self._masks.get(key)
        if mask is None:
            mask = ZoneMask.build(self.polygon, width=key[0], height=key[1])
            self._masks[key] = mask
        return mask

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "classes": sorted(self.classes),
            "dwell_seconds": self.dwell_seconds,
            "enabled": self.enabled,
        }


@dataclass
class Violation:
    category: str
    zone_id: str | None
    zone_name: str | None
    track_id: int | None
    class_name: str
    confidence: float
    dwell_seconds: float
    severity: str
    box: tuple[float, float, float, float] | None = None
    model_id: str | None = None
    key: str = ""

    @property
    def label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "label": self.label,
            "zone_id": self.zone_id,
            "zone_name": self.zone_name,
            "track_id": self.track_id,
            "class_name": self.class_name,
            "confidence": round(self.confidence, 4),
            "dwell_seconds": round(self.dwell_seconds, 2),
            "severity": self.severity,
            "box": [round(v, 1) for v in self.box] if self.box else None,
        }


@dataclass
class _DwellState:
    accum: float = 0.0
    last_hit: float = -1e9
    started: float | None = None
    fired_at: float | None = None
    max_conf: float = 0.0
    last_t: float | None = None


class RuleEngine:
    """Stateful per-source evaluator (one instance per video job or camera)."""

    def __init__(
        self,
        zones: Sequence[ZoneSpec],
        settings: dict[str, Any],
        cooldown_seconds: float | None = None,
        stationary_window: float = 8.0,
    ) -> None:
        self.zones = [z for z in zones if z.enabled]
        self.cooldown = (
            float(cooldown_seconds)
            if cooldown_seconds is not None
            else float(settings.get("incident_cooldown_minutes", 10)) * 60.0
        )
        self.stationary_window = stationary_window
        self._dwell: dict[tuple[int, str], _DwellState] = {}
        self._history: dict[int, deque[tuple[float, float, float, float]]] = {}
        self._stationary: dict[int, bool] = {}
        self._custom_fired: dict[str, float] = {}
        self._last_t: float | None = None
        self._gap_tolerance = 2.5

    # -- stationarity ----------------------------------------------------
    def _update_motion(self, track: Any, t_seconds: float) -> bool:
        box = track.box
        cx, cy = box_center(box)
        diag = max(1.0, box_scale(box))
        history = self._history.setdefault(track.track_id, deque(maxlen=240))
        history.append((t_seconds, cx, cy, diag))
        while history and t_seconds - history[0][0] > self.stationary_window:
            history.popleft()
        if len(history) < 2:
            self._stationary[track.track_id] = False
            return False
        span = t_seconds - history[0][0]
        if span < 1.0:
            self._stationary[track.track_id] = False
            return False
        latest = history[-1]
        tolerance = max(6.0, 0.18 * latest[3])
        spread = max(
            math.hypot(px - latest[1], py - latest[2]) for _, px, py, _ in history
        )
        stationary = spread <= tolerance
        self._stationary[track.track_id] = stationary
        return stationary

    def stationary_tracks(self) -> set[int]:
        return {tid for tid, flag in self._stationary.items() if flag}

    # -- main evaluation -------------------------------------------------
    def evaluate(
        self,
        tracks: Sequence[Any],
        t_seconds: float,
        frame_w: int,
        frame_h: int,
    ) -> list[Violation]:
        if self._last_t is not None:
            dt = max(0.0, min(t_seconds - self._last_t, 2.0))
        else:
            dt = 0.0
        self._last_t = t_seconds

        active: dict[int, Any] = {}
        stationary: dict[int, bool] = {}
        for track in tracks:
            if track.time_since_update > 0:
                continue
            active[track.track_id] = track
            stationary[track.track_id] = self._update_motion(track, t_seconds)

        violations: list[Violation] = []
        for zone in self.zones:
            mask = zone.mask_for(frame_w, frame_h)
            zone_classes = zone.classes
            if not zone_classes:
                continue
            for track_id, track in active.items():
                if track.class_name not in zone_classes:
                    continue
                coverage = mask.coverage(track.box, frame_w, frame_h)
                anchor = box_anchor(track.box)
                anchor_norm = (anchor[0] / frame_w, anchor[1] / frame_h)
                inside = coverage >= zone.overlap_threshold or point_in_polygon(anchor_norm, zone.polygon)
                key = (track_id, zone.id)
                state = self._dwell.setdefault(key, _DwellState())
                state.max_conf = max(state.max_conf, track.score)

                if inside and stationary[track_id]:
                    if state.accum == 0.0 or state.started is None:
                        state.started = t_seconds
                    state.accum += dt
                    state.last_hit = t_seconds
                elif t_seconds - state.last_hit > self._gap_tolerance:
                    state.accum = 0.0
                    state.started = None

                dwell = state.accum
                if dwell + 1e-6 < zone.dwell_seconds:
                    continue
                category = self._category_for(zone, track, stationary, active, frame_w)
                if not category:
                    continue
                if state.fired_at is not None and t_seconds - state.fired_at < self.cooldown:
                    continue
                state.fired_at = t_seconds
                violations.append(
                    Violation(
                        category=category,
                        zone_id=zone.id,
                        zone_name=zone.name,
                        track_id=track_id,
                        class_name=track.class_name,
                        confidence=state.max_conf,
                        dwell_seconds=dwell,
                        severity=self._severity(zone, track.class_name, dwell),
                        box=tuple(track.box),
                        key=f"{zone.id}:{track_id}:{category}",
                    )
                )

        self._prune(t_seconds, active)
        return violations

    def _category_for(
        self,
        zone: ZoneSpec,
        track: Any,
        stationary: dict[int, bool],
        active: dict[int, Any],
        frame_w: int,
    ) -> str | None:
        if zone.kind == "no_parking":
            if zone.option("double_parking", True) and self._has_neighbour(zone, track, stationary, active, frame_w):
                return "double_parking"
            return "illegal_parking"
        if zone.kind == "footpath":
            return "footpath_obstruction"
        if zone.kind == "restricted":
            return "restricted_entry"
        if zone.kind == "road":
            return "stopped_in_carriageway" if zone.option("no_stopping", False) else None
        return None

    def _has_neighbour(
        self,
        zone: ZoneSpec,
        track: Any,
        stationary: dict[int, bool],
        active: dict[int, Any],
        frame_w: int,
    ) -> bool:
        """Two stationary vehicles side by side inside the same zone."""
        if track.class_name not in VEHICLE_CLASSES:
            return False
        cx, cy = box_center(track.box)
        limit = zone.neighbour_ratio * frame_w
        for other_id, other in active.items():
            if other_id == track.track_id or not stationary.get(other_id):
                continue
            if other.class_name not in VEHICLE_CLASSES:
                continue
            ox, oy = box_center(other.box)
            if math.hypot(ox - cx, oy - cy) <= max(limit, 0.6 * box_scale(track.box)):
                return True
        return False

    def _severity(self, zone: ZoneSpec, class_name: str, dwell: float) -> str:
        ratio = dwell / max(1e-6, zone.dwell_seconds)
        base = str(zone.option("severity", "medium"))
        if class_name in {"bus", "truck"} and base == "low":
            base = "medium"
        if ratio >= 3.0:
            return "high"
        if ratio >= 1.5:
            return "high" if base == "medium" else "medium"
        return base

    def _prune(self, t_seconds: float, active: dict[int, Any]) -> None:
        if len(self._dwell) < 500:
            return
        stale = [k for k, s in self._dwell.items() if t_seconds - s.last_hit > 120]
        for key in stale:
            self._dwell.pop(key, None)
        for track_id in list(self._history):
            if track_id not in active and not self._history[track_id]:
                self._history.pop(track_id, None)

    # -- specialised models ----------------------------------------------
    def evaluate_custom(
        self,
        detections: Iterable[tuple[str, Detection]],
        t_seconds: float,
        frame_w: int,
        frame_h: int,
        cooldown_seconds: float = 300.0,
    ) -> list[Violation]:
        """Convert pothole/waste detections into incidents with spatial de-duplication."""
        out: list[Violation] = []
        for category, det in detections:
            cx, cy = box_center(det.xyxy)
            bucket = f"{category}:{int(cx // 80)}:{int(cy // 80)}"
            last = self._custom_fired.get(bucket)
            if last is not None and t_seconds - last < cooldown_seconds:
                continue
            self._custom_fired[bucket] = t_seconds
            out.append(
                Violation(
                    category=category,
                    zone_id=None,
                    zone_name=None,
                    track_id=None,
                    class_name=det.class_name,
                    confidence=det.score,
                    dwell_seconds=0.0,
                    severity="medium",
                    box=tuple(det.xyxy),
                    model_id=category,
                    key=bucket,
                )
            )
        return out
