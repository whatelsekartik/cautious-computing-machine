"""Typed settings persisted in SQLite with sane local-first defaults."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from .db import execute, query, utc_now

DEFAULT_SETTINGS: dict[str, Any] = {
    # AI / processing
    "device_preference": "auto",  # auto | mps | cpu
    "model_id": "yolov8n",
    "confidence": 0.35,
    "iou_threshold": 0.5,
    "imgsz": 640,
    "sample_every": 5,
    "max_processing_width": 1280,
    "save_annotated_video": True,
    # tracking (ByteTrack)
    "tracker_track_thresh": 0.4,
    "tracker_match_thresh": 0.8,
    "tracker_buffer": 30,
    "tracker_class_aware": True,
    # evidence
    "store_snapshots": True,
    "store_clips": True,
    "clip_pre_seconds": 3.0,
    "clip_post_seconds": 3.0,
    "clip_width": 640,
    "blur_persons": False,
    # data governance
    "evidence_retention_days": 30,
    "media_retention_days": 14,
    "incident_cooldown_minutes": 10,
    "operator_name": "",
    "theme": "system",
    # live
    "live_sample_every": 5,
}

_SETTING_KEYS = set(DEFAULT_SETTINGS)
_cache: dict[str, Any] | None = None


def _coerce(key: str, value: Any) -> Any:
    default = DEFAULT_SETTINGS[key]
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if isinstance(default, int) and not isinstance(default, bool):
        return int(value)
    if isinstance(default, float):
        return float(value)
    return str(value)


def _validate(key: str, value: Any) -> None:
    if key == "confidence" and not 0.05 <= value <= 0.95:
        raise ValueError("Detection confidence must be between 0.05 and 0.95")
    if key == "iou_threshold" and not 0.1 <= value <= 0.95:
        raise ValueError("IoU threshold must be between 0.1 and 0.95")
    if key in {"parse"}:
        raise ValueError("Unknown setting")
    if key == "imgsz" and value not in {320, 416, 512, 640, 960, 1280}:
        raise ValueError("Image size must be one of 320, 416, 512, 640, 960, 1280")
    if key == "sample_every" and not 1 <= value <= 60:
        raise ValueError("Frame sampling must be between 1 and 60")
    if key == "live_sample_every" and not 1 <= value <= 60:
        raise ValueError("Live frame sampling must be between 1 and 60")
    if key == "max_processing_width" and not 320 <= value <= 3840:
        raise ValueError("Processing width must be between 320 and 3840")
    if key == "device_preference" and value not in {"auto", "mps", "cpu"}:
        raise ValueError("Device must be auto, mps or cpu")
    if key.endswith("_retention_days") and not 1 <= value <= 3650:
        raise ValueError("Retention must be between 1 and 3650 days")
    if key == "incident_cooldown_minutes" and not 0 <= value <= 1440:
        raise ValueError("Cooldown must be between 0 and 1440 minutes")
    if key == "tracker_buffer" and not 1 <= value <= 300:
        raise ValueError("Tracker buffer must be between 1 and 300 frames")
    if key in {"clip_pre_seconds", "clip_post_seconds"} and not 0 <= value <= 30:
        raise ValueError("Clip buffer must be between 0 and 30 seconds")


def all_settings(refresh: bool = False) -> dict[str, Any]:
    global _cache
    if _cache is not None and not refresh:
        return deepcopy(_cache)
    values = deepcopy(DEFAULT_SETTINGS)
    try:
        for row in query("SELECT key, value FROM settings"):
            key = row["key"]
            if key not in DEFAULT_SETTINGS:
                continue
            try:
                values[key] = _coerce(key, json.loads(row["value"]))
            except (json.JSONDecodeError, ValueError):
                continue
    except Exception:  # pragma: no cover - DB not initialised yet
        pass
    _cache = values
    return deepcopy(values)


def get_setting(key: str) -> Any:
    if key not in DEFAULT_SETTINGS:
        raise KeyError(key)
    return all_settings()[key]


def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
    global _cache
    unknown = set(patch) - _SETTING_KEYS
    if unknown:
        raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
    prepared: dict[str, Any] = {}
    for key, raw in patch.items():
        value = _coerce(key, raw)
        _validate(key, value)
        prepared[key] = value
    for key, value in prepared.items():
        execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value), utc_now()),
        )
    _cache = None
    return all_settings(refresh=True)


def reset_setting(key: str) -> dict[str, Any]:
    global _cache
    if key not in DEFAULT_SETTINGS:
        raise KeyError(key)
    execute("DELETE FROM settings WHERE key = ?", (key,))
    _cache = None
    return all_settings(refresh=True)


def tracker_config(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    s = settings or all_settings()
    return {
        "track_thresh": s["tracker_track_thresh"],
        "match_thresh": s["tracker_match_thresh"],
        "track_buffer": s["tracker_buffer"],
        "class_aware": s["tracker_class_aware"],
    }
