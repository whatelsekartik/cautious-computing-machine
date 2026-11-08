"""Geometry primitives shared by the zone editor, rules engine and tests.

Polygons are stored as normalised coordinates (0..1) so a zone drawn on one
resolution keeps working when the same camera is processed at another size.
All boxes are ``(x1, y1, x2, y2)`` in pixels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

Box = tuple[float, float, float, float]
Point = tuple[float, float]


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def normalise_polygon(points: Sequence[Sequence[float]]) -> list[list[float]]:
    cleaned: list[list[float]] = []
    for point in points:
        if len(point) != 2:
            raise ValueError("Each polygon vertex needs exactly two numbers")
        x = clamp(float(point[0]), 0.0, 1.0)
        y = clamp(float(point[1]), 0.0, 1.0)
        if cleaned and abs(cleaned[-1][0] - x) < 1e-6 and abs(cleaned[-1][1] - y) < 1e-6:
            continue
        cleaned.append([round(x, 6), round(y, 6)])
    if len(cleaned) > 1 and cleaned[0] == cleaned[-1]:
        cleaned.pop()
    return cleaned


def polygon_area(points: Sequence[Sequence[float]]) -> float:
    """Shoelace area in normalised units."""
    if len(points) < 3:
        return 0.0
    total = 0.0
    for i, (x1, y1) in enumerate(points):
        x2, y2 = points[(i + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def validate_polygon(points: Sequence[Sequence[float]]) -> list[list[float]]:
    polygon = normalise_polygon(points)
    if len(polygon) < 3:
        raise ValueError("A zone needs at least three vertices")
    if polygon_area(polygon) < 0.0005:
        raise ValueError("Zone is too small to be useful")
    return polygon


def point_in_polygon(point: Point, polygon: Sequence[Sequence[float]]) -> bool:
    """Ray casting test for a normalised point and polygon."""
    x, y = point
    inside = False
    n = len(polygon)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
        ):
            inside = not inside
        j = i
    return inside


def box_anchor(box: Box) -> Point:
    """Bottom-centre of a detection: where a vehicle touches the ground."""
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2.0, y2)


def box_center(box: Box) -> Point:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def box_area(box: Box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def box_iou(a: Box, b: Box) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = box_area(a) + box_area(b) - inter
    return inter / union if union > 0 else 0.0


def iou_matrix(boxes_a: Sequence[Box], boxes_b: Sequence[Box]) -> np.ndarray:
    """Vectorised IoU matrix (N x M)."""
    if not len(boxes_a) or not len(boxes_b):
        return np.zeros((len(boxes_a), len(boxes_b)), dtype=np.float32)
    a = np.asarray(boxes_a, dtype=np.float32)
    b = np.asarray(boxes_b, dtype=np.float32)
    ix1 = np.maximum(a[:, None, 0], b[None, :, 0])
    iy1 = np.maximum(a[:, None, 1], b[None, :, 1])
    ix2 = np.minimum(a[:, None, 2], b[None, :, 2])
    iy2 = np.minimum(a[:, None, 3], b[None, :, 3])
    iw = np.clip(ix2 - ix1, 0, None)
    ih = np.clip(iy2 - iy1, 0, None)
    inter = iw * ih
    area_a = np.clip(a[:, 2] - a[:, 0], 0, None) * np.clip(a[:, 3] - a[:, 1], 0, None)
    area_b = np.clip(b[:, 2] - b[:, 0], 0, None) * np.clip(b[:, 3] - b[:, 1], 0, None)
    union = area_a[:, None] + area_b[None, :] - inter
    with np.errstate(divide="ignore", invalid="ignore"):
        iou = np.where(union > 0, inter / union, 0.0)
    return iou.astype(np.float32)


def box_scale(box: Box) -> float:
    """Diagonal used to make movement thresholds size independent."""
    return math.hypot(box[2] - box[0], box[3] - box[1])


@dataclass
class ZoneMask:
    """Rasterised zone used to measure how much of a detection sits inside it."""

    width: int
    height: int
    mask: np.ndarray

    @classmethod
    def build(cls, polygon: Sequence[Sequence[float]], width: int = 192, height: int = 108) -> "ZoneMask":
        import cv2

        mask = np.zeros((height, width), dtype=np.uint8)
        pts = np.array(
            [[int(round(x * (width - 1))), int(round(y * (height - 1)))] for x, y in polygon],
            dtype=np.int32,
        )
        if len(pts) >= 3:
            cv2.fillPoly(mask, [pts], 1)
        return cls(width=width, height=height, mask=mask)

    def coverage(self, box: Box, frame_width: int, frame_height: int) -> float:
        """Fraction of the detection box that falls inside the zone."""
        if frame_width <= 0 or frame_height <= 0:
            return 0.0
        x1 = int(box[0] / frame_width * self.width)
        y1 = int(box[1] / frame_height * self.height)
        x2 = int(math.ceil(box[2] / frame_width * self.width))
        y2 = int(math.ceil(box[3] / frame_height * self.height))
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(self.width, x2), min(self.height, y2)
        if x2 <= x1 or y2 <= y1:
            return 0.0
        region = self.mask[y1:y2, x1:x2]
        return float(region.mean()) if region.size else 0.0


def polygon_centroid(points: Iterable[Sequence[float]]) -> Point:
    pts = list(points)
    if not pts:
        return (0.5, 0.5)
    return (
        sum(p[0] for p in pts) / len(pts),
        sum(p[1] for p in pts) / len(pts),
    )


def clip_box(box: Box, width: int, height: int) -> Box:
    x1 = clamp(box[0], 0, width - 1)
    y1 = clamp(box[1], 0, height - 1)
    x2 = clamp(box[2], 0, width - 1)
    y2 = clamp(box[3], 0, height - 1)
    return (x1, y1, x2, y2)
