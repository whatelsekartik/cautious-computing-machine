"""Detection primitives shared by the built-in detector and custom models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

import numpy as np

# COCO classes we care about for civic monitoring.
VEHICLE_CLASSES: frozenset[str] = frozenset({"car", "motorcycle", "bus", "truck", "bicycle"})
PERSON_CLASSES: frozenset[str] = frozenset({"person"})
# Wide vehicles that physically block a footpath or carriageway.
BULKY_CLASSES: frozenset[str] = frozenset({"car", "bus", "truck", "motorcycle"})

VEHICLE_LABELS = {
    "car": "Car",
    "motorcycle": "Motorcycle",
    "bus": "Bus",
    "truck": "Truck",
    "bicycle": "Bicycle",
    "person": "Pedestrian",
}

CATEGORY_LABELS = {
    "illegal_parking": "Potential illegal parking",
    "double_parking": "Potential double parking",
    "footpath_obstruction": "Potential footpath obstruction",
    "restricted_entry": "Potential restricted-zone entry",
    "pothole": "Potential pothole",
    "garbage": "Potential garbage accumulation",
}


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
