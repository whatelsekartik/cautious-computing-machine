"""Detection primitives shared by the built-in detector and custom models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

import numpy as np

# Labels and class sets are defined once in ``civicwatch.labels`` so the cloud
# API can import them without pulling in NumPy. They are re-exported here for
# the inference modules that have always imported them from this module.
from ..labels import (  # noqa: E402  (kept below the numpy import on purpose)
    BULKY_CLASSES,
    CATEGORY_LABELS,
    PERSON_CLASSES,
    VEHICLE_CLASSES,
    VEHICLE_LABELS,
)

__all__ = [
    "BULKY_CLASSES",
    "CATEGORY_LABELS",
    "PERSON_CLASSES",
    "VEHICLE_CLASSES",
    "VEHICLE_LABELS",
    "Detection",
    "Detector",
]


@dataclass(frozen=True)
class Detection:
    xyxy: tuple[float, float, float, float]
    score: float
    class_id: int
    class_name: str

    @property
    def width(self) -> float:
        return self.xyxy[2] - self.xyxy[0]

    @property
    def height(self) -> float:
        return self.xyxy[3] - self.xyxy[1]


class Detector(Protocol):
    """Anything that can turn a BGR frame into detections can be plugged in."""

    model_id: str
    names: dict[int, str]

    def predict(self, frame: np.ndarray) -> list[Detection]:  # pragma: no cover - protocol
        ...

    def describe(self) -> dict:  # pragma: no cover - protocol
        ...


def filter_detections(
    detections: Sequence[Detection], allowed: set[str] | frozenset[str] | None
) -> list[Detection]:
    if not allowed:
        return list(detections)
    return [d for d in detections if d.class_name in allowed]
