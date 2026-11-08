"""Model catalogue, local weight management and the custom-model interface.

Three distinct model families exist in CivicWatch:

``vehicle_pedestrian``
    Built-in COCO detectors (YOLOv8n/s, YOLO11n). They detect cars,
    motorcycles, buses, trucks, bicycles and pedestrians — nothing else.

``pothole`` / ``garbage``
    Dedicated categories. They only work once an operator installs a weights
    file trained for that purpose. CivicWatch never fakes these results with a
    generic detector; until a file is installed the API reports ``installed:
    false`` and the UI says the category is unavailable.

Any Ultralytics-format detection ``.pt`` can be installed through
Settings → AI models, which is what makes this interface modular: a new
municipal model needs no code change.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import APP_VERSION, get_paths
from ..db import execute, query, query_one, utc_now

log = logging.getLogger("civicwatch.models")

RELEASE = "https://github.com/ultralytics/assets/releases/download/v8.3.0"
ULTRALYTICS_LICENSE = "AGPL-3.0"

BUILTIN_MODELS: list[dict[str, Any]] = [
    {
        "id": "yolov8n",
        "name": "YOLOv8 nano",
        "filename": "yolov8n.pt",
        "url": f"{RELEASE}/yolov8n.pt",
        "size_mb": 6.2,
        "license": ULTRALYTICS_LICENSE,
        "family": "vehicle_pedestrian",
        "description": "Default. Fastest COCO detector, well suited to CPU-only machines.",
        "recommended": True,
    },
    {
        "id": "yolo11n",
        "name": "YOLO11 nano",
        "filename": "yolo11n.pt",
        "url": f"{RELEASE}/yolo11n.pt",
        "size_mb": 5.4,
        "license": ULTRALYTICS_LICENSE,
        "family": "vehicle_pedestrian",
        "description": "Newer architecture, similar speed to YOLOv8n with slightly better recall.",
        "recommended": False,
    },
    {
        "id": "yolov8s",
        "name": "YOLOv8 small",
        "filename": "yolov8s.pt",
        "url": f"{RELEASE}/yolov8s.pt",
        "size_mb": 21.5,
        "license": ULTRALYTICS_LICENSE,
        "family": "vehicle_pedestrian",
        "description": "More accurate but roughly three times slower; use on MPS hardware.",
        "recommended": False,
    },
]

CUSTOM_CATEGORIES: dict[str, dict[str, str]] = {
    "pothole": {
        "label": "Pothole detection",
        "description": (
            "Requires a dedicated road-damage model. COCO detectors cannot "
            "recognise potholes, so this category stays disabled until you install one."
        ),
    },
    "garbage": {
        "label": "Garbage / waste detection",
        "description": (
            "Requires a dedicated waste-detection model. Until one is installed, "
            "garbage incidents cannot be produced."
        ),
    },
}

# Model cache: loading weights costs seconds, so detectors live for the process.
_detector_cache: dict[str, Any] = {}
_cache_lock = threading.Lock()


@dataclass
class DownloadState:
    model_id: str
    status: str = "idle"  # idle | downloading | verifying | ready | error
    progress: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: int = 0
    error: str | None = None


_downloads: dict[str, DownloadState] = {}


def model_dir() -> Path:
    path = get_paths().models
    path.mkdir(parents=True, exist_ok=True)
    return path


def custom_dir() -> Path:
    path = model_dir() / "custom"
    path.mkdir(parents=True, exist_ok=True)
    return path


def builtin_entry(model_id: str) -> dict[str, Any] | None:
    for entry in BUILTIN_MODELS:
        if entry["id"] == model_id:
            return entry
    return None


def weights_path(model_id: str) -> Path:
    entry = builtin_entry(model_id)
    if not entry:
        raise ValueError(f"Unknown built-in model: {model_id}")
    return model_dir() / entry["filename"]


def validate_weights(path: Path) -> dict[str, Any]:
    """Load the checkpoint locally and confirm it is a usable detection model."""
    if not path.exists():
        raise ValueError("Weights file is missing")
    if path.stat().st_size < 100_000:
        raise ValueError("Weights file is too small to be a valid model")
    try:
        import torch
    except Exception as exc:  # pragma: no cover
        raise ValueError(f"PyTorch unavailable: {exc}") from exc
    try:
        payload = torch.load(str(path), map_location="cpu", weights_only=False)
    except Exception as exc:
        raise ValueError(f"Not a readable PyTorch checkpoint: {exc}") from exc
    if not isinstance(payload, dict) or "model" not in payload:
        raise ValueError("Checkpoint does not contain a YOLO model")
    names = payload.get("names") or getattr(payload.get("model"), "names", None) or {}
    if isinstance(names, (list, tuple)):
        names = {i: str(v) for i, v in enumerate(names)}
    if not isinstance(names, dict) or not names:
        names = getattr(payload.get("ema"), "names", None) or {}
    if not isinstance(names, dict) or not names:
        raise ValueError("Checkpoint has no class names")
    return {
        "classes": {str(k): str(v) for k, v in names.items()},
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
    }


def _describe(entry: dict[str, Any]) -> dict[str, Any]:
    path = model_dir() / entry["filename"]
    installed = path.exists()
    state = _downloads.get(entry["id"])
    return {
        **entry,
        "installed": installed,
        "path": str(path) if installed else None,
        "installed_bytes": path.stat().st_size if installed else 0,
        "download": (state.__dict__ if state else DownloadState(entry["id"]).__dict__),
    }


def list_builtin_models() -> list[dict[str, Any]]:
    return [_describe(entry) for entry in BUILTIN_MODELS]


def custom_models() -> list[dict[str, Any]]:
    rows = query("SELECT * FROM custom_models ORDER BY installed_at DESC")
    result = []
    for row in rows:
        item = dict(row)
        path = custom_dir() / item["filename"]
        item["installed"] = path.exists()
        item["metadata"] = json.loads(item["metadata"]) if item.get("metadata") else {}
        item["enabled"] = bool(item["metadata"].get("enabled", True)) if item.get("metadata") else True
        result.append(item)
    return result


def category_status() -> list[dict[str, Any]]:
    """Report which specialised categories are genuinely available."""
    installed = {m["category"]: m for m in custom_models() if m["installed"]}
    out = []
    for category, meta in CUSTOM_CATEGORIES.items():
        model = installed.get(category)
        out.append(
            {
                "category": category,
                "label": meta["label"],
                "description": meta["description"],
                "installed": model is not None,
                "model": (
                    {
                        "id": model["id"],
                        "name": model["name"],
                        "license": model["license"],
                        "classes": model["metadata"].get("classes", []),
                    }
                    if model
                    else None
                ),
            }
        )
    return out


def download_model(model_id: str, force: bool = False) -> DownloadState:
    """Fetch a model. ``force`` re-downloads over an installed file.

    The download lands in ``*.part`` and only replaces the live weights after
    ``validate_weights`` accepts it, so an interrupted update never breaks a
    working installation.
    """
    entry = builtin_entry(model_id)
    if not entry:
        raise ValueError(f"Unknown built-in model: {model_id}")
    target = weights_path(model_id)
    if target.exists() and not force:
        state = _downloads.get(model_id) or DownloadState(model_id)
        state.status = "ready"
        state.progress = 1.0
        state.downloaded_bytes = target.stat().st_size
        state.total_bytes = target.stat().st_size
        _downloads[model_id] = state
        return state

    state = DownloadState(model_id=model_id, status="downloading")
    _downloads[model_id] = state

    def _work() -> None:
        tmp = target.with_suffix(target.suffix + ".part")
        try:
            request = urllib.request.Request(entry["url"], headers={"User-Agent": f"CivicWatch/{APP_VERSION}"})
            with urllib.request.urlopen(request, timeout=60) as response:
                total = int(response.headers.get("Content-Length") or 0)
                state.total_bytes = total
                with open(tmp, "wb") as handle:
                    while True:
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        state.downloaded_bytes += len(chunk)
                        if total:
                            state.progress = min(0.99, state.downloaded_bytes / total)
            state.status = "verifying"
            info = validate_weights(tmp)
            tmp.replace(target)
            state.progress = 1.0
            state.status = "ready"
            log.info("Model %s ready (%s classes)", model_id, len(info["classes"]))
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            state.status = "error"
            state.error = str(exc)
            log.warning("Model download failed for %s: %s", model_id, exc)
            tmp.unlink(missing_ok=True)

    threading.Thread(target=_work, daemon=True, name=f"model-download-{model_id}").start()
    return state


def download_states() -> dict[str, dict[str, Any]]:
    return {mid: state.__dict__ for mid, state in _downloads.items()}


def get_detector(
    model_id: str,
    device: str,
    confidence: float,
    iou: float,
    imgsz: int,
):
    """Return a cached detector, loading weights on first use."""
    from .yolo import ModelNotAvailable, YOLODetector

    entry = builtin_entry(model_id)
    if not entry:
        raise ModelNotAvailable(f"Unknown model: {model_id}")
    path = weights_path(model_id)
    if not path.exists():
        raise ModelNotAvailable(
            f"{entry['name']} is not installed yet. Open Settings → AI models to download it."
        )
    key = f"{model_id}:{device}"
    with _cache_lock:
        detector = _detector_cache.get(key)
        if detector is None:
            detector = YOLODetector(
                weights=path,
                model_id=model_id,
                device=device,
                confidence=confidence,
                iou=iou,
                imgsz=imgsz,
            )
            _detector_cache[key] = detector
        else:
            detector.update_runtime(confidence, iou, imgsz, device)
    return detector


def clear_detector_cache() -> None:
    with _cache_lock:
        _detector_cache.clear()


def install_custom_model(
    uploaded_path: Path,
    name: str,
    category: str,
    license_text: str = "",
    source: str = "",
    enabled: bool = True,
) -> dict[str, Any]:
    if category not in CUSTOM_CATEGORIES:
        raise ValueError(f"Unsupported category: {category}")
    info = validate_weights(uploaded_path)
    model_id = f"{category}-{hashlib.sha1((name + utc_now()).encode()).hexdigest()[:8]}"
    filename = f"{model_id}.pt"
    destination = custom_dir() / filename
    shutil.copy2(uploaded_path, destination)
    metadata = {
        "classes": list(info["classes"].values()),
        "sha256": info["sha256"],
        "bytes": info["bytes"],
        "enabled": enabled,
    }
    execute(
        "INSERT INTO custom_models (id, name, category, filename, license, source, installed_at, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (model_id, name, category, filename, license_text, source, utc_now(), json.dumps(metadata)),
    )
    log.info("Installed custom %s model %s", category, model_id)
    return {
        "id": model_id,
        "name": name,
        "category": category,
        "filename": filename,
        "license": license_text,
        "source": source,
        "installed_at": utc_now(),
        "metadata": metadata,
        "installed": True,
        "enabled": enabled,
    }


def set_custom_model_enabled(model_id: str, enabled: bool) -> dict[str, Any]:
    row = query_one("SELECT * FROM custom_models WHERE id = ?", (model_id,))
    if not row:
        raise KeyError("Model not found")
    metadata = json.loads(row["metadata"]) if row["metadata"] else {}
    metadata["enabled"] = bool(enabled)
    execute(
        "UPDATE custom_models SET metadata = ? WHERE id = ?", (json.dumps(metadata), model_id)
    )
    return next((m for m in custom_models() if m["id"] == model_id), {})


def delete_custom_model(model_id: str) -> None:
    row = query_one("SELECT * FROM custom_models WHERE id = ?", (model_id,))
    if not row:
        raise KeyError("Model not found")
    (custom_dir() / row["filename"]).unlink(missing_ok=True)
    execute("DELETE FROM custom_models WHERE id = ?", (model_id,))


class CustomDetector:
    """Adapter that runs an operator-supplied model for pothole/waste checks."""

    def __init__(self, record: dict[str, Any], device: str, confidence: float, imgsz: int):
        from .yolo import YOLODetector

        path = custom_dir() / record["filename"]
        self.category = record["category"]
        self.model_id = record["id"]
        self.detector = YOLODetector(
            weights=path,
            model_id=record["id"],
            device=device,
            confidence=confidence,
            iou=0.5,
            imgsz=imgsz,
            allowed_classes=None,
        )

    def predict(self, frame):
        return self.detector.predict(frame)


def active_custom_detectors(device: str, confidence: float, imgsz: int) -> list[CustomDetector]:
    detectors = []
    for record in custom_models():
        if not record["installed"] or not record.get("enabled", True):
            continue
        try:
            detectors.append(CustomDetector(record, device, confidence, imgsz))
        except Exception as exc:  # noqa: BLE001 - a broken custom model must not kill a job
            log.warning("Custom model %s could not be loaded: %s", record["id"], exc)
    return detectors
