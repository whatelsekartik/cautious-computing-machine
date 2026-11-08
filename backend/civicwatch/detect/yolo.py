"""Ultralytics YOLO detector wrapper (fully local inference)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

from .base import Detection, PERSON_CLASSES, VEHICLE_CLASSES

log = logging.getLogger("civicwatch.detector")

DEFAULT_CLASSES = frozenset(VEHICLE_CLASSES | PERSON_CLASSES)


class ModelNotAvailable(RuntimeError):
    """Raised when weights are missing or the local runtime cannot load them."""


class YOLODetector:
    """Thread-safe wrapper around one loaded Ultralytics model."""

    def __init__(
        self,
        weights: Path,
        model_id: str,
        device: str = "cpu",
        confidence: float = 0.35,
        iou: float = 0.5,
        imgsz: int = 640,
        allowed_classes: frozenset[str] | None = DEFAULT_CLASSES,
    ) -> None:
        self.weights = Path(weights)
        self.model_id = model_id
        self.device = device
        self.confidence = confidence
        self.iou = iou
        self.imgsz = imgsz
        self.allowed_classes = allowed_classes
        self._lock = threading.Lock()
        self._model = None
        self.names: dict[int, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.weights.exists():
            raise ModelNotAvailable(
                f"Model file not found: {self.weights.name}. Download it from Settings → AI models."
            )
        try:
            from ultralytics import YOLO
        except Exception as exc:  # pragma: no cover - dependency missing
            raise ModelNotAvailable(f"Ultralytics is not installed: {exc}") from exc
        try:
            self._model = YOLO(str(self.weights), task="detect")
            raw_names = getattr(self._model, "names", {}) or {}
            self.names = {int(k): str(v) for k, v in raw_names.items()}
            # Warm-up: the first MPS call compiles kernels, which otherwise
            # stalls the first real frame of every job.
            warm = np.zeros((self.imgsz, self.imgsz, 3), dtype=np.uint8)
            self._model.predict(warm, imgsz=self.imgsz, device=self.device, verbose=False)
        except Exception as exc:
            raise ModelNotAvailable(f"Could not load {self.weights.name}: {exc}") from exc

    @property
    def model(self):
        if self._model is None:  # pragma: no cover - defensive
            self._load()
        return self._model

    def update_runtime(self, confidence: float, iou: float, imgsz: int, device: str) -> None:
        changed_device = device != self.device
        self.confidence = confidence
        self.iou = iou
        self.imgsz = imgsz
        self.device = device
        if changed_device:
            log.info("Detector device switched to %s", device)

    def predict(self, frame: np.ndarray) -> list[Detection]:
        with self._lock:
            results = self.model.predict(
                frame,
                conf=self.confidence,
                iou=self.iou,
                imgsz=self.imgsz,
                device=self.device,
                verbose=False,
            )
        detections: list[Detection] = []
        if not results:
            return detections
        boxes = results[0].boxes
        if boxes is None:
            return detections
        names = self.names or {}
        for box in boxes:
            class_id = int(box.cls.item())
            name = names.get(class_id, str(class_id))
            if self.allowed_classes and name not in self.allowed_classes:
                continue
            x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
            detections.append(
                Detection(
                    xyxy=(x1, y1, x2, y2),
                    score=float(box.conf.item()),
                    class_id=class_id,
                    class_name=name,
                )
            )
        return detections

    def describe(self) -> dict:
        return {
            "model_id": self.model_id,
            "weights": self.weights.name,
            "device": self.device,
            "imgsz": self.imgsz,
            "confidence": self.confidence,
            "classes": sorted(self.names.values())[:20],
            "allowed_classes": sorted(self.allowed_classes) if self.allowed_classes else "all",
        }
